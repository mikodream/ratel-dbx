#!/usr/bin/env bash
# package.sh — 为单个 DBX target 打出未签名候选包
#
# 用法：
#   bash scripts/package.sh windows-x64      # 或在 CI 里按 matrix 调用
#   bash scripts/package.sh linux-x64
#   bash scripts/package.sh linux-arm64
#   bash scripts/package.sh darwin-x64
#   bash scripts/package.sh darwin-arm64
#
# 背景（务必理解，否则包会跑不起来）：
#   1. DBX 的 target 命名是 `{os}-{x64|arm64}`，**不是** amd64/arm64 混用。
#      见 dbx-plugin-runtime 的 current_plugin_target()。
#   2. 运行时按 manifest 的 entrypoints.backend.executable **字面路径**在包内找二进制，
#      没有任何平台占位符。而 plugin-cli 固定把二进制放到 bin/<target>/ 下，
#      **且不会改写 manifest**。所以每个 target 必须在打包前把 executable
#      改写成 bin/<target>/ratel-dbx[.exe]，打完后还原。
#   3. plugin-cli 内部是一个平台原生的 Rust 二进制，它会校验
#      "Native plugin target 'X' does not match build host 'Y'"，
#      因此通常必须在同架构的 runner 上打包。
#
# 跨架构打包（darwin-x64 on macos-14 arm64）：
#   plugin-cli 的 Node 包装层支持 DBX_PLUGIN_CLI_BINARY 覆盖二进制路径。
#   在 Apple Silicon 上装好 Rosetta 2 后，用 `arch -x86_64 <cli>` 调起
#   darwin-x64 版 CLI，它自报的 build host 就是 darwin-x64，校验即通过；
#   而 Go sidecar 那边用 GOARCH=amd64 交叉编译产出真正的 x86_64 二进制。
#   由 DBX_PLUGIN_CLI_WRAPPER 环境变量指定这层包装（见下）。
#
# 产物：dist/com.dbx.ratel-<version>-<target>.dbxp + .artifact.json
set -euo pipefail

TARGET="${1:-}"
case "$TARGET" in
  windows-x64)   GOOS=windows GOARCH=amd64 BIN_NAME="ratel-dbx.exe" ;;
  linux-x64)     GOOS=linux   GOARCH=amd64 BIN_NAME="ratel-dbx"     ;;
  linux-arm64)   GOOS=linux   GOARCH=arm64 BIN_NAME="ratel-dbx"     ;;
  darwin-x64)    GOOS=darwin  GOARCH=amd64 BIN_NAME="ratel-dbx"     ;;
  darwin-arm64)  GOOS=darwin  GOARCH=arm64 BIN_NAME="ratel-dbx"     ;;
  *)
    echo "usage: $0 {windows-x64|linux-x64|linux-arm64|darwin-x64|darwin-arm64}" >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python -c 'import json;print(json.load(open("manifest.json",encoding="utf-8"))["version"])')"
EXE_PATH="bin/${TARGET}/${BIN_NAME}"
MANIFEST_BAK="$(mktemp)"
cp manifest.json "$MANIFEST_BAK"

restore() { cp "$MANIFEST_BAK" manifest.json; rm -f "$MANIFEST_BAK"; }
trap restore EXIT

echo "==> target=$TARGET version=$VERSION executable=$EXE_PATH"

# 1) 把 executable 指向本次 target 的真实位置
python - "$EXE_PATH" <<'PY'
import json, sys
path = sys.argv[1]
with open("manifest.json", encoding="utf-8") as fh:
    manifest = json.load(fh)
manifest["entrypoints"]["backend"]["executable"] = path
with open("manifest.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

# 2) 交叉编译 sidecar（CGO 关掉才能纯静态/跨平台）
echo "==> building $GOOS/$GOARCH"
mkdir -p "bin/${TARGET}"
( cd backend && CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" \
    go build -trimpath -ldflags "-s -w -X main.pluginVersion=${VERSION}" \
    -o "../${EXE_PATH}" . )

# 3) 校验 manifest 能被 DBX 运行时接受（含二进制存在性检查）
if [ -f "$ROOT/scripts/validate_manifest.py" ]; then
  python "$ROOT/scripts/validate_manifest.py" manifest.json
fi

# 4) 打包；--artifact-url 让 artifact.json 里记录最终 Release 下载地址
REPO="${GITHUB_REPOSITORY:-mikodream/ratel-dbx}"
TAG="${RELEASE_TAG:-v${VERSION}}"
PKG_NAME="com.dbx.ratel-${VERSION}-${TARGET}.dbxp"
ARTIFACT_URL="https://github.com/${REPO}/releases/download/${TAG}/${PKG_NAME}"

echo "==> packaging"
# 两种调用方式：
#   a) 常规：dbx-plugin（已装好、架构与宿主一致的 CLI 包装器）
#   b) 跨架构：DBX_PLUGIN_CLI_BINARY 指向目标架构的原生二进制，
#      外层套 DBX_PLUGIN_CLI_WRAPPER（如 "arch -x86_64"）改变它自报的 build host。
#      注意必须直接调原生二进制，不能 arch -x86_64 去跑 node 包装层——
#      那样 Rosetta 跑的是 node 本身，CLI 仍会按宿主机架构判定。
WRAPPER="${DBX_PLUGIN_CLI_WRAPPER:-}"
if [ -n "${DBX_PLUGIN_CLI_BINARY:-}" ]; then
  CLI=("$DBX_PLUGIN_CLI_BINARY")
elif command -v dbx-plugin >/dev/null 2>&1; then
  CLI=(dbx-plugin)
else
  CLI=(npx --yes "@dbx-app/plugin-cli@${PLUGIN_CLI_VERSION:-0.1.9}")
fi

# shellcheck disable=SC2086
$WRAPPER "${CLI[@]}" package . --target "$TARGET" --artifact-url "$ARTIFACT_URL"

echo "==> done"
ls -la dist/*"${TARGET}"* 2>/dev/null || true
