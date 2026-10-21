#!/usr/bin/env python3
"""校验 dist/ 下的候选包并汇总出 release-candidates.json。

只做校验与汇总，不签名、不联网。格式对齐：
  - t8y2/dbx-store 的 schemas/plugin-candidate.schema.json
  - 官方 release pipeline 上传的 release-candidates.json

用法：
  python scripts/check_candidates.py dist \
      --expect windows-x64,linux-x64,linux-arm64,darwin-x64,darwin-arm64 \
      --write-release-candidates dist/release-candidates.json
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from assert_binary_arch import DETECTORS  # noqa: E402  同目录下的架构探测器

TARGET_RE = re.compile(r"^[a-z0-9-]{1,64}$")
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
# 各 target 包之间必须一致的 manifest 身份字段
IDENTITY_FIELDS = ("id", "name", "description", "publisher", "version")

# target -> (期望的二进制格式, 期望的 CPU 架构)
# 迁移到这里是为了让"包名说 x64、内容却是 arm64"这类静默错架构的包
# 无法通过发布前的汇总校验。
EXPECTED_BINARY = {
    "windows-x64": ("pe", "x86_64"),
    "windows-arm64": ("pe", "arm64"),
    "linux-x64": ("elf", "x86_64"),
    "linux-arm64": ("elf", "arm64"),
    "darwin-x64": ("macho", "x86_64"),
    "darwin-arm64": ("macho", "arm64"),
}


def read_manifest(package: pathlib.Path) -> dict:
    with zipfile.ZipFile(package) as archive:
        return json.loads(archive.read("manifest.json"))


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", nargs="?", default="dist")
    parser.add_argument("--expect", default="", help="必须全部出现的 target，逗号分隔")
    parser.add_argument("--write-release-candidates", default="")
    args = parser.parse_args()

    root = pathlib.Path(args.directory)
    errors: list[str] = []

    packages = sorted(root.rglob("*.dbxp"))
    if not packages:
        print(f"FAIL no .dbxp files under {root}", file=sys.stderr)
        return 1
    metadata_paths = sorted(root.rglob("*.artifact.json"))
    if not metadata_paths:
        print(f"FAIL no .artifact.json files under {root}", file=sys.stderr)
        return 1

    identities: dict[str, list[str]] = {}
    seen_targets: dict[str, str] = {}
    artifacts: list[dict] = []

    for metadata_path in metadata_paths:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            errors.append(f"unreadable metadata {metadata_path}: {error}")
            continue

        target = str(metadata.get("target") or "")
        if not TARGET_RE.fullmatch(target):
            errors.append(f"{metadata_path}: invalid artifact target {target!r}")
            continue
        if target in seen_targets:
            errors.append(
                f"duplicate target {target!r} in {seen_targets[target]} and {metadata_path.name}"
            )
            continue
        seen_targets[target] = metadata_path.name

        pkg_name = str(metadata.get("url") or "")
        # artifact.json 的 url 是最终下载地址，包文件名是它的最后一段
        pkg_file = pathlib.PurePosixPath(pkg_name.replace("\\", "/")).name
        package = root / pkg_file
        if not package.is_file():
            errors.append(f"{metadata_path}: package {pkg_file} not found next to metadata")
            continue

        if package.name.endswith(".signed.dbxp"):
            errors.append(f"{package.name}: store candidates must be unsigned")
        if metadata.get("signingKeyId"):
            errors.append(f"{metadata_path}: store candidates must not declare signingKeyId")

        actual_sha = sha256_of(package)
        declared_sha = str(metadata.get("sha256") or "")
        if not SHA256_RE.fullmatch(declared_sha):
            errors.append(f"{metadata_path}: invalid sha256 {declared_sha!r}")
        elif actual_sha.lower() != declared_sha.lower():
            errors.append(
                f"{package.name}: sha256 mismatch (file {actual_sha}, metadata {declared_sha})"
            )
        if int(metadata.get("size") or 0) != package.stat().st_size:
            errors.append(
                f"{package.name}: size mismatch "
                f"(file {package.stat().st_size}, metadata {metadata.get('size')})"
            )

        try:
            manifest = read_manifest(package)
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            errors.append(f"{package.name}: cannot read manifest.json ({error})")
            continue

        identity = {field: manifest.get(field, "") for field in IDENTITY_FIELDS}
        identities.setdefault(json.dumps(identity, sort_keys=True, ensure_ascii=False), []).append(
            package.name
        )

        # 包内 executable 必须真实存在于包内，且落在该 target 的目录下
        backend = (manifest.get("entrypoints") or {}).get("backend") or {}
        executable = str(backend.get("executable") or "")
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
        if not executable:
            errors.append(f"{package.name}: manifest declares no backend executable")
        elif executable not in names:
            errors.append(
                f"{package.name}: executable {executable!r} is not inside the package"
            )
        elif f"bin/{target}/" not in executable:
            errors.append(
                f"{package.name}: executable {executable!r} does not live under bin/{target}/"
            )
        else:
            # 断言二进制真实架构：防止"名字是 x64、内容是 arm64"的坏包流向商店
            expectation = EXPECTED_BINARY.get(target)
            if expectation is None:
                errors.append(f"{package.name}: no known binary architecture for target {target!r}")
            else:
                expect_format, expect_arch = expectation
                with zipfile.ZipFile(package) as archive:
                    binary = archive.read(executable)
                try:
                    actual_format, actual_arch = DETECTORS[expect_format](binary)
                except ValueError as error:
                    errors.append(f"{package.name}: {executable}: {error}")
                else:
                    if actual_format != expect_format or actual_arch != expect_arch:
                        errors.append(
                            f"{package.name}: {executable} is {actual_format}/{actual_arch}, "
                            f"expected {expect_format}/{expect_arch} for target {target!r}"
                        )

        artifacts.append(
            {
                "target": target,
                "url": pkg_name,
                "sha256": actual_sha,
                "size": package.stat().st_size,
            }
        )

    if len(identities) > 1:
        for names in identities.values():
            errors.append(f"manifest identity differs across targets: {', '.join(names)}")

    expected = [t.strip() for t in args.expect.split(",") if t.strip()]
    missing = [t for t in expected if t not in seen_targets]
    if missing:
        errors.append(f"missing expected target(s): {', '.join(missing)}")

    if errors:
        for message in errors:
            print(f"FAIL {message}", file=sys.stderr)
        return 1

    if args.write_release_candidates:
        identity_key = next(iter(identities))
        payload = {
            "plugin": json.loads(identity_key),
            "artifacts": sorted(artifacts, key=lambda item: item["target"]),
        }
        output = pathlib.Path(args.write_release_candidates)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {output}")

    print(f"OK {len(artifacts)} candidate(s): {', '.join(sorted(seen_targets))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
