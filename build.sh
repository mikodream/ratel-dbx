# build.sh — 构建 DBX 插件 sidecar 三平台二进制
# 用法：bash build.sh          # 构建当前平台 + 另两平台
#       bash build.sh windows  # 只构建 windows
# 产物输出到 bin/，manifest 的 executable 字段会按 DBX 打包器约定同步

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/backend"

build() {
  local goos="$1" goarch="$2" out="$3"
  echo "==> building $goos/$goarch -> $out"
  CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" go build -trimpath -ldflags "-s -w" -o "$out" .
}

# 平台变体命名与 DBX 打包约定一致：
#  - windows 包内二进制为 ratel-dbx.exe
#  - linux / darwin 包内二进制为 ratel-dbx-<os>-<arch>
case "${1:-all}" in
  windows)
    build windows amd64 ../bin/ratel-dbx.exe
    ;;
  linux)
    build linux amd64 ../bin/ratel-dbx-linux-amd64
    build linux arm64 ../bin/ratel-dbx-linux-arm64
    ;;
  darwin)
    build darwin arm64 ../bin/ratel-dbx-darwin-arm64
    build darwin amd64 ../bin/ratel-dbx-darwin-amd64
    ;;
  all)
    build windows amd64 ../bin/ratel-dbx.exe
    build linux amd64 ../bin/ratel-dbx-linux-amd64
    build linux arm64 ../bin/ratel-dbx-linux-arm64
    build darwin arm64 ../bin/ratel-dbx-darwin-arm64
    build darwin amd64 ../bin/ratel-dbx-darwin-amd64
    ;;
  *)
    echo "usage: $0 [all|windows|linux|darwin]"
    exit 1
    ;;
esac

echo "done. 产物在 ../bin/"
ls -la ../bin/
