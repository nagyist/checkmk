#!/usr/bin/env python3
# Copyright (C) 2023 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from collections.abc import Callable
from typing import ContextManager

import pytest

from tests.testlib.users import create_and_destroy_user

from cmk.utils.type_defs import UserId

from cmk.gui.config import active_config
from cmk.gui.plugins.metrics.utils import unit_info


def test_temperature_unit_default() -> None:
    assert unit_info["c"]["title"] == "Degree Celsius"


def test_temperature_unit_global_setting() -> None:
    active_config.default_temperature_unit = "fahrenheit"
    assert unit_info["c"]["title"] == "Degree Fahrenheit"


@pytest.mark.parametrize(
    ["user_setting_temperature_unit", "expected_temperature_unit_title"],
    [
        pytest.param(
            "celsius",
            "Degree Celsius",
            id="celsius",
        ),
        pytest.param(
            "fahrenheit",
            "Degree Fahrenheit",
            id="fahrenheit",
        ),
    ],
)
def test_temperature_unit_user_celsius(
    run_as_user: Callable[[UserId], ContextManager[None]],
    user_setting_temperature_unit: str,
    expected_temperature_unit_title: str,
) -> None:
    with create_and_destroy_user(
        username="harald",
        custom_attrs={"temperature_unit": user_setting_temperature_unit},
    ), run_as_user(UserId("harald")):
        assert unit_info["c"]["title"] == expected_temperature_unit_title
