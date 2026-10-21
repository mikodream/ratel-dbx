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
├── .dbx-store.json      # DBX 官方商店元数据（名称/标签/许可证，非运行时 Manifest）
├── dbx-plugin.toml      # 打包配置
├── build.sh             # 5 个 target 交叉编译 sidecar（本地开发用）
├── scripts/
│   ├── package.sh       # 按 target 打未签名候选包（发布用）
│   ├── validate_manifest.py  # 离线校验 manifest 约束
│   └── check_candidates.py   # 校验候选包并汇总 release-candidates.json
├── .github/workflows/release.yml  # 发 Release 自动构建 5 平台候选包
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
├── bin/<target>/        # 构建产物（不提交）
└── dist/                # .dbxp 候选包（不提交）
```

## 构建与打包

```bash
# 需 Go 1.22+
bash build.sh                 # 构建全部 5 个 target 到 bin/<target>/
bash build.sh windows         # 只构建 windows-x64

# 打发布候选包（会先交叉编译，再按 target 改写 executable，最后打包）
bash scripts/package.sh windows-x64
bash scripts/package.sh linux-x64
```

> **target 命名必须用 `{os}-{x64|arm64}`**，对齐 DBX 运行时的 `current_plugin_target()`。
> 写成 `linux-amd64` 不是合法 target，商店也不认。

## 提交到 DBX 官方商店

1. **递增版本**：改 `manifest.json` 的 `version` 和 `backend/main.go` 的 `defaultPluginVersion`。
2. **发 Release**：打 tag `v<version>` 并发布 GitHub Release，`.github/workflows/release.yml`
   会自动在 5 个平台的 runner 上构建候选包，并上传：
   - `com.dbx.ratel-<version>-<target>.dbxp`（**未签名**）
   - 对应的 `.artifact.json`
   - `release-candidates.json`（汇总）
3. **等待同步**：`t8y2/dbx-store` 的同步 Workflow 读取 `release-candidates.json`，
   自动创建 `candidates/com.dbx.ratel.json` 候选 PR。
4. **商店审核签名**：维护者审核后用 DBX Store 仓库密钥签名，回写 `plugins/` 与 `catalog/`，再合并 PR。

**不要自己签名，也不要填写 `signingKeyId`** —— 官方商店插件的签名由 DBX Store 统一完成。
`.dbx-store.json` 只在首次上架或修改商店文案（名称/标签/许可证）时才需要改。

## 安装到 DBX

1. **开发期**：`bash scripts/package.sh <target>` 出包，插件中心开「允许安装未签名开发包」后直接装。
2. **正式**：从 DBX 插件商店安装（走官方签名流程）。
3. **使用**：在 DBX 里点「Ratel 摸鱼台」工作台，填服务器地址（默认 `192.252.182.94:9998`）、
   昵称，点「连接」。摸鱼命令条里点牌型/动作即可发指令，房间和流水自动刷新。

> 打包器**不会**改写 `manifest.entrypoints.backend.executable`：它固定把二进制放到
> `bin/<target>/` 下，但 manifest 里的路径是字面解析的，所以 `scripts/package.sh` 会在打包前
> 把该字段改成对应 target 的真实路径，打完再还原。

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
