#!/usr/bin/env python3
"""Discover one official OpenWrt SDK per supported package ABI and format."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


FAMILIES = {
    "x86_64": "amd64", "i386": "386", "aarch64": "arm64",
    "arm": "arm", "mips": "mips", "mipsel": "mipsle",
    "riscv64": "riscv64", "loongarch64": "loong64", "s390x": "s390x",
}
DEFAULT_RELEASES = {"ipk": "24.10.5", "apk": "25.12.4"}
SDK_NAME = re.compile(r"openwrt-sdk-[A-Za-z0-9_.+\-]+\.Linux-x86_64\.tar\.(?:xz|zst)")


def download(url: str, *, optional: bool = False) -> bytes | None:
    headers = {"User-Agent": "luci-app-nezha-agent-multiarch-builder"}
    if urlparse(url).hostname == "api.github.com" and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers=headers), timeout=90) as response:
                return response.read()
        except HTTPError as error:
            if error.code == 404 and optional:
                return None
            if error.code < 500 and error.code != 429:
                raise
            if attempt == 3:
                raise
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Download failed: {url}")


def read_json(url: str) -> dict:
    return json.loads(download(url))


class DirectoryLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.names: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href", "") or ""
        if tag == "a" and re.fullmatch(r"[a-z0-9][a-z0-9_\-]*/", href):
            self.names.add(href[:-1])


def directories(url: str) -> list[str]:
    parser = DirectoryLinks()
    parser.feed(download(url).decode())
    if not parser.names:
        raise ValueError(f"No target directories at {url}")
    return sorted(parser.names)


def agent_architecture(package_arch: str) -> str | None:
    if package_arch == "x86_64":
        return "amd64"
    family = package_arch.split("_", 1)[0]
    # 486 lacks CMPXCHG8B; FA526/ARM920T are ARMv4 (Go requires ARMv5).
    if package_arch in {"i386_i486", "i386_i386", "arm_fa526", "arm_arm920t"}:
        return None
    return FAMILIES.get(family)


def parse_checksums(contents: str) -> dict[str, str]:
    result = {}
    for line in contents.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match:
            name = match[2].removeprefix("./")
            result[name] = match[1].lower()
    return result


def discover_target(root: str, target: str, extension: str, version: str) -> dict:
    base = f"{root}{target}/"
    checksums = parse_checksums(download(base + "sha256sums").decode())
    sdk_names = sorted(name for name in checksums if SDK_NAME.fullmatch(name))
    if not sdk_names:
        return {"target": target, "reason": "Official SDK is not published"}
    if len(sdk_names) != 1:
        raise ValueError(f"Ambiguous SDK files at {base}: {sdk_names}")
    data = download(base + "profiles.json", optional=True)
    if data is not None:
        profiles = json.loads(data)
        package_arch = profiles["arch_packages"]
        if profiles["version_number"] != version:
            raise ValueError(f"Wrong release in {base}profiles.json")
    else:
        config = download(base + "config.buildinfo").decode()
        match = re.search(r'^CONFIG_TARGET_ARCH_PACKAGES="([a-zA-Z0-9_+\-]+)"$', config, re.M)
        if not match:
            return {"target": target, "reason": "SDK has no published firmware/package ABI metadata"}
        package_arch = match[1]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_+\-]*", package_arch):
        raise ValueError(f"Invalid architecture: {package_arch!r}")
    arch = agent_architecture(package_arch)
    if arch is None:
        return {"target": target, "arch": package_arch,
                "reason": "No compatible architecture in this package's official Agent mapping"}
    return {
        "id": f"{extension}-{package_arch}", "format": extension,
        "release": version, "arch": package_arch, "agent_arch": arch,
        "target": target, "sdk_url": base + sdk_names[0],
        "sdk_sha256": checksums[sdk_names[0]],
    }


def discover_release(extension: str, version: str, workers: int) -> tuple[list, list]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid release version: {version!r}")
    if extension == "ipk" and not version.startswith(("23.05.", "24.10.")):
        raise ValueError("IPK requires an OpenWrt 23.05 or 24.10 SDK")
    if extension == "apk" and tuple(map(int, version.split(".")[:2])) < (25, 12):
        raise ValueError("APK requires an OpenWrt 25.12 or newer SDK")
    root = f"https://downloads.openwrt.org/releases/{version}/targets/"
    with ThreadPoolExecutor(max_workers=workers) as executor:
        parents = directories(root)
        children = executor.map(lambda parent: directories(root + parent + "/"), parents)
        targets = sorted(f"{parent}/{child}" for parent, items in zip(parents, children)
                         for child in items)
        rows = list(executor.map(lambda target: discover_target(root, target, extension, version), targets))
    selected = {}
    skipped = []
    for row in rows:
        if "reason" in row:
            skipped.append({"format": extension, "release": version, **row})
            continue
        selected.setdefault(row["arch"], row)
    if not selected:
        raise ValueError(f"No compatible SDKs for {version}")
    return list(selected.values()), skipped


def make_plan(rows: list[dict], skipped: list[dict], release: dict,
              requested: set[str] | None = None) -> dict:
    assets = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    tag = release["tag_name"]
    if not re.fullmatch(r"v[0-9][A-Za-z0-9_.+\-]*", tag):
        raise ValueError("Invalid Agent release tag")
    if "checksums.txt" not in assets:
        raise ValueError("The Agent release does not publish checksums.txt")
    # Deduplicate SDKs by package ABI, not target device. Never re-label another ABI.
    unique = {}
    for row in sorted(rows, key=lambda item: (item["format"], item["arch"], item["target"])):
        if requested and row["arch"] not in requested:
            continue
        if agent_architecture(row["arch"]) is None:
            skipped.append({**row, "reason": "CPU/ABI is unsupported by the packaged Go Agent"})
            continue
        name = f"nezha-agent_linux_{row['agent_arch']}.zip"
        if name not in assets:
            skipped.append({**row, "reason": f"Agent release {tag} has no {name}"})
            continue
        unique.setdefault((row["format"], row["arch"]), row)
    rows = list(unique.values())
    if requested:
        missing = requested - {row["arch"] for row in rows}
        if missing:
            raise ValueError(f"Requested architectures unavailable: {', '.join(sorted(missing))}")
    if not rows:
        raise ValueError("The selected SDKs and Agent release have no compatible architectures")
    if len(rows) > 256:
        raise ValueError("GitHub's 256-job matrix limit exceeded")
    arches = sorted({row["agent_arch"] for row in rows})
    return {
        "agent_tag": tag, "agent_arches": arches,
        "legacy_source_arches": [arch for arch in arches if arch in {"arm", "386"}],
        "matrix": {"include": rows}, "skipped": skipped,
        "assets": {name: assets[name] for name in assets
                   if name == "checksums.txt" or any(name == f"nezha-agent_linux_{a}.zip" for a in arches)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipk-version", default=DEFAULT_RELEASES["ipk"])
    parser.add_argument("--apk-version", default=DEFAULT_RELEASES["apk"])
    parser.add_argument("--agent-release", type=Path, required=True)
    parser.add_argument("--architectures", default="")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("build-plan.json"))
    args = parser.parse_args()
    rows, skipped = [], []
    for extension, version in (("ipk", args.ipk_version), ("apk", args.apk_version)):
        print(f"Discovering {extension.upper()} SDKs for OpenWrt {version}", file=sys.stderr, flush=True)
        found, omitted = discover_release(extension, version, args.workers)
        rows.extend(found)
        skipped.extend(omitted)
    requested = {item.strip() for item in args.architectures.split(",") if item.strip()} or None
    plan = make_plan(rows, skipped, json.loads(args.agent_release.read_text()), requested)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    counts = {extension: sum(row["format"] == extension for row in plan["matrix"]["include"])
              for extension in ("ipk", "apk")}
    print(f"Build matrix: {counts}, {len(plan['skipped'])} skipped target records", flush=True)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("matrix=" + json.dumps(plan["matrix"], separators=(",", ":")), file=output)
            print("agent_tag=" + plan["agent_tag"], file=output)
            print("legacy_source=" + str(bool(plan["legacy_source_arches"])).lower(), file=output)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            print(f"## OpenWrt build matrix\n\nAgent: `{plan['agent_tag']}`\n", file=summary)
            print("| Format | SDK | Package architecture | Target |\n|---|---|---|---|", file=summary)
            for row in plan["matrix"]["include"]:
                print(f"| {row['format']} | {row['release']} | {row['arch']} | {row['target']} |", file=summary)
            print("\nFull SDK hashes and excluded targets are in `build-plan.json`.", file=summary)


if __name__ == "__main__":
    main()
