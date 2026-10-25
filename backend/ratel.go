// ratel 连接层：复用 github.com/ratel-online/core 的协议帧（4字节长度前缀 + JSON 信封）。
// 负责：TCP/WS 连接、AuthInfo 登录、收消息解析、把结构化事件转发给 DBX 前端。
package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	"github.com/ratel-online/core/consts"
	coremodel "github.com/ratel-online/core/model"
	"github.com/ratel-online/core/network"
	"github.com/ratel-online/core/protocol"
)

// ratelClient 持有一条 ratel 长连接。
type ratelClient struct {
	s    *server
	conn *network.Conn

	// 当前登录身份（用于断线重连）
	host     string
	port     int
	proto    string // "ws" | "tcp"
	nickname string

	// 收消息循环退出信号
	done      atomic.Bool
	closeOnce sync.Once
}

// connectParams 汇总 connect 时所有参数。
func (s *server) connectParams(p connParams) (host, proto, nick string, port int) {
	host = p.Runtime.Host
	if host == "" {
		host = "ratel.isnico.com"
	}
	port = p.Runtime.Port
	if port == 0 {
		port = 9998
	}
	proto = p.ExternalConfig.Protocol
	if proto == "" {
		proto = "ws"
	}
	nick = p.ExternalConfig.Nickname
	if nick == "" {
		nick = "dbx"
	}
	return
}

// testConnection 只做 TCP 可达性 + WS 握手探测，不真正登录。
func (s *server) testConnection(p connParams) (bool, string) {
	host, proto, _, port := s.connectParams(p)
	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	if proto == "ws" {
		u := url.URL{Scheme: "ws", Host: addr, Path: "/ws"}
		c, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
		if err != nil {
			return false, "ws 连接失败: " + err.Error()
		}
		c.Close()
		return true, "ws 握手成功"
	}
	conn, err := net.DialTimeout("tcp", addr, 5*time.Second)
	if err != nil {
		return false, "tcp 连接失败: " + err.Error()
	}
	conn.Close()
	return true, "tcp 可达"
}

// connect 建立真实连接 + 登录 + 启动收消息循环。
func (s *server) connect(p connParams) error {
	host, proto, nick, port := s.connectParams(p)

	var rwc protocol.ReadWriteCloser
	if proto == "ws" {
		u := url.URL{Scheme: "ws", Host: net.JoinHostPort(host, fmt.Sprintf("%d", port)), Path: "/ws"}
		c, _, err := websocket.DefaultDialer.Dial(u.String(), nil)
		if err != nil {
			return fmt.Errorf("ws 连接失败: %w", err)
		}
		rwc = protocol.NewWebsocketReadWriteCloser(c)
	} else {
		tcpConn, err := net.DialTimeout("tcp", net.JoinHostPort(host, fmt.Sprintf("%d", port)), 5*time.Second)
		if err != nil {
			return fmt.Errorf("tcp 连接失败: %w", err)
		}
		rwc = protocol.NewTcpReadWriteCloser(tcpConn)
	}

	conn := network.Wrapper(rwc)
	client := &ratelClient{s: s, conn: conn, host: host, port: port, proto: proto, nickname: nick}

	// 发送登录包 AuthInfo{ID,Name,Score}
	uid := time.Now().UnixNano()
	if err := conn.Write(protocol.ObjectPacket(coremodel.AuthInfo{
		ID:    uid,
		Name:  nick,
		Score: 100,
	})); err != nil {
		conn.Close()
		return fmt.Errorf("登录包发送失败: %w", err)
	}

	// 收消息循环在独立 goroutine
	go client.listenLoop()

	s.mu.Lock()
	s.client = client
	s.connected = true
	s.mu.Unlock()

	s.writeEventLine("ratel/status", map[string]any{"connected": true, "host": host, "port": port, "protocol": proto, "nickname": nick})
	return nil
}

// listenLoop 读 ratel 推来的包，解析成前端事件。
func (c *ratelClient) listenLoop() {
	for {
		if c.done.Load() {
			return
		}
		packet, err := c.conn.Read()
		if err != nil {
			if !c.done.Load() {
				c.s.writeEventLine("ratel/status", map[string]any{"connected": false, "reason": "connection closed: " + err.Error()})
				c.s.mu.Lock()
				c.s.connected = false
				c.s.mu.Unlock()
			}
			return
		}
		c.handlePacket(*packet)
	}
}

// handlePacket 把一个 ratel 包解析成结构化事件。
// ratel server 推来的 Body 多为 JSON 文本，含 code 字段（1001=welcome, 1002=home, 1003=roomlist...）。
func (c *ratelClient) handlePacket(packet protocol.Packet) {
	body := string(packet.Body)

	// 终端信号：服务端控制交互提示符开/关
	switch body {
	case consts.IsStart:
		c.s.writeEventLine("ratel/prompt", map[string]any{"interactive": true})
		return
	case consts.IsStop:
		c.s.writeEventLine("ratel/prompt", map[string]any{"interactive": false})
		return
	}

	// 尝试解析成带 code 的结构化消息
	var msg struct {
		Code int    `json:"code"`
		Msg  string `json:"msg"`
	}
	if err := json.Unmarshal([]byte(body), &msg); err == nil && msg.Code != 0 {
		switch msg.Code {
		case consts.CodeRoomList:
			var rl struct {
				Rooms []coremodel.Room `json:"rooms"`
			}
			if json.Unmarshal([]byte(body), &rl) == nil {
				c.s.mu.Lock()
				c.s.roomListCache = rl.Rooms
				c.s.mu.Unlock()
				c.s.writeEventLine("ratel/rooms", map[string]any{"rooms": rl.Rooms})
				return
			}
		}
	}

	// 其它：作为原始流水转发（前端展示为终端文本）
	c.s.writeEventLine("ratel/raw", map[string]any{"text": body})
}

// sendRatelCommand 把前端命令发到 ratel server（Packet{Body: 命令字符串}）。
func (s *server) sendRatelCommand(cmd string) error {
	s.mu.Lock()
	client := s.client
	connected := s.connected
	s.mu.Unlock()
	if !connected || client == nil {
		return fmt.Errorf("尚未连接 ratel server，请先连接")
	}
	if err := client.conn.Write(protocol.StringPacket(cmd)); err != nil {
		return fmt.Errorf("命令发送失败: %w", err)
	}
	// 命令回显流水（前端可选显示）
	s.writeEventLine("ratel/raw", map[string]any{"text": cmd, "sent": true})
	return nil
}

// roomListSnapshot 返回最近一次服务端推来的房间列表。
func (s *server) roomListSnapshot() []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]map[string]any, 0)
	for _, r := range s.roomListCache {
		out = append(out, map[string]any{
			"id": r.ID, "name": r.Name, "type": r.Type, "typeDesc": r.TypeDesc,
			"players": r.Players, "state": r.State, "stateDesc": r.StateDesc,
		})
	}
	return out
}

// reconnect 用最近一次参数重连。
func (s *server) reconnect() error {
	s.mu.Lock()
	p := connParams{Runtime: s.lastRuntime, ExternalConfig: s.lastExt}
	s.mu.Unlock()
	s.disconnect()
	return s.connect(p)
}

// disconnect 断开当前连接（幂等）。
func (s *server) disconnect() {
	s.mu.Lock()
	client := s.client
	s.connected = false
	s.client = nil
	s.mu.Unlock()
	if client != nil {
		client.close()
	}
	s.writeEventLine("ratel/status", map[string]any{"connected": false, "reason": "disconnected by user"})
}

// close 关闭一条 ratelClient（幂等）。
func (c *ratelClient) close() {
	c.closeOnce.Do(func() {
		c.done.Store(true)
		if c.conn != nil {
			c.conn.Close()
		}
	})
}

// roomListCache 是最近一次房间列表（由 handlePacket 更新，放在 server 上避免 client 替换时丢失）

func init() {
	_ = sync.Mutex{}
}
