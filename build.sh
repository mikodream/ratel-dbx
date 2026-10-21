# build.sh — 构建 DBX 插件 sidecar 二进制（本地开发用）
#
# 用法：bash build.sh            # 构建全部 5 个 target
#       bash build.sh windows    # 只构建 windows-x64
#
# 注意：这只产出二进制，不打 .dbxp 包。
# 要做发布候选包，请用 scripts/package.sh <target> —— 它会按 target 改写
# manifest 的 executable 再打包（plugin-cli 不会自动改写，这点很关键）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"

VERSION="$(python -c 'import json;print(json.load(open("../manifest.json",encoding="utf-8"))["version"])')"

# target 命名严格对齐 DBX 的 current_plugin_target(): {os}-{x64|arm64}
build() {
  local target="$1" goos="$2" goarch="$3"
  local name="ratel-dbx"
  [ "$goos" = "windows" ] && name="ratel-dbx.exe"
  local out="../bin/${target}/${name}"
  echo "==> $target ($goos/$goarch)"
  mkdir -p "../bin/${target}"
  CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
    go build -trimpath -ldflags "-s -w -X main.pluginVersion=${VERSION}" -o "$out" .
}

case "${1:-all}" in
  windows)  build windows-x64   windows amd64 ;;
  linux)    build linux-x64     linux   amd64
            build linux-arm64   linux   arm64 ;;
  darwin)   build darwin-x64    darwin  amd64
            build darwin-arm64  darwin  arm64 ;;
  all)
    build windows-x64   windows amd64
    build linux-x64     linux   amd64
    build linux-arm64   linux   arm64
    build darwin-x64    darwin  amd64
    build darwin-arm64  darwin  arm64
    ;;
  *)
    echo "usage: $0 [all|windows|linux|darwin]" >&2
    exit 1
    ;;
esac

echo "done (version $VERSION). 产物在 bin/<target>/"
find "$ROOT/bin" -type f -exec ls -la {} \;
