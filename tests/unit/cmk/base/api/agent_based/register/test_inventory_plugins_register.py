#!/usr/bin/env python3
# Copyright (C) 2020 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# pylint: disable=protected-access

import pytest

from cmk.utils.type_defs import ParsedSectionName

from cmk.checkers.inventory import InventoryPluginName

import cmk.base.api.agent_based.register.inventory_plugins as inventory_plugins
from cmk.base.api.agent_based.inventory_classes import InventoryPlugin


def dummy_generator(section):  # pylint: disable=unused-argument
    yield "this will raise an exception, when encountered"


def test_create_inventory_plugin_missing_kwarg() -> None:
    with pytest.raises(TypeError):
        _ = inventory_plugins.create_inventory_plugin(name="norris")  # type: ignore[call-arg] #pylint: disable=missing-kwoa

    with pytest.raises(TypeError):
        _ = inventory_plugins.create_inventory_plugin(  # pylint: disable=missing-kwoa
            inventory_function=dummy_generator
        )  # type: ignore[call-arg]


def test_create_inventory_plugin_not_a_generator() -> None:
    def dummy_function(section):  # pylint: disable=unused-argument
        pass

    with pytest.raises(TypeError):
        _ = inventory_plugins.create_inventory_plugin(
            name="norris",
            inventory_function=dummy_function,
            module="mymodule",
        )


def test_create_inventory_plugin_wrong_arg_name() -> None:
    def dummy_generator(noitces):  # pylint: disable=unused-argument
        return
        yield  # pylint: disable=unreachable

    with pytest.raises(TypeError):
        _ = inventory_plugins.create_inventory_plugin(
            name="norris",
            inventory_function=dummy_generator,
            module="mymodule",
        )


def test_create_inventory_plugin_minimal() -> None:
    plugin = inventory_plugins.create_inventory_plugin(
        name="norris",
        inventory_function=dummy_generator,
        module="mymodule",
    )

    assert isinstance(plugin, InventoryPlugin)
    assert plugin.name == InventoryPluginName("norris")
    assert plugin.sections == [ParsedSectionName("norris")]
    assert plugin.inventory_function.__name__ == "dummy_generator"
    assert plugin.inventory_default_parameters == {}
    assert plugin.inventory_ruleset_name is None
    assert plugin.module == "mymodule"

    with pytest.raises(TypeError):
        _ = list(plugin.inventory_function(None))
