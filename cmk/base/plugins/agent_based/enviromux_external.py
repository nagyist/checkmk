#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from cmk.base.plugins.agent_based.utils.enviromux import (
    check_enviromux_humidity,
    check_enviromux_temperature,
    check_enviromux_voltage,
    DETECT_ENVIROMUX,
    discover_enviromux_humidity,
    discover_enviromux_temperature,
    discover_enviromux_voltage,
    ENVIROMUX_CHECK_DEFAULT_PARAMETERS,
    parse_enviromux,
)

from .agent_based_api.v1 import register, SNMPTree

register.snmp_section(
    name="enviromux_external",
    parse_function=parse_enviromux,
    fetch=SNMPTree(
        base=".1.3.6.1.4.1.3699.1.1.11.1.5.1.1",
        oids=[
            "1",  # extSensorIndex
            "2",  # extSensorType
            "3",  # extSensorDescription
            "7",  # extSensorValue
            "11",  # extSensorMinThreshold
            "12",  # extSensorMaxThreshold
        ],
    ),
    detect=DETECT_ENVIROMUX,
)
# .
#   .--temperature---------------------------------------------------------.
#   |      _                                      _                        |
#   |     | |_ ___ _ __ ___  _ __   ___ _ __ __ _| |_ _   _ _ __ ___       |
#   |     | __/ _ \ '_ ` _ \| '_ \ / _ \ '__/ _` | __| | | | '__/ _ \      |
#   |     | ||  __/ | | | | | |_) |  __/ | | (_| | |_| |_| | | |  __/      |
#   |      \__\___|_| |_| |_| .__/ \___|_|  \__,_|\__|\__,_|_|  \___|      |
#   |                       |_|                                            |
#   +----------------------------------------------------------------------+
#   |                                                                      |
#   '----------------------------------------------------------------------'


register.check_plugin(
    name="enviromux_external",
    service_name="Sensor External %s",
    discovery_function=discover_enviromux_temperature,
    check_function=check_enviromux_temperature,
    check_default_parameters={},
    check_ruleset_name="temperature",
)


# .
#   .--Voltage-------------------------------------------------------------.
#   |                 __     __    _ _                                     |
#   |                 \ \   / /__ | | |_ __ _  __ _  ___                   |
#   |                  \ \ / / _ \| | __/ _` |/ _` |/ _ \                  |
#   |                   \ V / (_) | | || (_| | (_| |  __/                  |
#   |                    \_/ \___/|_|\__\__,_|\__, |\___|                  |
#   |                                         |___/                        |
#   +----------------------------------------------------------------------+
#   |                                                                      |
#   '----------------------------------------------------------------------'
register.check_plugin(
    name="enviromux_external_voltage",
    sections=["enviromux_external"],
    service_name="Sensor External %s",
    discovery_function=discover_enviromux_voltage,
    check_function=check_enviromux_voltage,
    check_default_parameters=ENVIROMUX_CHECK_DEFAULT_PARAMETERS,
    check_ruleset_name="voltage",
)


# .
#   .--Humidity------------------------------------------------------------.
#   |              _   _                 _     _ _ _                       |
#   |             | | | |_   _ _ __ ___ (_) __| (_) |_ _   _               |
#   |             | |_| | | | | '_ ` _ \| |/ _` | | __| | | |              |
#   |             |  _  | |_| | | | | | | | (_| | | |_| |_| |              |
#   |             |_| |_|\__,_|_| |_| |_|_|\__,_|_|\__|\__, |              |
#   |                                                  |___/               |
#   +----------------------------------------------------------------------+
#   |                                                                      |
#   '----------------------------------------------------------------------'

register.check_plugin(
    name="enviromux_external_humidity",
    sections=["enviromux_external"],
    service_name="Sensor External %s",
    discovery_function=discover_enviromux_humidity,
    check_function=check_enviromux_humidity,
    check_default_parameters={},
    check_ruleset_name="humidity",
)
