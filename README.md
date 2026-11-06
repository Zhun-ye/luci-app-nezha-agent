# luci-app-nezha-agent

适用于 OpenWrt 的哪吒监控 Agent LuCI 管理插件，采用现代 LuCI JavaScript
页面并通过 procd 管理 Agent 服务。

哪吒监控 Agent 官方仓库：<https://github.com/nezhahq/agent>

## 功能

- 在 LuCI 中配置面板服务器、客户端密钥和官方 Agent 参数
- 粘贴哪吒 V1 面板生成的 Linux 安装命令并自动填充连接参数
- 显示服务状态并支持 procd 开机启动、重载和异常重启
- 构建时获取官方最新 Agent Release，并校验官方 SHA-256
- 保存配置时保留 Agent UUID 和其他未由 LuCI 管理的 YAML 字段
- 提供独立简体中文语言包

## 支持范围

本 fork 自动扫描 OpenWrt 官方 SDK，按**软件包架构 ABI 去重**，为可支持的全部架构分别生成 IPK、APK 主包和中文语言包。当前默认使用：

| 适用版本 | 包格式 | 构建 SDK |
| --- | --- | --- |
| OpenWrt 24.10 | IPK | OpenWrt 24.10.5，各架构对应 SDK |
| OpenWrt 25.12 | APK | OpenWrt 25.12.4，各架构对应 SDK |

架构范围包括 x86_64、32 位 x86、ARMv5/6/7、AArch64、MIPS/MIPSel、RISC-V 64、LoongArch 64 在上述发行版中提供 SDK 和 ABI 元数据的变体。每次构建在 Actions Summary 和 Release 中列出实际支持的完整架构表，并在 `build-plan.json` 中记录 SDK URL、SHA-256 和排除原因。

本项目没有官方 Agent 二进制映射的架构（例如 32 位 PowerPC、MIPS64、ARC），或未提供可识别 ABI 元数据的 SDK，不会被冒充为其他架构发布。构建通过表示打包验证通过，实机运行仍取决于设备的 CPU、内核和可用资源。

Release 中每个平台包含两个包：

- `luci-app-nezha-agent`：主程序、服务和 LuCI 页面
- `luci-i18n-nezha-agent-zh-cn`：简体中文语言包

## 安装

先用 `opkg print-architecture` 或 `apk --print-arch` 确认设备软件包架构，从 Releases 下载**同一版本、同一架构的主包和中文包**。以下以 `aarch64_cortex-a53` 为例。

OpenWrt 24.10：

```sh
opkg install ./openwrt-24.10.5_aarch64_cortex-a53_*.ipk
```

OpenWrt 25.12：

```sh
apk add --allow-untrusted ./openwrt-25.12.4_aarch64_cortex-a53_*.apk
```

安装后进入“服务 → 哪吒监控 Agent”完成配置。

也可以将哪吒 V1 面板生成的 Linux 安装命令粘贴到“V1 安装命令”，点击“解析并填充”，核对提取的服务器、客户端密钥、TLS 和 UUID 后再保存并应用。安装命令本身不会写入配置。命令不包含 `NZ_UUID` 时，UUID 字段会被清空，并在应用配置时生成新的 UUID。

## GitHub Actions 自动编译

- 推送到 `main`、推送 `v*` 标签时自动构建；Pull Request 执行构建验证，但不发布 Release。
- 在 **Actions → Build all OpenWrt architectures → Run workflow** 可以手动启动。
- `ipk_version` / `apk_version` 可修改 SDK 发行版；IPK 支持 23.05、24.10 系列，APK 使用 25.12 或更新系列。不同发行版的实际架构表可能不同。
- `agent_tag` 留空时解析官方最新 Agent Release，并固定其源码提交；填写后按指定版本构建。
- `architectures` 留空会构建全部；也可填写 `x86_64,aarch64_cortex-a53` 等以便测试。指定部分架构时只上传 Artifacts，不发布全量 Release。
- `publish` 决定手动的完整构建是否发布。普通 `main` 推送和标签构建成功后自动创建以 `build-` 开头的独立 Release。
- 全量构建最多同时运行 12 个 SDK 任务；某架构失败不会取消其他架构，但会阻止发布不完整的 Release。
- 每个架构的安装包和构建记录保存为 `packages-*` Artifact；完整集合保存为 `all-packages-*`。Release 包含逐文件 SHA-256、`packages.json` 和完整 SDK 选择记录。

首次 fork 后需在 GitHub 的 Actions 页启用工作流。使用仓库内置 `GITHUB_TOKEN`，无需自行保存 PAT。发布 job 使用 `contents: write`，其余 job 仅有读取权限。

每次构建将仓库运行编号用于软件包修订号，以便新构建可替换旧包。默认普通 SDK 手工构建修订号为 `2`。

### Agent 二进制与旧 CPU

ARM 使用固定官方源码提交以 `CGO_ENABLED=0 GOARM=5,softfloat` 构建，32 位 x86 使用 `CGO_ENABLED=0 GO386=softfloat` 构建，减少对 VFP/SSE2 的依赖。其余架构下载并校验官方 Release 的二进制；所有归档再次检查 ELF 架构和大小端。

兼容构建的 ARM / 32 位 x86 安装包默认关闭 Agent 自动及强制更新，避免自动替换为要求更高指令集的官方二进制。**升级已有配置时，请在 LuCI 中手动确认这两个选项**；已有配置按 OpenWrt 的常规方式保留。通过重新运行本工作流获取新版兼容包。

## OpenWrt SDK 手工编译

将项目放入源码树或 SDK 的 `package/luci-app-nezha-agent`：

```sh
cat >> .config <<'EOF'
CONFIG_PACKAGE_luci-app-nezha-agent=m
CONFIG_LUCI_LANG_zh_Hans=y
EOF

make defconfig
make package/luci-app-nezha-agent/{clean,compile} V=s
```

默认会解析官方最新 Agent Release。CI 可以通过以下 Make 变量固定同一次构建：

```sh
make package/luci-app-nezha-agent/compile \
  NEZHA_AGENT_TAG=v2.2.2 \
  NEZHA_AGENT_HASH=<nezha-agent_linux_amd64.zip 的 SHA-256>
```

## 自动更新

Agent 自带自动更新功能，默认配置为：

```yaml
disable_auto_update: false
```

原始配置允许自动更新；本 fork 的 ARM / 32 位 x86 兼容安装包会将 `disable_auto_update` 和 `disable_force_update` 的初始值设为 `true`，原因见上述旧 CPU 说明。其他架构保留原始默认值。

## 配置文件

- UCI 配置：`/etc/config/nezha-agent`
- Agent 配置：`/etc/nezha-agent/config.yml`
- Agent 程序：`/usr/bin/nezha-agent`
- 服务脚本：`/etc/init.d/nezha-agent`

UUID 保存在 UCI 和 Agent 配置文件中；首次缺失或在 LuCI 中清空时生成，并写回 UCI。升级时会自动迁移已有 Agent 配置文件中的 UUID。LuCI 支持配置多个 `custom_ip_api`。使用透明代理时，应将对应 API 域名加入代理软件的直连规则，避免 Agent 获取到代理出口 IP。配置同步只更新 LuCI 管理的字段，手动添加的其他 YAML 参数不会被校验或删除。

卸载软件包会自动停止并禁用服务，但保留用户配置。若不再需要，可手动删除 `/etc/config/nezha-agent`、`/etc/config/nezha-agent-opkg` 和 `/etc/nezha-agent`。
