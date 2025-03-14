#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import pytest

from tests.unit.conftest import FixRegister

from cmk.checkers.checking import CheckPluginName

from cmk.base.plugins.agent_based.agent_based_api.v1 import Metric, Result, Service, State, TableRow
from cmk.base.plugins.agent_based.agent_based_api.v1.type_defs import (
    CheckResult,
    InventoryResult,
    StringTable,
)
from cmk.base.plugins.agent_based.oracle_recovery_area import inventory_oracle_recovery_area

from .utils_inventory import sort_inventory_result

_AGENT_OUTPUT = [
    ["AIMDWHD1", "300", "51235", "49000", "300"],
]


@pytest.mark.parametrize(
    "string_table, expected_result",
    [
        (
            _AGENT_OUTPUT,
            [
                Service(item="AIMDWHD1"),
            ],
        ),
    ],
)
def test_discover_oracle_recovery_area(
    fix_register: FixRegister, string_table: StringTable, expected_result: CheckResult
) -> None:
    check_plugin = fix_register.check_plugins[CheckPluginName("oracle_recovery_area")]
    assert sorted(check_plugin.discovery_function(string_table)) == expected_result


@pytest.mark.parametrize(
    "string_table, item, expected_result",
    [
        (
            _AGENT_OUTPUT,
            "AIMDWHD1",
            [
                Result(
                    state=State.CRIT,
                    summary="47.9 GiB out of 50.0 GiB used (95.1%, warn/crit at 70.0%/90.0%), 300 MiB reclaimable",
                ),
                Metric("used", 49000.0, levels=(35864.5, 46111.5), boundaries=(0.0, 51235.0)),
                Metric("reclaimable", 300.0),
            ],
        ),
    ],
)
def test_check_oracle_recovery_area(
    fix_register: FixRegister, string_table: StringTable, item: str, expected_result: CheckResult
) -> None:
    check_plugin = fix_register.check_plugins[CheckPluginName("oracle_recovery_area")]
    assert (
        list(
            check_plugin.check_function(
                item=item,
                params={
                    "levels": (70.0, 90.0),
                },
                section=string_table,
            )
        )
        == expected_result
    )


@pytest.mark.parametrize(
    "string_table, expected_result",
    [
        ([], []),
        (
            _AGENT_OUTPUT,
            [
                TableRow(
                    path=["software", "applications", "oracle", "recovery_area"],
                    key_columns={
                        "sid": "AIMDWHD1",
                    },
                    inventory_columns={
                        "flashback": "300",
                    },
                    status_columns={},
                ),
            ],
        ),
    ],
)
def test_inventory_oracle_recovery_area(
    string_table: StringTable, expected_result: InventoryResult
) -> None:
    assert sort_inventory_result(
        inventory_oracle_recovery_area(string_table)
    ) == sort_inventory_result(expected_result)
