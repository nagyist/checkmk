#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

"""Register the builtin global setting configuration variable groups"""

from cmk.gui.i18n import _
from cmk.gui.plugins.watolib.utils import config_variable_group_registry, ConfigVariableGroup


@config_variable_group_registry.register
class ConfigVariableGroupNotifications(ConfigVariableGroup):
    def title(self) -> str:
        return _("Notifications")

    def sort_index(self) -> int:
        return 15


@config_variable_group_registry.register
class ConfigVariableGroupUserInterface(ConfigVariableGroup):
    def title(self) -> str:
        return _("User interface")

    def sort_index(self) -> int:
        return 20


@config_variable_group_registry.register
class ConfigVariableGroupWATO(ConfigVariableGroup):
    def title(self) -> str:
        return _("Setup")

    def sort_index(self) -> int:
        return 25


@config_variable_group_registry.register
class ConfigVariableGroupSiteManagement(ConfigVariableGroup):
    def title(self) -> str:
        return _("Site management")

    def sort_index(self) -> int:
        return 30
