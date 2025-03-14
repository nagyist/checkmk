#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from collections.abc import Collection, Iterable

import pytest

from cmk.gui.breadcrumb import BreadcrumbItem
from cmk.gui.plugins.wato.utils import base_modes
from cmk.gui.plugins.wato.utils.base_modes import WatoMode
from cmk.gui.plugins.wato.utils.main_menu import (
    ABCMainModule,
    MainModuleTopic,
    MainModuleTopicHosts,
)
from cmk.gui.type_defs import Icon, PermissionName
from cmk.gui.watolib.main_menu import MainModuleRegistry

module_registry = MainModuleRegistry()


class SomeWatoMode(WatoMode):
    @staticmethod
    def static_permissions() -> Collection[PermissionName]:
        return []

    @classmethod
    def name(cls) -> str:
        return "some_wato_mode"


@module_registry.register
class SomeMainModule(ABCMainModule):
    @property
    def mode_or_url(self) -> str:
        return "some_wato_mode"

    @property
    def topic(self) -> MainModuleTopic:
        return MainModuleTopicHosts

    @property
    def title(self) -> str:
        return "Main Module"

    @property
    def icon(self) -> Icon:
        return "icon"

    @property
    def permission(self) -> None | str:
        return "some_permission"

    @property
    def description(self) -> str:
        return "Description"

    @property
    def sort_index(self) -> int:
        return 30

    @property
    def is_show_more(self) -> bool:
        return False


@pytest.fixture(name="main_module_registry", scope="function", autouse=True)
def fixture_main_module_registry(monkeypatch):
    monkeypatch.setattr(
        base_modes,
        "main_module_registry",
        module_registry,
    )


class TestWatoMode:
    def test_breadcrumb_without_additions(
        self,
        request_context,
        main_module_registry,
    ):
        assert list(SomeWatoMode().breadcrumb()) == [
            BreadcrumbItem(title="Hosts", url=None),
            BreadcrumbItem(title="(Untitled module)", url="wato.py?mode=some_wato_mode"),
        ]

    def test_breadcrumb_with_additions(
        self,
        monkeypatch,
        request_context,
        main_module_registry,
    ):
        def additional_breadcrumb_items() -> Iterable[BreadcrumbItem]:
            yield BreadcrumbItem(
                title="In between 1",
                url=None,
            )
            yield BreadcrumbItem(
                title="In between 2",
                url="123",
            )

        monkeypatch.setattr(
            SomeMainModule,
            "additional_breadcrumb_items",
            additional_breadcrumb_items,
        )
        assert list(SomeWatoMode().breadcrumb()) == [
            BreadcrumbItem(title="Hosts", url=None),
            BreadcrumbItem(title="In between 1", url=None),
            BreadcrumbItem(title="In between 2", url="123"),
            BreadcrumbItem(title="(Untitled module)", url="wato.py?mode=some_wato_mode"),
        ]
