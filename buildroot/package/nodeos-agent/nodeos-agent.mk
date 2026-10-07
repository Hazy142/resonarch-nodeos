################################################################################
# nodeos-agent
################################################################################

NODEOS_AGENT_VERSION = 0.2.0
NODEOS_AGENT_SITE = $(BR2_EXTERNAL_NODEOS_PATH)/package/nodeos-agent/src
NODEOS_AGENT_SITE_METHOD = local
NODEOS_AGENT_LICENSE = MIT

define NODEOS_AGENT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/nodeos-lib.sh $(TARGET_DIR)/usr/lib/nodeos/nodeos-lib.sh
	for tool in nodeos-agent nodeos-netgate nodeos-evidence nodeos-console; do \
		$(INSTALL) -D -m 0755 $(@D)/$$tool $(TARGET_DIR)/usr/sbin/$$tool; \
	done
	ln -sf nodeos-evidence $(TARGET_DIR)/usr/sbin/nodeos-evidence-export
endef

$(eval $(generic-package))
