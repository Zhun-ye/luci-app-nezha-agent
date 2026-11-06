#!/usr/bin/env python3
"""Reject partial/mixed builds before making download artifacts or a release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def verify_packages(plan: dict, directory: Path) -> list[dict]:
    result = []
    expected_files = set()
    for row in plan["matrix"]["include"]:
        prefix = f"openwrt-{row['release']}_{row['arch']}_"
        main = list(directory.glob(prefix + f"luci-app-nezha-agent*.{row['format']}"))
        language = list(directory.glob(prefix + f"luci-i18n-nezha-agent-zh-cn*.{row['format']}"))
        metadata = directory / (prefix + "build.json")
        if len(main) != 1 or len(language) != 1 or not metadata.is_file():
            raise ValueError(f"Incomplete build for {row['id']}: main={len(main)}, language={len(language)}, metadata={metadata.is_file()}")
        build = json.loads(metadata.read_text())
        if build.get("sdk") != row or build.get("agent_tag") != plan["agent_tag"]:
            raise ValueError(f"Build metadata does not match the plan for {row['id']}")
        for path in (main[0], language[0]):
            if path.stat().st_size == 0:
                raise ValueError(f"Empty package: {path.name}")
            expected_files.add(path.name)
        result.append({**row, "main": main[0].name, "language": language[0].name, "metadata": metadata.name})
    actual = {path.name for path in directory.iterdir() if path.suffix in {".ipk", ".apk"}}
    if actual != expected_files:
        raise ValueError(f"Unexpected or missing package files: {sorted(actual ^ expected_files)}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    records = verify_packages(plan, args.directory)
    shutil.copyfile(args.plan, args.directory / "build-plan.json")
    manifest = {"package_version": args.package_version, "package_revision": args.revision,
                "agent_tag": plan["agent_tag"], "packages": records}
    (args.directory / "packages.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    counts = {extension: sum(row["format"] == extension for row in records) for extension in ("ipk", "apk")}
    notes = [f"LuCI **{args.package_version}-r{args.revision}**，Agent **{plan['agent_tag']}**。\n",
             f"完整构建：**{counts['ipk']} 个 IPK 架构、{counts['apk']} 个 APK 架构**，每个架构包含主包和简体中文语言包。\n",
             "按设备软件包架构选择文件；IPK 与 APK 分别使用对应 OpenWrt SDK 构建。\n",
             "| 格式 | OpenWrt SDK | 软件包架构 |\n|---|---|---|"]
    notes.extend(f"| {row['format'].upper()} | {row['release']} | `{row['arch']}` |" for row in records)
    notes.extend([
        "\n安装前用 `opkg print-architecture` 或 `apk --print-arch` 核对架构。",
        "\nIPK：`opkg install ./对应主包.ipk ./对应中文包.ipk`。",
        "\nAPK：`apk add --allow-untrusted ./对应主包.apk ./对应中文包.apk`；第三方 APK 未使用 OpenWrt 官方签名。",
        "\nARM 使用 `GOARM=5,softfloat`、32 位 x86 使用 `GO386=softfloat` 从固定官方源码提交构建；其他架构校验官方二进制 SHA-256。",
        "\n兼容构建的 ARM / 32 位 x86 包默认关闭 Agent 自动及强制更新，避免被官方二进制替换后无法在旧 CPU 上运行。升级已有配置时请手动确认这两个开关。",
        "\nSDK 选择、排除原因和每个包的源码/Agent/SDK 校验值见 `build-plan.json`、`packages.json` 及各架构的 `*_build.json`。",
        "\n`sha256sums.txt` 包含所有下载文件的 SHA-256。架构构建通过不代表已在实机完成运行测试。",
    ])
    (args.directory / "release-notes.md").write_text("\n".join(notes) + "\n")
    checksums = []
    for path in sorted(args.directory.iterdir()):
        if path.is_file() and path.name != "sha256sums.txt":
            checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    (args.directory / "sha256sums.txt").write_text("".join(checksums))
    print(f"Verified complete matrix: {counts}; {len(records) * 2} installable packages")


if __name__ == "__main__":
    main()
