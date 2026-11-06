#!/usr/bin/env bash
set -euo pipefail

# JOB_JSON is one entry from the generated matrix, never evaluated as shell code.
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
job_json="${JOB_JSON:?JOB_JSON is required}"
workspace="${BUILD_WORKSPACE:-${RUNNER_TEMP:-/tmp}/nezha-sdk}"
agent_dir="${AGENT_DIR:-$repo_dir/prepared-agent}"
output_dir="${OUTPUT_DIR:-$repo_dir/upload}"
sdk_url="$(jq -er '.sdk_url' <<< "$job_json")"
sdk_sha256="$(jq -er '.sdk_sha256' <<< "$job_json")"
architecture="$(jq -er '.arch' <<< "$job_json")"
extension="$(jq -er '.format' <<< "$job_json")"
release="$(jq -er '.release' <<< "$job_json")"
agent_arch="$(jq -er '.agent_arch' <<< "$job_json")"
agent_tag="$(jq -er '.agent_tag' "$agent_dir/agent-manifest.json")"
agent_name="nezha-agent_linux_${agent_arch}.zip"
agent_hash="$(jq -er --arg arch "$agent_arch" '.binaries[] | select(.arch == $arch) | .sha256' "$agent_dir/agent-manifest.json")"
revision="${NEZHA_BUILD_RELEASE:-2}"

[[ "$sdk_url" =~ ^https://downloads\.openwrt\.org/releases/[0-9.]+/targets/[a-z0-9_-]+/[a-z0-9_-]+/openwrt-sdk-[A-Za-z0-9_.+-]+\.tar\.(xz|zst)$ ]]
[[ "$sdk_sha256" =~ ^[0-9a-f]{64}$ && "$agent_hash" =~ ^[0-9a-f]{64}$ ]]
[[ "$architecture" =~ ^[a-z0-9][a-z0-9_+-]*$ && "$revision" =~ ^[0-9]+$ ]]
[[ "$extension" == ipk || "$extension" == apk ]]
mkdir -p "$workspace/sdk" "$output_dir"
sdk_dir="$workspace/sdk"
curl --fail --location --retry 4 --connect-timeout 30 "$sdk_url" -o "$workspace/sdk.tar"
printf '%s  %s\n' "$sdk_sha256" "$workspace/sdk.tar" | sha256sum -c -
tar -xf "$workspace/sdk.tar" -C "$sdk_dir" --strip-components=1
rm -f "$workspace/sdk.tar"

cd "$sdk_dir"
./scripts/feeds update base packages luci
./scripts/feeds install -a -p base
./scripts/feeds install -a -p packages
./scripts/feeds install -a -p luci
mkdir -p package/luci-app-nezha-agent
rsync -a --delete \
    --exclude='.git/' --exclude='.github/' --exclude='dist/' \
    --exclude='sdk/' --exclude='upload/' --exclude='prepared-agent/' \
    --exclude='agent-source/' --exclude='build-plan/' --exclude='__pycache__/' \
    "$repo_dir/" package/luci-app-nezha-agent/
if [[ "$agent_arch" == arm || "$agent_arch" == 386 ]]; then
    # Upstream auto-updates can reintroduce instructions missing on legacy CPUs.
    sed -i -e "s/option disable_auto_update '0'/option disable_auto_update '1'/" \
        -e "s/option disable_force_update '0'/option disable_force_update '1'/" \
        package/luci-app-nezha-agent/root/etc/config/nezha-agent
fi
mkdir -p dl
cp "$agent_dir/$agent_name" "dl/$agent_name"
printf '%s  %s\n' "$agent_hash" "dl/$agent_name" | sha256sum -c -
cat > .config <<'EOF'
CONFIG_ALL_NONSHARED=n
CONFIG_ALL_KMODS=n
CONFIG_ALL=n
CONFIG_AUTOREMOVE=n
CONFIG_PACKAGE_luci-app-nezha-agent=m
CONFIG_LUCI_LANG_zh_Hans=y
EOF
make defconfig NEZHA_AGENT_TAG="$agent_tag" NEZHA_AGENT_HASH="$agent_hash" NEZHA_BUILD_RELEASE="$revision"
actual_arch="$(sed -n 's/^CONFIG_TARGET_ARCH_PACKAGES="\([^"]*\)"$/\1/p' .config)"
[[ "$actual_arch" == "$architecture" ]] || {
    printf 'SDK package architecture mismatch: expected %s, found %s\n' "$architecture" "$actual_arch" >&2
    exit 1
}
make package/luci-app-nezha-agent/clean \
    NEZHA_AGENT_TAG="$agent_tag" NEZHA_AGENT_HASH="$agent_hash" NEZHA_BUILD_RELEASE="$revision"
make package/luci-app-nezha-agent/compile -j"${BUILD_JOBS:-$(nproc)}" V=s \
    NEZHA_AGENT_TAG="$agent_tag" NEZHA_AGENT_HASH="$agent_hash" NEZHA_BUILD_RELEASE="$revision"

mapfile -d '' packages < <(find bin/packages -type f \
    \( -name "luci-app-nezha-agent*.${extension}" \
    -o -name "luci-i18n-nezha-agent-zh-cn*.${extension}" \) -print0)
[[ "${#packages[@]}" -eq 2 ]] || {
    printf 'Expected main and Chinese packages; got %s\n' "${#packages[@]}" >&2
    exit 1
}
for package in "${packages[@]}"; do
    cp "$package" "$output_dir/openwrt-${release}_${architecture}_$(basename "$package")"
done
metadata="$output_dir/openwrt-${release}_${architecture}_build.json"
feeds="$(./scripts/feeds list -s)"
jq -n --argjson job "$job_json" --arg agent_tag "$agent_tag" \
    --arg agent_hash "$agent_hash" --arg revision "$revision" \
    --arg feeds "$feeds" --arg commit "${GITHUB_SHA:-$(git -C "$repo_dir" rev-parse HEAD)}" \
    --slurpfile agent_manifest "$agent_dir/agent-manifest.json" \
    '{sdk: $job, package_revision: $revision, package_commit: $commit,
      agent_tag: $agent_tag, agent_sha256: $agent_hash,
      agent: $agent_manifest[0], feeds: $feeds}' > "$metadata"
printf 'Built %s packages for %s (%s)\n' "$extension" "$architecture" "$release"
