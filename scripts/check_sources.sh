#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sh -n root/etc/init.d/nezha-agent
sh -n root/etc/uci-defaults/99-nezha-agent
sh -n root/usr/libexec/nezha-agent-manager
sh -n scripts/build_ipk.sh
sh -n scripts/generate_release_notes.sh
bash -n scripts/build_sdk.sh
sh tests/test_arch_mapping.sh
sh tests/test_manager.sh
sh tests/test_uci_defaults.sh
sh tests/test_release_notes.sh
node tests/test_import_parser.js
node --check htdocs/luci-static/resources/view/nezha-agent/config.js
node --check htdocs/luci-static/resources/nezha-agent/import-parser.js
jq empty root/usr/share/luci/menu.d/luci-app-nezha-agent.json
jq empty root/usr/share/rpcd/acl.d/luci-app-nezha-agent.json
python3 -m unittest discover -s tests -p 'test_build_*.py' -v
