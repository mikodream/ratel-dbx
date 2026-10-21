// DBX 插件 Sidecar 入口。
// 构建：
//   go build -o ../bin/ratel-dbx .
// 产物为单二进制，DBX 按 manifest 的 entrypoints.backend.executable 启动。
package main

import (
	"os"
)

// pluginVersion 由构建脚本通过 -ldflags 注入，与 manifest.json 的 version 保持一致：
//
//	go build -ldflags "-X main.pluginVersion=0.1.1" .
//
// 未注入时回落到 defaultPluginVersion，仅供本地裸跑使用。
var pluginVersion = defaultPluginVersion

const defaultPluginVersion = "0.1.3"

func main() {
	srv := newServer()
	srv.logf("ratel-dbx sidecar starting (pid=%d, data_dir=%s)",
		os.Getpid(), os.Getenv("DBX_PLUGIN_DATA_DIR"))
	srv.run()
}
