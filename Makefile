include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-nezha-agent
PKG_VERSION:=1.2.0
PKG_RELEASE:=$(if $(NEZHA_BUILD_RELEASE),$(NEZHA_BUILD_RELEASE),2)
PKG_PO_VERSION:=$(PKG_VERSION)

PKG_LICENSE:=MIT Apache-2.0
PKG_MAINTAINER:=OpenWrt LuCI Community

NEZHA_AGENT_TAG?=$(strip $(shell curl -fsSL https://api.github.com/repos/nezhahq/agent/releases/latest 2>/dev/null | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1))
NEZHA_AGENT_VERSION:=$(patsubst v%,%,$(NEZHA_AGENT_TAG))

ifeq ($(ARCH),x86_64)
  NEZHA_AGENT_ARCH:=amd64
else ifeq ($(ARCH),i386)
  NEZHA_AGENT_ARCH:=386
else ifeq ($(ARCH),aarch64)
  NEZHA_AGENT_ARCH:=arm64
else ifeq ($(ARCH),arm)
  NEZHA_AGENT_ARCH:=arm
else ifeq ($(ARCH),mips)
  NEZHA_AGENT_ARCH:=mips
else ifeq ($(ARCH),mipsel)
  NEZHA_AGENT_ARCH:=mipsle
else ifeq ($(ARCH),riscv64)
  NEZHA_AGENT_ARCH:=riscv64
else ifeq ($(ARCH),loongarch64)
  NEZHA_AGENT_ARCH:=loong64
else ifeq ($(ARCH),s390x)
  NEZHA_AGENT_ARCH:=s390x
endif

ifneq ($(ARCH),)
  ifeq ($(NEZHA_AGENT_ARCH),)
    $(error Unsupported OpenWrt architecture: $(ARCH))
  endif
endif

PKG_SOURCE:=nezha-agent_linux_$(NEZHA_AGENT_ARCH).zip
PKG_SOURCE_URL:=https://github.com/nezhahq/agent/releases/download/$(NEZHA_AGENT_TAG)
NEZHA_AGENT_HASH?=$(strip $(shell curl -fsSL https://github.com/nezhahq/agent/releases/download/$(NEZHA_AGENT_TAG)/checksums.txt 2>/dev/null | awk '$$2 == "$(PKG_SOURCE)" { print $$1; exit }'))
PKG_HASH:=$(NEZHA_AGENT_HASH)

LUCI_TITLE:=LuCI support for Nezha Agent
LUCI_DEPENDS:=+luci-base +ca-bundle

define Package/$(PKG_NAME)/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	/etc/init.d/nezha-agent enable 2>/dev/null || true
	rm -f /tmp/luci-indexcache.* 2>/dev/null
	rm -rf /tmp/luci-modulecache 2>/dev/null
	killall -HUP rpcd 2>/dev/null || true
}
exit 0
endef

define Package/$(PKG_NAME)/prerm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	/etc/init.d/nezha-agent stop 2>/dev/null || true
	/etc/init.d/nezha-agent disable 2>/dev/null || true
}
exit 0
endef

define Package/$(PKG_NAME)/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	rm -f /tmp/luci-indexcache.* 2>/dev/null
	rm -rf /tmp/luci-modulecache 2>/dev/null
	killall -HUP rpcd 2>/dev/null || true
	printf '%s\n' 'Nezha Agent configuration was retained.'
	printf '%s\n' 'Remove it manually if no longer needed:'
	printf '%s\n' '  rm -f /etc/config/nezha-agent /etc/config/nezha-agent-opkg'
	printf '%s\n' '  rm -rf /etc/nezha-agent'
}
exit 0
endef

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
