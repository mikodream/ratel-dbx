// DBX 原生 Sidecar：stdio-jsonl JSON-RPC 2.0
// 职责：与 DBX Host 通信（请求/响应/事件通知），把 ratel 连接管理转发给前端。
// 协议帧解析复用 github.com/ratel-online/core（与官方 client 锁定版本一致）。
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"

	coremodel "github.com/ratel-online/core/model"
)

// ---- DBX JSON-RPC 基本结构 ----

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      interface{}     `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type rpcResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      interface{} `json:"id"`
	Result  interface{} `json:"result,omitempty"`
	Error   *rpcError   `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type rpcNotification struct {
	JSONRPC string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

// Event 是发给前端 onEvent 的结构：method + payload。
type Event struct {
	Method  string
	Payload any
}

// server 维护 sidecar 生命周期、连接句柄与 stdout 写锁（stdio-jsonl 必须串行写）。
type server struct {
	outMu  sync.Mutex
	events chan Event // 发给前端的事件队列（消费循环 flush 到 stdout）

	// 连接状态（ratelClient 管理）
	mu            sync.Mutex
	client        *ratelClient
	connected     bool
	roomListCache []coremodel.Room

	lastRuntime runtime
	lastExt     extConf
}

func newServer() *server {
	return &server{
		events: make(chan Event, 64),
	}
}

// ---- stdout / stderr ----

func (s *server) logf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "[ratel-dbx] "+format+"\n", args...)
}

func (s *server) writeEventLine(method string, payload any) {
	n := rpcNotification{JSONRPC: "2.0", Method: method}
	raw, err := json.Marshal(payload)
	if err != nil {
		s.logf("encode event %s: %v", method, err)
		return
	}
	n.Params = raw
	s.writeLine(n)
}

func (s *server) writeLine(v any) {
	b, _ := json.Marshal(v)
	s.outMu.Lock()
	defer s.outMu.Unlock()
	os.Stdout.Write(b)
	os.Stdout.Write([]byte("\n"))
}

// eventPump 单独消费事件队列，避免阻塞 request 处理。
func (s *server) eventPump() {
	for ev := range s.events {
		s.writeEventLine(ev.Method, ev.Payload)
	}
}

// run 是侧车入口。
func (s *server) run() {
	go s.eventPump()

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Text()
		if strings.TrimSpace(line) == "" {
			continue
		}
		var req rpcRequest
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			s.logf("bad rpc line: %v", err)
			continue
		}
		if req.ID == nil {
			s.handleNotify(req)
			continue
		}
		if resp := s.dispatch(req); resp != nil {
			s.writeLine(resp)
		}
	}
	// stdin EOF（DBX 关闭插件）：停连接、关闭队列，优雅退出
	s.teardown()
}

// teardown 在进程退出时调用：断开 ratel、关闭事件队列。
func (s *server) teardown() {
	s.mu.Lock()
	client := s.client
	s.mu.Unlock()
	if client != nil {
		client.close()
	}
	close(s.events)
}

func (s *server) dispatch(req rpcRequest) *rpcResponse {
	switch req.Method {
	case "plugin/initialize":
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
			"protocolVersion": 1,
			// DBX 运行时按 Vec<String> 反序列化，必须是 JSON 数组（sequence），不能用空对象 {}
			"capabilities": []string{},
			"plugin": map[string]any{
				"id":      "com.dbx.ratel",
				"version": pluginVersion,
			},
		}}

	case "connection/test":
		var p connParams
		json.Unmarshal(req.Params, &p)
		s.mu.Lock()
		s.lastRuntime, s.lastExt = p.Runtime, p.ExternalConfig
		s.mu.Unlock()
		ok, msg := s.testConnection(p)
		if !ok {
			return errResp(req.ID, -32000, msg)
		}
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"success": true, "message": msg}}

	case "connection/connect":
		var p connParams
		json.Unmarshal(req.Params, &p)
		s.mu.Lock()
		s.lastRuntime, s.lastExt = p.Runtime, p.ExternalConfig
		s.mu.Unlock()
		if err := s.connect(p); err != nil {
			return errResp(req.ID, -32001, err.Error())
		}
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"success": true}}

	case "connection/disconnect":
		s.disconnect()
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"success": true}}

	case "connection/action":
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"success": true}}

	// ---- 前端自定义 RPC ----
	case "ratel/sendCommand":
		var p struct {
			Command string `json:"command"`
		}
		json.Unmarshal(req.Params, &p)
		if err := s.sendRatelCommand(p.Command); err != nil {
			return errResp(req.ID, -32003, err.Error())
		}
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"ok": true}}

	case "ratel/rooms":
		rooms := s.roomListSnapshot()
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"rooms": rooms}}

	case "ratel/reconnect":
		if err := s.reconnect(); err != nil {
			return errResp(req.ID, -32004, err.Error())
		}
		return &rpcResponse{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"ok": true}}

	default:
		return errResp(req.ID, -32601, "method not found: "+req.Method)
	}
}

func (s *server) handleNotify(req rpcRequest) {
	s.logf("notify from host: %s", req.Method)
}

func errResp(id interface{}, code int, msg string) *rpcResponse {
	return &rpcResponse{JSONRPC: "2.0", ID: id, Error: &rpcError{Code: code, Message: msg}}
}

// ---- 连接参数 ----

type connParams struct {
	Provider       string  `json:"provider"`
	Connection     string  `json:"connection"`
	Runtime        runtime `json:"runtime"`
	ExternalConfig extConf `json:"external_config"`
	Secrets        secrets `json:"secrets"`
}

type runtime struct {
	Host string `json:"host"`
	Port int    `json:"port"`
}

type extConf struct {
	Protocol string `json:"protocol"`
	Nickname string `json:"nickname"`
}

type secrets struct{}
