#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""This module defines the main_menu_registry and main menu related helper functions.

Entries of the main_menu_registry must NOT be registered in this module to keep imports
in this module as small as possible.
"""

from cmk.utils.licensing.registry import get_license_message
from cmk.utils.plugin_registry import Registry
from cmk.utils.version import __version__, edition

from cmk.gui.htmllib.generator import HTMLWriter
from cmk.gui.http import request
from cmk.gui.i18n import _, _l
from cmk.gui.logged_in import user
from cmk.gui.type_defs import MegaMenu, TopicMenuItem, TopicMenuTopic
from cmk.gui.utils.html import HTML
from cmk.gui.utils.urls import doc_reference_url, DocReference, makeuri_contextless


def any_show_more_items(topics: list[TopicMenuTopic]) -> bool:
    return any(item.is_show_more for topic in topics for item in topic.items)


class MegaMenuRegistry(Registry[MegaMenu]):
    """A registry that contains the menu entries of the main navigation.

    All menu entries must be obtained via this registry to avoid cyclic
    imports. To avoid typos it's recommended to use the helper methods
    menu_* to obtain the different entries.

    Examples:

        >>> from cmk.gui.i18n import _l
        >>> from cmk.gui.type_defs import MegaMenu
        >>> from cmk.gui.main_menu import mega_menu_registry
        >>> mega_menu_registry.register(MegaMenu(
        ...     name="monitoring",
        ...     title=_l("Monitor"),
        ...     icon="main_monitoring",
        ...     sort_index=5,
        ...     topics=lambda: [],
        ...     search=None,
        ... ))
        MegaMenu(...)
        >>> assert mega_menu_registry["monitoring"].sort_index == 5

    """

    def plugin_name(self, instance: MegaMenu) -> str:
        return instance.name

    def menu_monitoring(self) -> MegaMenu:
        return self["monitoring"]

    def menu_customize(self) -> MegaMenu:
        return self["customize"]

    def menu_setup(self) -> MegaMenu:
        return self["setup"]

    def menu_help(self) -> MegaMenu:
        return self["help"]

    def menu_user(self) -> MegaMenu:
        return self["user"]


mega_menu_registry = MegaMenuRegistry()


def _help_menu_topics() -> list[TopicMenuTopic]:
    return [
        TopicMenuTopic(
            name="learning_checkmk",
            title=_("Learning Checkmk"),
            icon="learning_checkmk",
            items=[
                TopicMenuItem(
                    name="beginners_guide",
                    title=_("Beginner's guide"),
                    url=doc_reference_url(DocReference.INTRO_WELCOME),
                    target="_blank",
                    sort_index=10,
                    icon="learning_beginner",
                ),
                TopicMenuItem(
                    name="user_manual",
                    title=_("User manual"),
                    url=doc_reference_url(),
                    target="_blank",
                    sort_index=20,
                    icon="learning_guide",
                ),
                TopicMenuItem(
                    name="video_tutorials",
                    title=_("Video tutorials"),
                    url="https://www.youtube.com/playlist?list=PL8DfRO2DvOK1slgjfTu0hMOnepf1F7ssh",
                    target="_blank",
                    sort_index=30,
                    icon="learning_video_tutorials",
                ),
                TopicMenuItem(
                    name="community_forum",
                    title=_("Community forum"),
                    url="https://forum.checkmk.com/",
                    target="_blank",
                    sort_index=40,
                    icon="learning_forum",
                ),
            ],
        ),
        TopicMenuTopic(
            name="developer_resources",
            title=_("Developer resources"),
            icon="developer_resources",
            items=[
                TopicMenuItem(
                    name="plugin_api_introduction",
                    title=_("Check plugin API introduction"),
                    url=doc_reference_url(DocReference.DEVEL_CHECK_PLUGINS),
                    target="_blank",
                    sort_index=10,
                    icon={
                        "icon": "services_green",
                        "emblem": "api",
                    },
                ),
                TopicMenuItem(
                    name="plugin_api_reference",
                    title=_("Check plugin API reference"),
                    url="plugin-api/",
                    target="_blank",
                    sort_index=20,
                    icon={
                        "icon": "services_green",
                        "emblem": "api",
                    },
                ),
                TopicMenuItem(
                    name="rest_api_introduction",
                    title=_("REST API introduction"),
                    url=doc_reference_url(DocReference.REST_API),
                    target="_blank",
                    sort_index=30,
                    icon={
                        "icon": "global_settings",
                        "emblem": "api",
                    },
                ),
                TopicMenuItem(
                    name="rest_api_documentation",
                    title=_("REST API documentation"),
                    url="openapi/",
                    target="_blank",
                    sort_index=40,
                    icon={
                        "icon": "global_settings",
                        "emblem": "api",
                    },
                ),
                TopicMenuItem(
                    name="rest_api_interactive_gui",
                    title=_("REST API interactive GUI"),
                    url="api/1.0/ui/",
                    target="_blank",
                    sort_index=50,
                    icon={
                        "icon": "global_settings",
                        "emblem": "api",
                    },
                ),
            ],
        ),
        TopicMenuTopic(
            name="about_checkmk",
            title=_("About Checkmk"),
            icon="about_checkmk",
            items=[
                TopicMenuItem(
                    name="info",
                    title=_("Info"),
                    url="info.py",
                    sort_index=10,
                    icon="tribe29",
                ),
                TopicMenuItem(
                    name="change_log",
                    title=_("Change log (Werks)"),
                    url="change_log.py",
                    sort_index=20,
                    icon="tribe29",
                ),
            ],
        ),
    ]


mega_menu_registry.register(
    MegaMenu(
        name="help_links",
        title=_l("Help"),
        icon="main_help",
        sort_index=18,
        topics=_help_menu_topics,
        info_line=lambda: f"{edition().title} {__version__}{license_status()}",
    )
)


def license_status() -> HTML | str:
    status_message: HTML | str = get_license_message()
    if not status_message:
        return ""
    if user.may("wato.licensing"):
        status_message = HTMLWriter.render_a(
            status_message,
            makeuri_contextless(request, [("mode", "licensing")], filename="wato.py"),
            target="main",
        )
    return HTMLWriter.render_br() + status_message
