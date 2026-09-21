################################################################################
# nodeos-agent
################################################################################

NODEOS_AGENT_VERSION = 0.1.0
NODEOS_AGENT_SITE = $(BR2_EXTERNAL_NODEOS_PATH)/package/nodeos-agent/src
NODEOS_AGENT_SITE_METHOD = local
NODEOS_AGENT_LICENSE = MIT

define NODEOS_AGENT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/nodeos-agent $(TARGET_DIR)/usr/sbin/nodeos-agent
endef

$(eval $(generic-package))
