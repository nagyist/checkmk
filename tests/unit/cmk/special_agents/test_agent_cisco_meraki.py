#!/usr/bin/env python3
# Copyright (C) 2022 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from collections.abc import Mapping, Sequence

import pytest

from cmk.special_agents import agent_cisco_meraki


class FakeGetOrganisationIDsCache:
    def get_live_data(self) -> Sequence[str]:
        return ["123", "456", "789"]


class FakeOrganisations:
    def getOrganizations(self) -> Sequence[Mapping]:
        return [
            {"id": "123"},
            {"id": "456"},
        ]

    def getOrganizationLicensesOverview(self, organisation_id: str) -> Mapping | None:
        return None if organisation_id == "789" else {"id": organisation_id}

    def getOrganizationDevices(self, organisation_id: str, total_pages: str) -> Sequence[Mapping]:
        if organisation_id == "123":
            return [
                {"serial": "S123-1", "lanIp": "1.2.3.4", "name": "dev1"},
                {"serial": "S123-2", "lanIp": "1.2.3.5", "name": "dev2"},
            ]

        if organisation_id == "456":
            return [{"serial": "S456", "lanIp": "1.2.3.6"}]

        return []

    def getOrganizationDevicesStatuses(
        self, organisation_id: str, total_pages: str
    ) -> Sequence[Mapping]:
        if organisation_id == "123":
            return [
                {"serial": "S123-1", "lanIp": "1.2.3.4", "name": "dev1", "status": "online"},
                {"serial": "S123-2", "lanIp": "1.2.3.5", "name": "dev2", "status": "online"},
            ]

        if organisation_id == "456":
            return [{"serial": "S456", "lanIp": "1.2.3.6", "name": "dev3", "status": "online"}]

        return []


class FakeSensor:
    def getOrganizationSensorReadingsLatest(
        self, organisation_id: str, total_pages: str
    ) -> Sequence[Mapping]:
        if organisation_id == "123":
            return [
                {"serial": "S123-1", "readings": []},
                {"serial": "S123-2", "readings": []},
            ]

        if organisation_id == "456":
            return [{"serial": "S456", "readings": []}]

        return []


class FakeDashboard:
    organizations = FakeOrganisations()
    sensor = FakeSensor()


@pytest.mark.parametrize(
    "args, expected_output_lines",
    [
        (
            [],
            [
                "<<<cisco_meraki_org_licenses_overview:sep(0)>>>",
                '[{"id": "123"}, {"id": "456"}]',
                "<<<<dev1>>>>",
                "<<<cisco_meraki_org_device_info:sep(0)>>>",
                '[{"lanIp": "1.2.3.4", "name": "dev1", "serial": "S123-1"}]',
                "<<<cisco_meraki_org_device_status:sep(0)>>>",
                '[{"lanIp": "1.2.3.4", "name": "dev1", "serial": "S123-1", "status": "online"}]',
                "<<<cisco_meraki_org_sensor_readings:sep(0)>>>",
                '[{"readings": [], "serial": "S123-1"}]',
                "<<<<>>>>",
                "<<<<dev2>>>>",
                "<<<cisco_meraki_org_device_info:sep(0)>>>",
                '[{"lanIp": "1.2.3.5", "name": "dev2", "serial": "S123-2"}]',
                "<<<cisco_meraki_org_device_status:sep(0)>>>",
                '[{"lanIp": "1.2.3.5", "name": "dev2", "serial": "S123-2", "status": "online"}]',
                "<<<cisco_meraki_org_sensor_readings:sep(0)>>>",
                '[{"readings": [], "serial": "S123-2"}]',
                "<<<<>>>>",
            ],
        ),
        (
            [
                "--sections",
                "licenses-overview",
            ],
            [
                "<<<cisco_meraki_org_licenses_overview:sep(0)>>>",
                '[{"id": "123"}, {"id": "456"}]',
            ],
        ),
        (
            [
                "--sections",
                "device-statuses",
            ],
            [
                "<<<<dev1>>>>",
                "<<<cisco_meraki_org_device_info:sep(0)>>>",
                '[{"lanIp": "1.2.3.4", "name": "dev1", "serial": "S123-1"}]',
                "<<<cisco_meraki_org_device_status:sep(0)>>>",
                '[{"lanIp": "1.2.3.4", "name": "dev1", "serial": "S123-1", "status": "online"}]',
                "<<<<>>>>",
                "<<<<dev2>>>>",
                "<<<cisco_meraki_org_device_info:sep(0)>>>",
                '[{"lanIp": "1.2.3.5", "name": "dev2", "serial": "S123-2"}]',
                "<<<cisco_meraki_org_device_status:sep(0)>>>",
                '[{"lanIp": "1.2.3.5", "name": "dev2", "serial": "S123-2", "status": "online"}]',
                "<<<<>>>>",
            ],
        ),
        (
            [
                "--sections",
                "sensor-readings",
            ],
            [
                "<<<<dev1>>>>",
                "<<<cisco_meraki_org_device_info:sep(0)>>>",
                '[{"lanIp": "1.2.3.4", "name": "dev1", "serial": "S123-1"}]',
                "<<<cisco_meraki_org_sensor_readings:sep(0)>>>",
                '[{"readings": [], "serial": "S123-1"}]',
                "<<<<>>>>",
                "<<<<dev2>>>>",
                "<<<cisco_meraki_org_device_info:sep(0)>>>",
                '[{"lanIp": "1.2.3.5", "name": "dev2", "serial": "S123-2"}]',
                "<<<cisco_meraki_org_sensor_readings:sep(0)>>>",
                '[{"readings": [], "serial": "S123-2"}]',
                "<<<<>>>>",
            ],
        ),
    ],
)
@pytest.mark.parametrize(
    "orgs",
    [
        [],
        [
            "--orgs",
            "123",
            "456",
            "789",
        ],
    ],
)
def test_agent_cisco_meraki_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: Sequence[str],
    expected_output_lines: Sequence[str],
    orgs: Sequence[str],
) -> None:
    monkeypatch.setattr(
        agent_cisco_meraki,
        "_configure_meraki_dashboard",
        lambda a, b, c: FakeDashboard(),
    )
    monkeypatch.setattr(
        agent_cisco_meraki,
        "GetOrganisationIDsCache",
        lambda a: FakeGetOrganisationIDsCache(),
    )

    agent_cisco_meraki.agent_cisco_meraki_main(
        agent_cisco_meraki.parse_arguments(
            [
                "testhost",
                "my-api-key",
            ]
            + list(orgs)
            + list(args)
        )
    )

    captured = capsys.readouterr()
    assert captured.out.rstrip().split("\n") == expected_output_lines
    assert captured.err == ""
