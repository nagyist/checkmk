#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# fmt: off
# mypy: disable-error-code=var-annotated


checkname = "akcp_exp_humidity"


info = [["Dual Humidity Port 1", "30", "7", "1"]]


discovery = {"": [("Dual Humidity Port 1", (30, 35, 60, 65))]}


checks = {
    "": [
        (
            "Dual Humidity Port 1",
            (30, 35, 60, 65),
            [
                (2, "State: sensor error", []),
                (1, "30.00% (warn/crit below 35.00%/30.00%)", [("humidity", 30, 60, 65, 0, 100)]),
            ],
        )
    ]
}
