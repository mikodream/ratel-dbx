# Ratel DBX 插件 🃏

把 [Ratel 在线棋牌 server](https://github.com/ratel-online/server) 接进 [DBX](https://dbxio.com/cn) 的摸鱼客户端插件。
在 DBX 里直接连服务器、看房间、发指令、玩斗地主 / 跑得快 / 德州 / 大众麻将 / 骗子酒馆 —— 摸鱼用，不耽误正事。

## 为什么是「前端 UI + Go Sidecar」

DBX 插件前端跑在 opaque-origin iframe 沙箱里，**默认没有任何网络访问能力**（要发网络请求得声明
`host.network` 权限，且**只认 https、最多 8 个 origin**）。而 ratel server 走的是
`ws://`/`tcp` 长连接 + 自定义二进制帧协议，沙箱前端根本发不了。

所以网络层交给**原生 Sidecar（Go 单二进制）**：
- 前端（`ui/`）：纯 HTML/CSS/JS，零 npm、零构建，和 DBX 原生前端同构。只干 UI——连接表单、
  摸鱼快捷命令条、房间列表、推送流水。
- 后端（`backend/`）：DBX 标准 `stdio-jsonl` JSON-RPC 2.0。真正开 ws 连 ratel、发登录包、
  收推送、转发命令。协议帧解析直接复用 [`ratel-online/core`](https://github.com/ratel-online/core)
  （和官方 client 锁定版本一致），和 ratel server 协议**零偏差**。

前端和 sidecar 通过 DBX 桥通信：`window.dbxPlugin.invoke/notify/onEvent`。

## 架构

```
DBX Host
┌────────────────────────────────────────────────────────┐
│  连接表单 (manifest 声明)  │  工作台 (沙箱 iframe ui/)  │
│  connection-provider       │  index.html + main.js      │
└──────────────┬─────────────┴───────────────┬───────────┘
               │ lifecycle RPC                │ invoke / notify / onEvent
               ▼                             ▼
┌────────────────────────────────────────────────────────┐
│  Go Sidecar (backend/, stdio-jsonl v1, 单二进制)        │
│  · plugin/initialize  · connection/test|connect|disconnect
│  · ratel/sendCommand  · ratel/rooms · ratel/reconnect
│  复用 ratel-online/core: 4字节长度帧 + JSON 信封 + AuthInfo
└──────────────┬─────────────────────────────────────────┘
               │ ws://host:9998/ws  或  tcp://host:9999
               ▼
        Ratel Server (Go, 纯内存)
```

## 协议要点（来自 ratel-online/core 锁定版本）

- **帧**：4 字节大端长度前缀 + JSON 包体。包体结构 `{"data": <payload>}`。
- **登录**：连接后第一包发 `AuthInfo{ID, Name, Score}`，服务端回欢迎/主界面。
- **命令**：发 `{"data":"v"}` 这种字符串命令（`v` 刷房间、`call`/`W3`/`sx` 等）。
- **收消息**：服务端推来的 Body 含 `code` 字段（`1003`=房间列表等），按 `IsStart/IsStop`
  切交互提示符，其余进流水。

## 目录结构

```
ratel-dbx/
├── manifest.json        # DBX 插件契约：id/权限/入口/贡献点
├── dbx-plugin.toml      # 打包配置
├── build.sh             # 三平台交叉编译 sidecar
├── assets/plugin.svg    # 扑克 A 图标
├── ui/                  # 沙箱前端（零构建）
│   ├── index.html
│   ├── main.js          # 摸鱼命令面板 + 房间 + 流水
│   └── style.css        # 明暗主题自适应
├── backend/             # Go sidecar 源码
│   ├── go.mod
│   ├── main.go          # 入口
│   ├── rpc.go           # stdio-jsonl JSON-RPC + 事件队列
│   └── ratel.go         # 复用 core 的 ws/tcp 连接 + 收发包
└── bin/                 # 构建产物（不提交）
```

## 构建

```bash
# 需 Go 1.18+
cd backend && go mod tidy
bash build.sh            # 全平台
# 或只构当前平台
cd backend && go build -o ../bin/ratel-dbx.exe .   # Windows
```

## 安装到 DBX

1. **打包**：用 `dbx-plugin package .`（[DBX 插件 CLI](https://dbxio.com/cn/docs/plugin-development)）
   生成各平台 `.dbxp`。打包器会按目标平台把 `manifest.entrypoints.backend.executable`
   改写成对应的 `bin/ratel-dbx(.exe)` 或 `bin/ratel-dbx-<os>-<arch>`。
2. **安装**：开发期可在插件中心开「允许安装未签名开发包」；正式发布走仓库签名 + `.dbxp`。
3. **使用**：在 DBX 里点「Ratel 摸鱼台」工作台，填服务器地址（默认 `192.252.182.94:9998`）、
   昵称，点「连接」。摸鱼命令条里点牌型/动作即可发指令，房间和流水自动刷新。

## 摸鱼命令速查

| 场景 | 命令 |
|---|---|
| 刷房间 | `v` |
| 开始 | `s` |
| 准备 | `ready` |
| 斗地主/跑得快出牌 | `0`(单10) `22`(对2) `sx`(王炸) `3334`(三带一) `jjjqqq34`(飞机) `p`(不出) |
| 德州 | `call` `raise` `allin` `fold` `check` |
| 麻将 | `W3`/`T7`/`O5`(万条筒) `D`(东) `P`(碰) `G`(杠) `H`(胡) `pass` |
| 骗子酒馆 | `k`/`q`/`a`/`s`(出牌) `c`(质疑) |
| 房间设置 | `set ds on`(不洗牌) `set ct on`(聊天) `set sk on`(技能) `set lz on`(癞子) `set pwd xxxx`(密码) |

## 注意

- 这是**摸鱼工具**，连接的是你自己的 / 内网 ratel server。默认 `192.252.182.94:9998` 来自
  ratel 官方 README，实际部署请按你的 server 地址改。
- Sidecar 是 Go 单二进制，运行零依赖；开发期会 `go mod` 拉 `ratel-online/core` 和
  `gorilla/websocket`，构建后这些被静态编译进二进制。
