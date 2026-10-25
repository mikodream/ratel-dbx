#!/usr/bin/env bash
# package.sh — 为单个 DBX target 打出未签名候选包
#
# 用法：
#   bash scripts/package.sh windows-x64
#   bash scripts/package.sh linux-x64
#   bash scripts/package.sh linux-arm64
#   bash scripts/package.sh darwin-x64
#   bash scripts/package.sh darwin-arm64
#
# 关键事实（务必理解，否则包会静默错架构）：
#
#   1. DBX 的 target 命名是 `{os}-{x64|arm64}`，不是 amd64/arm64 混用。
#      见 dbx-plugin-runtime 的 current_plugin_target()。
#
#   2. 运行时按 manifest 的 entrypoints.backend.executable **字面路径**在包内找二进制，
#      没有任何平台占位符。而 plugin-cli 固定把二进制放到 bin/<target>/ 下，
#      **且不会改写 manifest**。所以打包前必须把 executable 改写成本 target 的真实
#      路径，打完后还原。
#
#   3. **dbx-plugin package 会自己 go build 一遍 Go sidecar**（帮助文本原文：
#      "Rust and Go projects build a native sidecar for the current host target"），
#      没有跳过开关。也就是说本脚本里预先交叉编译的二进制会被它覆盖。
#      因此跨架构打包不能靠"先交叉编译再打包"，只能让它内部的 go build 按目标架构编：
#      通过 GOOS/GOARCH 环境变量注入（已被 probe 实测验证）。
#      这也意味着本脚本不再自己 build，只负责改写 manifest 与断言。
#
#   4. plugin-cli 是平台原生的 Rust 二进制，会校验
#      "Native plugin target 'X' does not match build host 'Y'"。
#      跨架构时用 DBX_PLUGIN_CLI_BINARY 指定目标架构的原生 CLI，
#      再用 DBX_PLUGIN_CLI_WRAPPER（如 "arch -x86_64"）调起它，
#      使其自报的 build host 就是目标架构。
#
#   5. 打包完成后必须断言包内二进制的真实架构，否则会得到"名字是 x64、内容是 arm64"
#      的坏包——这种包能装能过校验，却只会在目标机器上运行时崩溃。
#
# 产物：dist/com.dbx.ratel-<version>-<target>.dbxp + .artifact.json
set -euo pipefail

TARGET="${1:-}"
case "$TARGET" in
  windows-x64)   GOOS=windows GOARCH=amd64 BIN_NAME="ratel-dbx.exe" EXPECT_ARCH="x86_64" PKG_FMT="pe"   ;;
  linux-x64)     GOOS=linux   GOARCH=amd64 BIN_NAME="ratel-dbx"     EXPECT_ARCH="x86_64" PKG_FMT="elf"  ;;
  linux-arm64)   GOOS=linux   GOARCH=arm64 BIN_NAME="ratel-dbx"     EXPECT_ARCH="arm64"  PKG_FMT="elf"  ;;
  darwin-x64)    GOOS=darwin  GOARCH=amd64 BIN_NAME="ratel-dbx"     EXPECT_ARCH="x86_64" PKG_FMT="macho" ;;
  darwin-arm64)  GOOS=darwin  GOARCH=arm64 BIN_NAME="ratel-dbx"     EXPECT_ARCH="arm64"  PKG_FMT="macho" ;;
  *)
    echo "usage: $0 {windows-x64|linux-x64|linux-arm64|darwin-x64|darwin-arm64}" >&2
    exit 1
    ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import json;print(json.load(open("manifest.json",encoding="utf-8"))["version"])')"
EXE_PATH="bin/${TARGET}/${BIN_NAME}"
MANIFEST_BAK="$(mktemp)"
cp manifest.json "$MANIFEST_BAK"

restore() { cp "$MANIFEST_BAK" manifest.json; rm -f "$MANIFEST_BAK"; }
trap restore EXIT

echo "==> target=$TARGET version=$VERSION executable=$EXE_PATH (${GOOS}/${GOARCH})"

# 1) 把 executable 指向本次 target 的真实位置
python3 - "$EXE_PATH" <<'PY'
import json, sys
path = sys.argv[1]
with open("manifest.json", encoding="utf-8") as fh:
    manifest = json.load(fh)
manifest["entrypoints"]["backend"]["executable"] = path
with open("manifest.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

# 2) 校验 manifest 结构（此阶段二进制还没生成，允许缺失）
if [ -f "$ROOT/scripts/validate_manifest.py" ]; then
  python3 "$ROOT/scripts/validate_manifest.py" manifest.json --allow-missing-binary
fi

# 3) 打包。CLI 会按下面的 GOOS/GOARCH 自行编译 sidecar。
REPO="${GITHUB_REPOSITORY:-mikodream/ratel-dbx}"
TAG="${RELEASE_TAG:-v${VERSION}}"
PKG_NAME="com.dbx.ratel-${VERSION}-${TARGET}.dbxp"
ARTIFACT_URL="https://github.com/${REPO}/releases/download/${TAG}/${PKG_NAME}"

echo "==> packaging"
WRAPPER="${DBX_PLUGIN_CLI_WRAPPER:-}"
if [ -n "${DBX_PLUGIN_CLI_BINARY:-}" ]; then
  CLI=("$DBX_PLUGIN_CLI_BINARY")
elif command -v dbx-plugin >/dev/null 2>&1; then
  CLI=(dbx-plugin)
else
  CLI=(npx --yes "@dbx-app/plugin-cli@${PLUGIN_CLI_VERSION:-0.1.9}")
fi

# 让 CLI 内部的 go build 按目标架构编译，而不是宿主架构
export GOOS GOARCH CGO_ENABLED=0
export GOFLAGS="${GOFLAGS:-} -trimpath"

# shellcheck disable=SC2086
$WRAPPER "${CLI[@]}" package . --target "$TARGET" --artifact-url "$ARTIFACT_URL"

# 4) 断言包内二进制的真实架构
echo "==> asserting packaged binary architecture"
python3 "$ROOT/scripts/assert_binary_arch.py" "dist/${PKG_NAME}" --expect-arch "$EXPECT_ARCH" --expect-format "$PKG_FMT"

echo "==> done"
ls -la dist/*"${TARGET}"* 2>/dev/null || true
