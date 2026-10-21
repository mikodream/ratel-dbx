#!/usr/bin/env python3
"""离线校验 manifest.json 的关键约束（发布前把关）。

不依赖 DBX 二进制或 Rust 工具链，纯 stdlib。覆盖踩过的坑：
  - 贡献类型标签（command 是单数，没有 commands）
  - menus 的 group 必须在宿主白名单里
  - commandPalette 上禁止 default_visible
  - command.key / menu.location 等字段拼写
  - backend.executable 必须真实存在
  - 图标文件存在
  - permission 白名单
目的不是替代 DBX 的 Rust 校验，而是让 CI 在没有 DBX 的机器上也能尽早失败。
"""

import json
import pathlib
import sys

# dbx-plugin-runtime/src/plugins/manifest.rs
CONTRIBUTION_TYPES = {
    "connection-provider",
    "workbench",
    "filesystem-provider",
    "context-menu",
    "result-view",
    "command",
    "menus",
    "mcp",
}
MENU_GROUPS = {"navigation", "primary", "secondary", "destructive"}
MENU_LOCATIONS = {"commandPalette", "appToolbar", "appSidebar"}
COMMAND_ACTION_TYPES = {"open-workbench"}
PRESENTATIONS = {"tab", "panel"}
REUSE = {"singleton", "new"}
RESTORE = {"none"}
SUPPORTED_PERMISSIONS = {
    "host.workbench",
    "host.events",
    "host.binary",
    "host.filesystem",
    "host.plans:read",
    "host.schema:read",
    "host.storage",
    "host.ai",
    "host.clipboard:read",
    "host.data:read",
    "host.connections:read",
}
# PluginCommandContribution 的字段（deny_unknown_fields）
COMMAND_FIELDS = {"type", "id", "label", "description", "icon", "action", "enablement"}
MENU_ITEM_FIELDS = {
    "location",
    "command",
    "group",
    "order",
    "default_visible",
    "when",
}


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    # --allow-missing-binary：源码仓库里 bin/ 不入库，CI 在 package 之前跑校验时用
    allow_missing_binary = "--allow-missing-binary" in flags
    manifest_path = pathlib.Path(argv[0] if argv else "manifest.json")
    root = manifest_path.parent
    errors: list[str] = []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL cannot read {manifest_path}: {error}", file=sys.stderr)
        return 1

    if manifest.get("manifest_version") != 1:
        errors.append("manifest_version must be 1")
    for field in ("id", "name", "version", "publisher"):
        if not str(manifest.get(field) or "").strip():
            errors.append(f"missing required field {field!r}")
    engines = manifest.get("engines") or {}
    if not str(engines.get("host_api") or "").strip():
        errors.append("engines.host_api is required")

    for permission in manifest.get("permissions") or []:
        if permission.startswith("host.network:"):
            if not permission[len("host.network:") :].startswith("https://"):
                errors.append(f"network permission must be https: {permission}")
        elif permission not in SUPPORTED_PERMISSIONS:
            errors.append(f"unsupported permission {permission!r}")

    icon = manifest.get("icon")
    if icon and not (root / icon).is_file():
        errors.append(f"icon file does not exist: {icon}")

    # entrypoints
    entrypoints = manifest.get("entrypoints") or {}
    ui = entrypoints.get("ui")
    if ui and not (root / ui.get("entry", "")).is_file():
        errors.append(f"ui entry does not exist: {ui.get('entry')}")
    backend = entrypoints.get("backend")
    has_ui = bool(ui)
    if backend:
        exe = str(backend.get("executable") or "")
        if not exe:
            errors.append("backend.executable is required")
        elif not (root / exe).is_file() and not allow_missing_binary:
            errors.append(f"backend executable does not exist: {exe}")
        transport = backend.get("transport")
        if transport not in (None, "stdio-jsonl", "stdio-framed"):
            errors.append(f"invalid backend transport {transport!r}")
        if transport == "stdio-framed" and "host.binary" not in (
            manifest.get("permissions") or []
        ):
            errors.append("stdio-framed transport requires host.binary permission")

    contributions = manifest.get("contributions") or []
    ids: set[str] = set()
    workbenches: set[str] = set()
    commands: set[str] = set()

    for index, contribution in enumerate(contributions):
        ctype = contribution.get("type")
        cid = str(contribution.get("id") or "")
        if ctype not in CONTRIBUTION_TYPES:
            errors.append(
                f"contribution[{index}]: unknown type {ctype!r} "
                f"(command is singular; there is no 'commands')"
            )
            continue
        if not cid:
            errors.append(f"contribution[{index}]: missing id")
        elif cid in ids:
            errors.append(f"duplicate contribution id {cid!r}")
        ids.add(cid)

        if ctype == "workbench":
            workbenches.add(cid)
            if not str(contribution.get("label") or "").strip():
                errors.append(f"workbench {cid}: label is required")
            if not has_ui:
                errors.append(f"workbench {cid}: requires a UI entrypoint")

        if ctype == "command":
            commands.add(cid)
            unknown = set(contribution) - COMMAND_FIELDS
            if unknown:
                errors.append(
                    f"command {cid}: unknown field(s) {sorted(unknown)} "
                    "(presentation/default_visible belong to menus items)"
                )
            if not str(contribution.get("label") or "").strip():
                errors.append(f"command {cid}: label is required")
            action = contribution.get("action") or {}
            atype = action.get("type")
            if atype not in COMMAND_ACTION_TYPES:
                errors.append(f"command {cid}: unsupported action type {atype!r}")
            elif atype == "open-workbench":
                wb = action.get("workbench")
                if not wb:
                    errors.append(f"command {cid}: action.workbench is required")
                if action.get("presentation") not in (None, *PRESENTATIONS):
                    errors.append(f"command {cid}: invalid presentation {action.get('presentation')!r}")
                if action.get("reuse") not in (None, *REUSE):
                    errors.append(f"command {cid}: invalid reuse {action.get('reuse')!r}")
                if action.get("restore") not in (None, *RESTORE):
                    errors.append(f"command {cid}: invalid restore {action.get('restore')!r}")

        if ctype == "menus":
            items = contribution.get("items") or []
            if len(items) > 64:
                errors.append(f"menus {cid}: at most 64 items allowed")
            seen: set[tuple] = set()
            for item in items:
                unknown = set(item) - MENU_ITEM_FIELDS
                if unknown:
                    errors.append(f"menus {cid}: item has unknown field(s) {sorted(unknown)}")
                location = item.get("location")
                if location not in MENU_LOCATIONS:
                    errors.append(f"menus {cid}: invalid location {location!r}")
                group = item.get("group")
                if group not in MENU_GROUPS:
                    errors.append(
                        f"menus {cid}: group {group!r} is outside the host word list "
                        f"({', '.join(sorted(MENU_GROUPS))})"
                    )
                if location == "commandPalette" and "default_visible" in item:
                    errors.append(
                        f"menus {cid}: default_visible is not allowed on commandPalette placements"
                    )
                key = (location, item.get("command"))
                if key in seen:
                    errors.append(f"menus {cid}: duplicate placement {key}")
                seen.add(key)

    # 引用完整性
    for contribution in contributions:
        if contribution.get("type") == "command":
            action = contribution.get("action") or {}
            wb = action.get("workbench")
            if wb and wb not in workbenches:
                errors.append(f"command {contribution.get('id')}: dangling workbench {wb!r}")
        if contribution.get("type") == "menus":
            for item in contribution.get("items") or []:
                target = item.get("command")
                if target not in commands:
                    errors.append(
                        f"menus {contribution.get('id')}: item references undeclared command {target!r}"
                    )
        if contribution.get("type") == "connection-provider":
            wb = contribution.get("workbench")
            if wb and wb not in workbenches:
                errors.append(
                    f"connection-provider {contribution.get('id')}: dangling workbench {wb!r}"
                )

    if errors:
        for message in errors:
            print(f"FAIL {message}", file=sys.stderr)
        return 1

    print(
        f"OK manifest {manifest['id']}@{manifest['version']}: "
        f"{len(contributions)} contribution(s) — {', '.join(sorted(ids))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
