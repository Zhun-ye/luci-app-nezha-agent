#!/usr/bin/env python3
"""Regression checks for architecture coverage and partial-release rejection."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_matrix import DirectoryLinks, agent_architecture, discover_target, make_plan, parse_checksums
from collect_release import verify_packages


def row(arch="x86_64", extension="ipk", target="x86/64"):
    return {"id": f"{extension}-{arch}", "format": extension, "release": "24.10.5" if extension == "ipk" else "25.12.4",
            "arch": arch, "agent_arch": agent_architecture(arch), "target": target,
            "sdk_url": "https://downloads.openwrt.org/sdk.tar.zst", "sdk_sha256": "a" * 64}


def release(arches=("amd64", "arm64", "arm", "386", "mips", "mipsle", "riscv64", "loong64")):
    names = ["checksums.txt"] + [f"nezha-agent_linux_{arch}.zip" for arch in arches]
    return {"tag_name": "v2.4.0", "assets": [{"name": name, "browser_download_url": "https://github.com/" + name} for name in names]}


class ArchitectureTests(unittest.TestCase):
    def test_package_abis_map_to_correct_endianness_and_cpu(self):
        expected = {"x86_64": "amd64", "i386_pentium4": "386", "i386_geode": "386",
                    "aarch64_cortex-a53": "arm64", "aarch64_generic": "arm64",
                    "arm_arm926ej-s": "arm", "arm_cortex-a7_neon-vfpv4": "arm",
                    "mips_24kc": "mips", "mipsel_24kc": "mipsle", "riscv64_riscv64": "riscv64",
                    "loongarch64_generic": "loong64"}
        for abi, arch in expected.items():
            with self.subTest(abi=abi):
                self.assertEqual(agent_architecture(abi), arch)
        for abi in ("mips64_octeonplus", "powerpc_464fp", "arc_archs", "armeb_xscale", "i386_i486", "arm_fa526", "arm_arm920t"):
            self.assertIsNone(agent_architecture(abi))

    def test_directory_parser_ignores_parent_links_and_external_urls(self):
        parser = DirectoryLinks()
        parser.feed('<a href="../">up</a><a href="x86/">x86</a><a href="https://host/">host</a><a href="/root/">root</a>')
        self.assertEqual(parser.names, {"x86"})

    def test_sdk_checksum_parses_real_sha256sum_formats(self):
        self.assertEqual(parse_checksums("a" * 64 + "  ./sdk.tar.zst\n" + "b" * 64 + " *other.zip\ninvalid"),
                         {"sdk.tar.zst": "a" * 64, "other.zip": "b" * 64})

    def test_matrix_preserves_all_formats_and_deduplicates_same_abi(self):
        rows = [row(), row(target="x86/other"), row(extension="apk"), row("aarch64_generic"), row("arm_arm926ej-s")]
        plan = make_plan(rows, [], release())
        self.assertEqual(len(plan["matrix"]["include"]), 4)
        self.assertEqual(plan["legacy_source_arches"], ["arm"])

    def test_missing_official_binary_is_reported_not_relabelled(self):
        plan = make_plan([row(), row("loongarch64_generic")], [], release(("amd64",)))
        self.assertEqual(len(plan["matrix"]["include"]), 1)
        self.assertIn("no nezha-agent_linux_loong64.zip", plan["skipped"][0]["reason"])

    def test_typo_in_requested_architecture_is_a_failure(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            make_plan([row()], [], release(), {"aarch64_typo"})

    def test_ambiguous_sdk_is_a_failure(self):
        sdk = "openwrt-sdk-24.10.5-x86-64_gcc-13.3.0_musl.Linux-x86_64.tar."
        checksums = ("a" * 64 + "  " + sdk + "zst\n" + "b" * 64 + "  " + sdk + "xz\n").encode()
        with patch("build_matrix.download", return_value=checksums):
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                discover_target("https://example/", "x86/64", "ipk", "24.10.5")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.plan = make_plan([row(), row(extension="apk")], [], release())

    def write_packages(self, item):
        prefix = f"openwrt-{item['release']}_{item['arch']}_"
        for package in ("luci-app-nezha-agent", "luci-i18n-nezha-agent-zh-cn"):
            (self.directory / f"{prefix}{package}_1.2.0.{item['format']}").write_bytes(b"package")
        (self.directory / (prefix + "build.json")).write_text(json.dumps({"sdk": item, "agent_tag": self.plan["agent_tag"]}))

    def test_rejects_a_release_missing_one_format(self):
        self.write_packages(self.plan["matrix"]["include"][0])
        with self.assertRaisesRegex(ValueError, "Incomplete build"):
            verify_packages(self.plan, self.directory)

    def test_rejects_stale_or_unplanned_packages(self):
        for item in self.plan["matrix"]["include"]:
            self.write_packages(item)
        (self.directory / "stale.ipk").write_bytes(b"stale")
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            verify_packages(self.plan, self.directory)

    def test_rejects_mixed_agent_versions(self):
        for item in self.plan["matrix"]["include"]:
            self.write_packages(item)
        metadata = next(self.directory.glob("*_build.json"))
        data = json.loads(metadata.read_text())
        data["agent_tag"] = "v1.0.0"
        metadata.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "metadata"):
            verify_packages(self.plan, self.directory)

    def test_accepts_one_main_and_language_package_for_every_sdk(self):
        for item in self.plan["matrix"]["include"]:
            self.write_packages(item)
        self.assertEqual(len(verify_packages(self.plan, self.directory)), 2)


if __name__ == "__main__":
    unittest.main()
