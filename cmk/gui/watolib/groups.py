#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import copy
import re
from collections.abc import Sequence
from typing import Any, Literal

import cmk.utils.paths
import cmk.utils.version as cmk_version
from cmk.utils.regex import GROUP_NAME_PATTERN
from cmk.utils.type_defs import EventRule, timeperiod_spec_alias

# It's OK to import centralized config load logic
import cmk.ec.export as ec  # pylint: disable=cmk-module-layer-violation

import cmk.gui.hooks as hooks
import cmk.gui.plugins.userdb.utils as userdb_utils
import cmk.gui.userdb as userdb
from cmk.gui.exceptions import MKUserError
from cmk.gui.groups import (
    AllGroupSpecs,
    GroupName,
    GroupSpec,
    GroupSpecs,
    GroupType,
    load_contact_group_information,
    load_group_information,
)
from cmk.gui.htmllib.generator import HTMLWriter
from cmk.gui.htmllib.html import html
from cmk.gui.http import request
from cmk.gui.i18n import _, _l
from cmk.gui.logged_in import user
from cmk.gui.plugins.watolib.utils import config_variable_registry
from cmk.gui.utils.html import HTML
from cmk.gui.utils.speaklater import LazyString
from cmk.gui.utils.urls import makeuri_contextless
from cmk.gui.valuespec import DualListChoice
from cmk.gui.watolib.changes import add_change
from cmk.gui.watolib.global_settings import GlobalSettings, load_configuration_settings
from cmk.gui.watolib.group_writer import save_group_information
from cmk.gui.watolib.host_attributes import (
    ABCHostAttribute,
    HostAttributeTopic,
    HostAttributeTopicBasicSettings,
)
from cmk.gui.watolib.hosts_and_folders import CREFolder, Folder, folder_preserving_link
from cmk.gui.watolib.notifications import load_notification_rules, load_user_notification_rules
from cmk.gui.watolib.rulesets import AllRulesets
from cmk.gui.watolib.utils import convert_cgroups_from_tuple

if cmk_version.is_managed_edition():
    import cmk.gui.cme.helpers as managed_helpers  # pylint: disable=no-name-in-module
    import cmk.gui.cme.managed as managed  # pylint: disable=no-name-in-module


def add_group(name: GroupName, group_type: GroupType, extra_info: GroupSpec) -> None:
    check_modify_group_permissions(group_type)
    all_groups = load_group_information()
    groups = all_groups.get(group_type, {})

    # Check group name
    if len(name) == 0:
        raise MKUserError("name", _("Please specify a name of the new group."))
    if " " in name:
        raise MKUserError("name", _("Sorry, spaces are not allowed in group names."))
    if not re.match(GROUP_NAME_PATTERN, name):
        raise MKUserError(
            "name",
            _("Invalid group name. Only the characters a-z, A-Z, 0-9, _, . and - are allowed."),
        )
    if name in groups:
        raise MKUserError("name", _("Sorry, there is already a group with that name"))

    _set_group(all_groups, group_type, name, extra_info)
    _add_group_change(
        extra_info, "edit-%sgroups" % group_type, _l("Create new %s group %s") % (group_type, name)
    )


def edit_group(name: GroupName, group_type: GroupType, extra_info: GroupSpec) -> None:
    check_modify_group_permissions(group_type)
    all_groups = load_group_information()
    groups = all_groups.get(group_type, {})

    if name not in groups:
        raise MKUserError("name", _("Unknown group: %s") % name)

    old_group_backup = copy.deepcopy(groups[name])

    _set_group(all_groups, group_type, name, extra_info)
    if cmk_version.is_managed_edition():
        old_customer = managed.get_customer_id(old_group_backup)
        new_customer = managed.get_customer_id(extra_info)
        if old_customer != new_customer:
            _add_group_change(
                old_group_backup,
                "edit-%sgroups" % group_type,
                _l("Removed %sgroup %s from customer %s")
                % (group_type, name, managed.get_customer_name_by_id(old_customer)),
            )
            _add_group_change(
                extra_info,
                "edit-%sgroups" % group_type,
                _l("Moved %sgroup %s to customer %s. Additional properties may have changed.")
                % (group_type, name, managed.get_customer_name_by_id(new_customer)),
            )
        else:
            _add_group_change(
                old_group_backup,
                "edit-%sgroups" % group_type,
                _l("Updated properties of %sgroup %s") % (group_type, name),
            )
    else:
        _add_group_change(
            extra_info,
            "edit-%sgroups" % group_type,
            _l("Updated properties of %s group %s") % (group_type, name),
        )


class UnknownGroupException(Exception):
    ...


class GroupInUseException(Exception):
    ...


def delete_group(name: GroupName, group_type: GroupType) -> None:
    check_modify_group_permissions(group_type)
    # Check if group exists
    all_groups = load_group_information()
    groups = all_groups.get(group_type, {})
    if name not in groups:
        raise UnknownGroupException(
            None,
            _("Unknown %s group: %s") % (group_type, name),
        )

    # Check if still used
    usages = find_usages_of_group(name, group_type)
    if usages:
        raise GroupInUseException(
            None,
            _("Unable to delete group. It is still in use by: %s")
            % ", ".join([e[0] for e in usages]),
        )

    # Delete group
    group = groups.pop(name)
    save_group_information(all_groups)
    _add_group_change(
        group, "edit-%sgroups" % group_type, _l("Deleted %s group %s") % (group_type, name)
    )


# TODO: Consolidate all group change related functions in a class that can be overriden
# by the CME code for better encapsulation.
def _add_group_change(group: GroupSpec, action_name: str, text: LazyString) -> None:
    group_sites = None
    if cmk_version.is_managed_edition():
        cid = managed.get_customer_id(group)
        if not managed.is_global(cid):
            if cid is None:  # conditional caused by bad typing
                raise Exception("cannot happen: no customer ID")
            group_sites = list(managed_helpers.get_sites_of_customer(cid).keys())

    add_change(action_name, text, sites=group_sites)


def check_modify_group_permissions(group_type: GroupType) -> None:
    required_permissions = {
        "contact": ["wato.users"],
        "host": ["wato.groups"],
        "service": ["wato.groups"],
    }

    # Check permissions
    perms = required_permissions.get(group_type)
    if perms is None:
        raise Exception(f"invalid group type {group_type!r}")
    for permission in perms:
        user.need_permission(permission)


def _set_group(
    all_groups: AllGroupSpecs,
    group_type: GroupType,
    name: GroupName,
    extra_info: GroupSpec,
) -> None:
    # Check if this alias is used elsewhere
    alias = extra_info.get("alias")
    if not alias:
        raise MKUserError("alias", "Alias is missing")

    unique, info = is_alias_used(group_type, name, alias)
    if not unique:
        assert info
        raise MKUserError("alias", info)

    all_groups.setdefault(group_type, {})
    all_groups[group_type].setdefault(name, {})
    all_groups[group_type][name] = extra_info
    save_group_information(all_groups)

    if group_type == "contact":
        hooks.call("contactgroups-saved", all_groups)


def find_usages_of_group(name: GroupName, group_type: GroupType) -> list[tuple[str, str]]:
    usages = []
    if group_type == "contact":
        usages = find_usages_of_contact_group(name)
    elif group_type == "host":
        usages = find_usages_of_host_group(name)
    elif group_type == "service":
        usages = find_usages_of_service_group(name)
    return usages


def find_usages_of_contact_group(name: GroupName) -> list[tuple[str, str]]:
    """Check if a group is currently in use and cannot be deleted
    Returns a list of occurrances.
    """
    global_config = load_configuration_settings()

    used_in = _find_usages_of_group_in_rules(name, ["host_contactgroups", "service_contactgroups"])
    used_in += _find_usages_of_contact_group_in_users(name)
    used_in += _find_usages_of_contact_group_in_default_user_profile(name, global_config)
    used_in += _find_usages_of_contact_group_in_mkeventd_notify_contactgroup(name, global_config)
    used_in += _find_usages_of_contact_group_in_hosts_and_folders(name, Folder.root_folder())
    used_in += _find_usages_of_contact_group_in_notification_rules(name)
    used_in += _find_usages_of_contact_group_in_dashboards(name)
    used_in += _find_usages_of_contact_group_in_ec_rules(name)

    return used_in


def _find_usages_of_contact_group_in_users(name: GroupName) -> list[tuple[str, str]]:
    """Is the contactgroup assigned to a user?"""
    used_in = []
    users = userdb.load_users()
    for userid, user_spec in sorted(users.items(), key=lambda x: x[1].get("alias", x[0])):
        cgs = user_spec.get("contactgroups", [])
        if name in cgs:
            used_in.append(
                (
                    "{}: {}".format(_("User"), user_spec.get("alias", userid)),
                    folder_preserving_link([("mode", "edit_user"), ("edit", userid)]),
                )
            )
    return used_in


def _find_usages_of_contact_group_in_default_user_profile(
    name: GroupName, global_config: GlobalSettings
) -> list[tuple[str, str]]:
    """Used in default_user_profile?"""
    used_in = []
    config_variable = config_variable_registry["default_user_profile"]()
    domain = config_variable.domain()
    configured = global_config.get("default_user_profile", {})
    default_value = domain().default_globals()["default_user_profile"]
    if (configured and name in configured["contactgroups"]) or name in default_value[
        "contactgroups"
    ]:
        used_in.append(
            (
                "%s" % (_("Default User Profile")),
                folder_preserving_link(
                    [("mode", "edit_configvar"), ("varname", "default_user_profile")]
                ),
            )
        )
    return used_in


def _find_usages_of_contact_group_in_mkeventd_notify_contactgroup(
    name: GroupName, global_config: GlobalSettings
) -> list[tuple[str, str]]:
    """Is the contactgroup used in mkeventd notify (if available)?"""
    used_in = []
    if "mkeventd_notify_contactgroup" in config_variable_registry:
        config_variable = config_variable_registry["mkeventd_notify_contactgroup"]()
        domain = config_variable.domain()
        configured = global_config.get("mkeventd_notify_contactgroup")
        default_value = domain().default_globals()["mkeventd_notify_contactgroup"]
        if (configured and name == configured) or name == default_value:
            used_in.append(
                (
                    "%s" % (config_variable.valuespec().title()),
                    folder_preserving_link(
                        [("mode", "edit_configvar"), ("varname", "mkeventd_notify_contactgroup")]
                    ),
                )
            )
    return used_in


def _find_usages_of_contact_group_in_hosts_and_folders(
    name: GroupName, folder: CREFolder
) -> list[tuple[str, str]]:
    used_in = []
    for subfolder in folder.subfolders():
        used_in += _find_usages_of_contact_group_in_hosts_and_folders(name, subfolder)

    attributes = folder.attributes()
    if name in attributes.get("contactgroups", {}).get("groups", []):
        used_in.append((_("Folder: %s") % folder.alias_path(), folder.edit_url()))

    for host in folder.hosts().values():
        attributes = host.attributes()
        if name in attributes.get("contactgroups", {}).get("groups", []):
            used_in.append((_("Host: %s") % host.name(), host.edit_url()))

    return used_in


def _find_usages_of_contact_group_in_notification_rules(name: str) -> list[tuple[str, str]]:
    used_in: list[tuple[str, str]] = []
    for rule in load_notification_rules():
        if _used_in_notification_rule(name, rule):
            title = "{}: {}".format(_("Notification rule"), rule.get("description", ""))
            used_in.append((title, "wato.py?mode=notifications"))

    for user_id, user_rules in load_user_notification_rules().items():
        for rule in user_rules:
            if _used_in_notification_rule(name, rule):
                title = "{}: {}".format(
                    _("Notification rules of user %s") % user_id,
                    rule.get("description", ""),
                )
                used_in.append((title, "wato.py?mode=user_notifications&user=%s" % user_id))

    return used_in


def _find_usages_of_contact_group_in_dashboards(name: str) -> list[tuple[str, str]]:
    used_in: list[tuple[str, str]] = []
    # FIXME: This leads to a circular import otherwise.
    from cmk.gui.dashboard import get_all_dashboards

    for (dashboard_owner, dashboard_name), board in get_all_dashboards().items():
        public_value: bool | tuple[str, Sequence[str]] = board["public"]
        if isinstance(public_value, tuple) and name in public_value[1]:
            title = "{}: {}".format(_("Dashboard of user %s") % dashboard_owner, dashboard_name)
            used_in.append(
                (
                    title,
                    makeuri_contextless(
                        request,
                        [
                            ("load_name", dashboard_name),
                            ("mode", "edit"),
                            ("owner", dashboard_owner),
                        ],
                        filename="edit_dashboard.py",
                    ),
                )
            )
    return used_in


def _used_in_notification_rule(name: str, rule: EventRule) -> bool:
    return name in rule.get("contact_groups", []) or name in rule.get("match_contactgroups", [])


def _find_usages_of_contact_group_in_ec_rules(name: str) -> list[tuple[str, str]]:
    """Is the contactgroup used in an eventconsole rule?"""
    used_in: list[tuple[str, str]] = []
    rule_packs = ec.load_rule_packs()
    for pack in rule_packs:
        for nr, rule in enumerate(pack.get("rules", [])):
            if name in rule.get("contact_groups", {}).get("groups", []):
                used_in.append(
                    (
                        "{}: {}".format(_("Event console rule"), rule["id"]),
                        folder_preserving_link(
                            [
                                ("mode", "mkeventd_edit_rule"),
                                ("edit", nr),
                                ("rule_pack", pack["id"]),
                            ]
                        ),
                    )
                )
    return used_in


def find_usages_of_host_group(name: GroupName) -> list[tuple[str, str]]:
    return _find_usages_of_group_in_rules(name, ["host_groups"])


def find_usages_of_service_group(name: GroupName) -> list[tuple[str, str]]:
    return _find_usages_of_group_in_rules(name, ["service_groups"])


def _find_usages_of_group_in_rules(name: GroupName, varnames: list[str]) -> list[tuple[str, str]]:
    used_in = []
    rulesets = AllRulesets.load_all_rulesets()
    for varname in varnames:
        ruleset = rulesets.get(varname)
        for _folder, _rulenr, rule in ruleset.get_rules():
            if rule.value == name:
                used_in.append(
                    (
                        "{}: {}".format(_("Ruleset"), ruleset.title()),
                        folder_preserving_link([("mode", "edit_ruleset"), ("varname", varname)]),
                    )
                )
    return used_in


def is_alias_used(
    my_what: GroupType | Literal["roles", "timeperiods"], my_name: GroupName, my_alias: str
) -> tuple[bool, str | None]:
    # Host / Service / Contact groups
    all_groups = load_group_information()
    for what, groups in all_groups.items():
        for gid, group in groups.items():
            if group["alias"] == my_alias and (my_what != what or my_name != gid):
                return False, _("This alias is already used in the %s group %s.") % (what, gid)

    # Timeperiods
    timeperiods = cmk.gui.watolib.timeperiods.load_timeperiods()
    for key, value in timeperiods.items():
        if timeperiod_spec_alias(value) == my_alias and (
            my_what != "timeperiods" or my_name != key
        ):
            return False, _("This alias is already used in timeperiod %s.") % key

    # Roles
    roles = userdb_utils.load_roles()
    for key, value in roles.items():
        if value.get("alias") == my_alias and (my_what != "roles" or my_name != key):
            return False, _("This alias is already used in the role %s.") % key

    return True, None


class HostAttributeContactGroups(ABCHostAttribute):
    """Attribute needed for folder permissions"""

    def __init__(self) -> None:
        ABCHostAttribute.__init__(self)
        self._contactgroups: GroupSpecs | None = None
        self._loaded_at: int | None = None

    def name(self) -> str:
        return "contactgroups"

    def title(self) -> str:
        return _("Permissions")

    def topic(self) -> type[HostAttributeTopic]:
        return HostAttributeTopicBasicSettings

    @classmethod
    def sort_index(cls) -> int:
        return 25

    def is_show_more(self) -> bool:
        return True

    def help(self) -> str:
        url = makeuri_contextless(
            request,
            [("mode", "edit_ruleset"), ("varname", "host_contactgroups")],
            filename="wato.py",
        )
        return (
            _(
                "Only members of the contact groups listed here have Setup "
                "permission for the host/folder. Optionally, you can make these "
                "contact groups automatically monitor contacts. The assignment "
                "of hosts to contact groups can also be defined by "
                "<a href='%s'>rules</a>."
            )
            % url
        )

    def show_in_table(self) -> bool:
        return False

    def show_in_folder(self) -> bool:
        return True

    def default_value(self) -> Any:
        return (True, [])

    def paint(self, value, hostname):
        value = convert_cgroups_from_tuple(value)
        texts: list[HTML] = []
        self.load_data()
        if self._contactgroups is None:  # conditional caused by horrible API
            raise Exception("invalid contact groups")
        items = self._contactgroups.items()
        for name, cgroup in sorted(items, key=lambda x: x[1]["alias"]):
            if name in value["groups"]:
                display_name = cgroup.get("alias", name)
                texts.append(
                    HTMLWriter.render_a(
                        display_name,
                        href=makeuri_contextless(
                            request,
                            [("mode", "edit_contact_group"), ("edit", name)],
                            filename="wato.py",
                        ),
                    )
                )
        result: HTML = HTML(", ").join(texts)
        if texts and value["use"]:
            result += HTMLWriter.render_span(
                HTMLWriter.render_b("*"),
                title=_("These contact groups are also used in the monitoring configuration."),
            )
        return "", result

    def render_input(self, varprefix: str, value: Any) -> None:
        value = convert_cgroups_from_tuple(value)

        # If we're just editing a host, then some of the checkboxes will be missing.
        # This condition is not very clean, but there is no other way to savely determine
        # the context.
        is_host = bool(request.var("host")) or request.var("mode") == "newhost"
        is_search = varprefix == "host_search"

        # Only show contact groups I'm currently in and contact
        # groups already listed here.
        self.load_data()
        self._vs_contactgroups().render_input(varprefix + self.name(), value["groups"])

        html.hr()

        if is_host:
            html.checkbox(
                varprefix + self.name() + "_use",
                value["use"],
                label=_("Add these contact groups to the host"),
            )

        elif not is_search:
            html.checkbox(
                varprefix + self.name() + "_recurse_perms",
                value["recurse_perms"],
                label=_("Give these groups also <b>permission on all subfolders</b>"),
            )
            html.hr()
            html.checkbox(
                varprefix + self.name() + "_use",
                value["use"],
                label=_("Add these groups as <b>contacts</b> to all hosts <b>in this folder</b>"),
            )
            html.br()
            html.checkbox(
                varprefix + self.name() + "_recurse_use",
                value["recurse_use"],
                label=_(
                    "Add these groups as <b>contacts</b> to all hosts <b>in all subfolders of this folder</b>"
                ),
            )

        html.hr()
        html.help(
            _(
                "With this option contact groups that are added to hosts are always "
                "being added to services, as well. This only makes a difference if you have "
                "assigned other contact groups to services via rules in <i>Host & Service Parameters</i>. "
                "As long as you do not have any such rule a service always inherits all contact groups "
                "from its host."
            )
        )
        html.checkbox(
            varprefix + self.name() + "_use_for_services",
            value.get("use_for_services", False),
            label=_("Always add host contact groups also to its services")
            if is_host
            else _(
                "Always add these groups as <b>contacts</b> to all services <b>in all subfolders of this folder</b>"
            ),
        )

    def load_data(self):
        # Make cache valid only during this HTTP request
        if self._loaded_at == id(html):
            return
        self._loaded_at = id(html)
        self._contactgroups = load_contact_group_information()

    def from_html_vars(self, varprefix: str) -> dict[str, Any]:
        self.load_data()

        cgs = self._vs_contactgroups().from_html_vars(varprefix + self.name())

        return {
            "groups": cgs,
            "recurse_perms": html.get_checkbox(varprefix + self.name() + "_recurse_perms"),
            "use": html.get_checkbox(varprefix + self.name() + "_use"),
            "use_for_services": html.get_checkbox(varprefix + self.name() + "_use_for_services"),
            "recurse_use": html.get_checkbox(varprefix + self.name() + "_recurse_use"),
        }

    def filter_matches(self, crit, value, hostname):
        value = convert_cgroups_from_tuple(value)
        # Just use the contact groups for searching
        for contact_group in crit["groups"]:
            if contact_group not in value["groups"]:
                return False
        return True

    def _vs_contactgroups(self):
        if self._contactgroups is None:  # conditional caused by horrible API
            raise Exception("invalid contact groups")
        cg_choices = sorted(
            [
                (cg_id, cg_attrs.get("alias", cg_id))
                for cg_id, cg_attrs in self._contactgroups.items()
            ],
            key=lambda x: x[1],
        )
        return DualListChoice(choices=cg_choices, rows=20, size=100)

    def validate_input(self, value, varprefix):
        if not isinstance(value, dict):
            raise MKUserError(self.name(), "Unknown format.")
        self.load_data()
        self._vs_contactgroups().validate_value(value.get("groups", []), varprefix)

    def openapi_field(self):
        # FIXME: due to cyclical imports which, when fixed, expose even more cyclical imports.
        from cmk.gui import fields as gui_fields

        from cmk import fields

        return fields.Nested(
            gui_fields.HostContactGroup,
            description=self.help(),
        )
