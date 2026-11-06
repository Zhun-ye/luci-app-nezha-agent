#!/usr/bin/env python3
"""Verify upstream binaries; build conservative ARM and x86 binaries when needed."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from build_matrix import download, parse_checksums


def verify_binary(path: Path, architecture: str) -> None:
    # Validate the archive's executable without ever running a target binary.
    expected = {"386": (1, 1, 3), "amd64": (2, 1, 62), "arm": (1, 1, 40),
                "arm64": (2, 1, 183), "mips": (1, 2, 8), "mipsle": (1, 1, 8),
                "riscv64": (2, 1, 243), "loong64": (2, 1, 258), "s390x": (2, 2, 22)}
    with ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name == "nezha-agent"]
        if len(names) != 1:
            raise ValueError(f"{path.name} must contain exactly one nezha-agent executable")
        header = archive.read(names[0])[:64]
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise ValueError(f"Not an ELF executable: {path.name}")
    endian = "little" if header[5] == 1 else "big"
    actual = (header[4], header[5], int.from_bytes(header[18:20], endian))
    if actual != expected[architecture]:
        raise ValueError(f"Wrong ELF architecture in {path.name}: {actual}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("build-plan.json"))
    parser.add_argument("--source-dir", type=Path, default=Path("agent-source"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, default=Path("prepared-agent"))
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise ValueError("A full pinned Agent commit SHA is required")
    args.output.mkdir(parents=True, exist_ok=True)
    checksums_data = download(plan["assets"]["checksums.txt"])
    checksums = parse_checksums(checksums_data.decode())
    (args.output / "upstream-checksums.txt").write_bytes(checksums_data)
    tag = plan["agent_tag"]

    def prepare(arch: str) -> dict:
        name = f"nezha-agent_linux_{arch}.zip"
        target = args.output / name
        if arch in plan["legacy_source_arches"]:
            env = dict(os.environ, CGO_ENABLED="0", GOOS="linux", GOARCH=arch,
                       GOARM="5,softfloat", GO386="softfloat", GOMIPS="softfloat")
            with tempfile.TemporaryDirectory(prefix="nezha-agent-") as temporary:
                binary = Path(temporary) / "nezha-agent"
                subprocess.run([
                    "go", "build", "-trimpath", "-buildvcs=false",
                    "-ldflags", f"-s -w -X github.com/nezhahq/agent/pkg/monitor.Version={tag.removeprefix('v')} -X main.arch={arch}",
                    "-o", str(binary), "./cmd/agent",
                ], cwd=args.source_dir, env=env, check=True)
                # Fixed ZIP timestamps and permissions keep repeated packaging deterministic.
                info = ZipInfo("nezha-agent", (1980, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                info.compress_type = ZIP_DEFLATED
                with ZipFile(target, "w") as archive:
                    archive.writestr(info, binary.read_bytes())
            origin = "source: GOARM=5,softfloat" if arch == "arm" else "source: GO386=softfloat"
        else:
            expected = checksums.get(name, "")
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError(f"No upstream SHA-256 for {name}")
            data = download(plan["assets"][name])
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"Upstream SHA-256 mismatch: {name}")
            target.write_bytes(data)
            origin = "official release, upstream SHA-256 verified"
        verify_binary(target, arch)
        return {"arch": arch, "file": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "origin": origin, "agent_tag": tag, "source_commit": args.source_commit}

    official = [arch for arch in plan["agent_arches"] if arch not in plan["legacy_source_arches"]]
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(prepare, official))
    # Share the Go module cache, but avoid two compiler trees competing for RAM.
    records.extend(prepare(arch) for arch in plan["legacy_source_arches"])
    records.sort(key=lambda record: record["arch"])
    manifest = {"agent_tag": tag, "source_commit": args.source_commit, "binaries": records}
    if plan["legacy_source_arches"]:
        manifest["go_version"] = subprocess.check_output(["go", "version"], text=True).strip()
    (args.output / "agent-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (args.output / "sha256sums.txt").write_text("".join(f"{item['sha256']}  {item['file']}\n" for item in records))
    for item in records:
        print(f"{item['arch']}: {item['origin']} ({item['sha256']})", flush=True)


if __name__ == "__main__":
    main()
