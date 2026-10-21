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
#   3. plugin-cli 拒绝在非目标主机上打原生包
#      ("does not match build host")，因此 CI 里必须用对应 OS 的 runner。
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
if [ -x "$ROOT/scripts/validate_manifest.py" ] || [ -f "$ROOT/scripts/validate_manifest.py" ]; then
  python "$ROOT/scripts/validate_manifest.py" manifest.json
fi

# 4) 打包；--artifact-url 让 artifact.json 里记录最终 Release 下载地址
REPO="${GITHUB_REPOSITORY:-mikodream/ratel-dbx}"
TAG="${RELEASE_TAG:-v${VERSION}}"
PKG_NAME="com.dbx.ratel-${VERSION}-${TARGET}.dbxp"
ARTIFACT_URL="https://github.com/${REPO}/releases/download/${TAG}/${PKG_NAME}"

echo "==> packaging"
# CI 里是全局安装的 dbx-plugin；本地可能只有 npx 可用
if command -v dbx-plugin >/dev/null 2>&1; then
  dbx-plugin package . --target "$TARGET" --artifact-url "$ARTIFACT_URL"
else
  npx --yes "@dbx-app/plugin-cli@${PLUGIN_CLI_VERSION:-0.1.9}" package . \
    --target "$TARGET" --artifact-url "$ARTIFACT_URL"
fi

echo "==> done"
ls -la dist/*"${TARGET}"* 2>/dev/null || true
