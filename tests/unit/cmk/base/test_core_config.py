#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from pytest import MonkeyPatch

from tests.testlib.base import Scenario

import cmk.utils.config_path
import cmk.utils.paths
import cmk.utils.version as cmk_version
from cmk.utils import password_store
from cmk.utils.config_path import ConfigPath, LATEST_CONFIG, VersionedConfigPath
from cmk.utils.exceptions import MKGeneralException
from cmk.utils.labels import Labels
from cmk.utils.parameters import TimespecificParameters
from cmk.utils.rulesets.ruleset_matcher import LabelSources
from cmk.utils.tags import TagGroupID, TagID
from cmk.utils.type_defs import HostName

from cmk.checkers.check_table import ConfiguredService
from cmk.checkers.checking import CheckPluginName

import cmk.base.config as config
import cmk.base.core_config as core_config
import cmk.base.nagios_utils
from cmk.base.config import ConfigCache, ObjectAttributes
from cmk.base.core_config import (
    CollectedHostLabels,
    get_labels_from_attributes,
    read_notify_host_file,
    write_notify_host_file,
)
from cmk.base.core_factory import create_core


@pytest.fixture(name="config_path")
def fixture_config_path():
    ConfigPath.ROOT.mkdir(parents=True, exist_ok=True)
    try:
        yield ConfigPath.ROOT
    finally:
        shutil.rmtree(ConfigPath.ROOT)


def test_do_create_config_nagios(core_scenario: ConfigCache) -> None:
    core_config.do_create_config(create_core("nagios"), core_scenario, duplicates=())

    assert Path(cmk.utils.paths.nagios_objects_file).exists()
    assert config.PackedConfigStore.from_serial(LATEST_CONFIG).path.exists()


def test_commandline_arguments_basics() -> None:
    assert (
        core_config.commandline_arguments(HostName("bla"), "blub", "args 123 -x 1 -y 2")
        == "args 123 -x 1 -y 2"
    )

    assert (
        core_config.commandline_arguments(
            HostName("bla"), "blub", ["args", "1; echo", "-x", "1", "-y", "2"]
        )
        == "args '1; echo' -x 1 -y 2"
    )

    assert (
        core_config.commandline_arguments(
            HostName("bla"), "blub", ["args", "1 2 3", "-d=2", "--hallo=eins", 9]
        )
        == "args '1 2 3' -d=2 --hallo=eins 9"
    )

    with pytest.raises(MKGeneralException):
        core_config.commandline_arguments(HostName("bla"), "blub", (1, 2))


@pytest.mark.parametrize("pw", ["abc", "123", "x'äd!?", "aädg"])
def test_commandline_arguments_password_store(pw: str) -> None:
    password_store.save({"pw-id": pw})
    assert core_config.commandline_arguments(
        HostName("bla"), "blub", ["arg1", ("store", "pw-id", "--password=%s"), "arg3"]
    ) == "--pwstore=2@11@pw-id arg1 '--password=%s' arg3" % ("*" * len(pw))


def test_commandline_arguments_not_existing_password(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        core_config.commandline_arguments(
            HostName("bla"), "blub", ["arg1", ("store", "pw-id", "--password=%s"), "arg3"]
        )
        == "--pwstore=2@11@pw-id arg1 '--password=***' arg3"
    )
    stderr = capsys.readouterr().err
    assert 'The stored password "pw-id" used by service "blub" on host "bla"' in stderr


def test_commandline_arguments_wrong_types() -> None:
    with pytest.raises(MKGeneralException):
        core_config.commandline_arguments(HostName("bla"), "blub", 1)  # type: ignore[arg-type]

    with pytest.raises(MKGeneralException):
        core_config.commandline_arguments(HostName("bla"), "blub", (1, 2))


def test_commandline_arguments_str() -> None:
    assert (
        core_config.commandline_arguments(HostName("bla"), "blub", "args 123 -x 1 -y 2")
        == "args 123 -x 1 -y 2"
    )


def test_commandline_arguments_list() -> None:
    assert core_config.commandline_arguments(HostName("bla"), "blub", ["a", "123"]) == "a 123"


def test_commandline_arguments_list_with_numbers() -> None:
    assert core_config.commandline_arguments(HostName("bla"), "blub", [1, 1.2]) == "1 1.2"


def test_commandline_arguments_list_with_pwstore_reference() -> None:
    assert (
        core_config.commandline_arguments(
            HostName("bla"), "blub", ["a", ("store", "pw1", "--password=%s")]
        )
        == "--pwstore=2@11@pw1 a '--password=***'"
    )


def test_commandline_arguments_list_with_invalid_type() -> None:
    with pytest.raises(MKGeneralException):
        core_config.commandline_arguments(
            HostName("bla"), "blub", [None]  # type: ignore[list-item]
        )


def test_get_host_attributes(monkeypatch: MonkeyPatch) -> None:
    ts = Scenario()
    ts.add_host(HostName("test-host"), tags={TagGroupID("agent"): TagID("no-agent")})
    ts.set_option(
        "host_labels",
        {
            "test-host": {
                "ding": "dong",
            },
        },
    )
    config_cache = ts.apply(monkeypatch)

    expected_attrs = {
        "_ADDRESSES_4": "",
        "_ADDRESSES_6": "",
        "_ADDRESS_4": "0.0.0.0",
        "_ADDRESS_6": "",
        "_ADDRESS_FAMILY": "4",
        "_FILENAME": "/wato/hosts.mk",
        "_TAGS": "/wato/ auto-piggyback ip-v4 ip-v4-only lan no-agent no-snmp prod site:unit",
        "__TAG_address_family": "ip-v4-only",
        "__TAG_agent": "no-agent",
        "__TAG_criticality": "prod",
        "__TAG_ip-v4": "ip-v4",
        "__TAG_networking": "lan",
        "__TAG_piggyback": "auto-piggyback",
        "__TAG_site": "unit",
        "__TAG_snmp_ds": "no-snmp",
        "__LABEL_ding": "dong",
        "__LABEL_cmk/site": "NO_SITE",
        "__LABELSOURCE_cmk/site": "discovered",
        "__LABELSOURCE_ding": "explicit",
        "address": "0.0.0.0",
        "alias": "test-host",
    }

    if cmk_version.is_managed_edition():
        expected_attrs["_CUSTOMER"] = "provider"

    assert config_cache.get_host_attributes(HostName("test-host")) == expected_attrs


@pytest.mark.usefixtures("fix_register")
@pytest.mark.parametrize(
    "hostname,result",
    [
        (
            HostName("localhost"),
            {
                "check_interval": 1.0,
                "contact_groups": "ding",
            },
        ),
        (HostName("blub"), {"check_interval": 40.0}),
    ],
)
def test_get_cmk_passive_service_attributes(
    monkeypatch: pytest.MonkeyPatch, hostname: HostName, result: ObjectAttributes
) -> None:
    ts = Scenario()
    ts.add_host(HostName("localhost"))
    ts.add_host(HostName("blub"))
    ts.set_option(
        "extra_service_conf",
        {
            "contact_groups": [
                {
                    "condition": {
                        "service_description": [{"$regex": "CPU load$"}],
                        "host_name": ["localhost"],
                    },
                    "options": {},
                    "value": "ding",
                },
            ],
            "check_interval": [
                {
                    "condition": {
                        "service_description": [{"$regex": "Check_MK$"}],
                        "host_name": ["blub"],
                    },
                    "options": {},
                    "value": 40.0,
                },
                {
                    "condition": {
                        "service_description": [{"$regex": "CPU load$"}],
                        "host_name": ["localhost"],
                    },
                    "options": {},
                    "value": 33.0,
                },
            ],
        },
    )
    config_cache = ts.apply(monkeypatch)
    check_mk_attrs = core_config.get_service_attributes(hostname, "Check_MK", config_cache)

    service = ConfiguredService(
        check_plugin_name=CheckPluginName("cpu_loads"),
        item=None,
        description="CPU load",
        parameters=TimespecificParameters(),
        discovered_parameters={},
        service_labels={},
        is_enforced=False,
    )
    service_spec = core_config.get_cmk_passive_service_attributes(
        config_cache, hostname, service, check_mk_attrs
    )
    assert service_spec == result


@pytest.mark.parametrize(
    "tag_groups,result",
    [
        (
            {
                "tg1": "val1",
                "tg2": "val1",
            },
            {
                "__TAG_tg1": "val1",
                "__TAG_tg2": "val1",
            },
        ),
        (
            {"täg-113232_eybc": "äbcdef"},
            {
                "__TAG_täg-113232_eybc": "äbcdef",
            },
        ),
        (
            {"a.d B/E u-f N_A": "a.d B/E u-f N_A"},
            {
                "__TAG_a.d B/E u-f N_A": "a.d B/E u-f N_A",
            },
        ),
    ],
)
def test_get_tag_attributes(
    tag_groups: Mapping[TagGroupID, TagID] | Labels | LabelSources, result: ObjectAttributes
) -> None:
    attributes = ConfigCache._get_tag_attributes(tag_groups, "TAG")
    assert attributes == result
    for k, v in attributes.items():
        assert isinstance(k, str)
        assert isinstance(v, str)


@pytest.mark.parametrize(
    "hostname, host_attrs, expected_result",
    [
        (
            "myhost",
            {
                "alias": "my_host_alias",
                "_ADDRESS_4": "127.0.0.1",
                "address": "127.0.0.1",
                "_ADDRESSES_4": "127.0.0.2 127.0.0.3",
                "_ADDRESSES_4_1": "127.0.0.2",
                "_ADDRESSES_4_2": "127.0.0.3",
            },
            core_config.HostAddressConfiguration(
                hostname="myhost",
                host_address="127.0.0.1",
                alias="my_host_alias",
                ipv4address="127.0.0.1",
                ipv6address=None,
                indexed_ipv4addresses={
                    "$_HOSTADDRESSES_4_1$": "127.0.0.2",
                    "$_HOSTADDRESSES_4_2$": "127.0.0.3",
                },
                indexed_ipv6addresses={},
            ),
        )
    ],
)
def test_get_host_config(
    hostname: str,
    host_attrs: config.ObjectAttributes,
    expected_result: core_config.HostAddressConfiguration,
) -> None:
    host_config = core_config._get_host_address_config(hostname, host_attrs)
    assert host_config == expected_result


@pytest.mark.parametrize(
    "check_name, active_check_info, hostname, host_attrs, expected_result",
    [
        pytest.param(
            "my_active_check",
            {
                "my_active_check": {
                    "command_line": "echo $ARG1$",
                    "argument_function": lambda _: "--arg1 arument1 --host_alias $HOSTALIAS$",
                    "service_description": lambda _: "Active check of $HOSTNAME$",
                }
            },
            HostName("myhost"),
            {
                "alias": "my_host_alias",
                "_ADDRESS_4": "127.0.0.1",
                "address": "127.0.0.1",
                "_ADDRESS_FAMILY": "4",
                "display_name": "my_host",
            },
            [("Active check of myhost", "--arg1 arument1 --host_alias $HOSTALIAS$")],
            id="one_active_service",
        ),
        pytest.param(
            "my_active_check",
            {
                "my_active_check": {
                    "command_line": "echo $ARG1$",
                    "service_generator": lambda *_: (
                        yield from [
                            ("First service", "--arg1 argument1"),
                            ("Second service", "--arg2 argument2"),
                        ]
                    ),
                }
            },
            HostName("myhost"),
            {
                "alias": "my_host_alias",
                "_ADDRESS_4": "127.0.0.1",
                "address": "127.0.0.1",
                "_ADDRESS_FAMILY": "4",
                "display_name": "my_host",
            },
            [("First service", "--arg1 argument1"), ("Second service", "--arg2 argument2")],
            id="multiple_active_services",
        ),
    ],
)
def test_iter_active_check_services(
    check_name: str,
    active_check_info: Mapping[str, Mapping[str, str]],
    hostname: HostName,
    host_attrs: dict[str, Any],
    expected_result: Sequence[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "active_check_info", active_check_info)
    monkeypatch.setattr(ConfigCache, "get_host_attributes", lambda e, s: host_attrs)

    cache = config.get_config_cache()
    cache.initialize()

    active_info = active_check_info[check_name]
    services = list(
        core_config.iter_active_check_services(
            check_name, active_info, hostname, host_attrs, {}, password_store.load()
        )
    )
    assert services == expected_result


@pytest.mark.parametrize("ipaddress", [None, "127.0.0.1"])
def test_template_translation(ipaddress: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    template = "<NOTHING>x<IP>x<HOST>x<host>x<ip>x"
    hostname = HostName("testhost")
    ts = Scenario()
    ts.add_host(hostname)
    config_cache = ts.apply(monkeypatch)

    assert config_cache.translate_program_commandline(
        hostname, ipaddress, template
    ) == "<NOTHING>x{}x{}x<host>x<ip>x".format(ipaddress if ipaddress is not None else "", hostname)


# The types between core config and the argument thingies is not shared due to
# a layering violation. Test whether a different type is still handled as valid
# SpecialAgentConfiguration.
class TestSpecialAgentConfiguration(NamedTuple):
    args: Sequence[str]
    stdin: str | None


# Hocus pocus...
fun_args_stdin: tuple[tuple[config.SpecialAgentInfoFunctionResult, tuple[str, str | None]]] = (
    ("arg0 arg;1", "arg0 arg;1", None),
    (["arg0", "arg;1"], "arg0 'arg;1'", None),
    (TestSpecialAgentConfiguration(["arg0"], None), "arg0", None),
    (TestSpecialAgentConfiguration(["arg0", "arg;1"], None), "arg0 'arg;1'", None),
    (TestSpecialAgentConfiguration(["list0", "list1"], None), "list0 list1", None),
    (
        TestSpecialAgentConfiguration(["arg0", "arg;1"], "stdin_blob"),
        "arg0 'arg;1'",
        "stdin_blob",
    ),
    (
        TestSpecialAgentConfiguration(["list0", "list1"], "stdin_blob"),
        "list0 list1",
        "stdin_blob",
    ),
)  # type: ignore[assignment]


class TestMakeSpecialAgentCmdline:
    # ... and more hocus pocus.

    @pytest.fixture(autouse=True)
    def agent_dir(self, monkeypatch):
        dir_ = Path("/tmp")
        monkeypatch.setattr(cmk.utils.paths, "local_agents_dir", dir_)
        monkeypatch.setattr(cmk.utils.paths, "agents_dir", dir_)
        return dir_

    @pytest.fixture
    def agentname(self):
        return "my_id"

    @pytest.fixture(params=fun_args_stdin)
    def patch_config(self, agentname, monkeypatch, request):
        fun, args, stdin = request.param
        monkeypatch.setitem(
            config.special_agent_info,
            agentname,
            lambda a, b, c: fun,
        )
        return args, stdin

    @pytest.fixture
    def expected_args(self, patch_config):
        return patch_config[0]

    @pytest.fixture
    def expected_stdin(self, patch_config):
        return patch_config[1]

    @pytest.mark.parametrize("ipaddress", [None, "127.0.0.1"])
    def test_make_special_agent_cmdline(
        self,
        agentname,
        ipaddress,
        agent_dir,
        expected_args,
        monkeypatch,
    ):
        hostname = HostName("testhost")
        params: dict[Any, Any] = {}
        ts = Scenario()
        ts.add_host(hostname)
        ts.apply(monkeypatch)

        # end of setup

        assert core_config.make_special_agent_cmdline(hostname, ipaddress, agentname, params) == (
            str(agent_dir / "special" / ("agent_%s" % agentname)) + " " + expected_args
        )


@pytest.mark.parametrize(
    "attributes, expected",
    [
        pytest.param(
            {
                "_ADDRESSES_4": "",
                "_ADDRESSES_6": "",
                "__TAG_piggyback": "auto-piggyback",
                "__TAG_site": "unit",
                "__TAG_snmp_ds": "no-snmp",
                "__LABEL_ding": "dong",
                "__LABEL_cmk/site": "NO_SITE",
                "__LABELSOURCE_cmk/site": "discovered",
                "__LABELSOURCE_ding": "explicit",
                "address": "0.0.0.0",
                "alias": "test-host",
            },
            {
                "cmk/site": "NO_SITE",
                "ding": "dong",
            },
        ),
    ],
)
def test_get_labels_from_attributes(attributes: dict[str, str], expected: Labels) -> None:
    assert get_labels_from_attributes(list(attributes.items())) == expected


@pytest.mark.parametrize(
    "versioned_config_path, host_name, host_labels, expected",
    [
        pytest.param(
            VersionedConfigPath(1),
            "horsthost",
            CollectedHostLabels(
                host_labels={"owe": "owe"},
                service_labels={
                    "svc": {"lbl": "blub"},
                    "svc2": {},
                },
            ),
            CollectedHostLabels(
                host_labels={"owe": "owe"},
                service_labels={"svc": {"lbl": "blub"}},
            ),
        )
    ],
)
def test_write_and_read_notify_host_file(
    versioned_config_path: VersionedConfigPath,
    host_name: HostName,
    host_labels: CollectedHostLabels,
    expected: CollectedHostLabels,
    monkeypatch: MonkeyPatch,
) -> None:
    notify_labels_path: Path = Path(versioned_config_path) / "notify" / "labels"
    monkeypatch.setattr(
        cmk.base.core_config,
        "_get_host_file_path",
        lambda config_path: notify_labels_path,
    )

    write_notify_host_file(
        versioned_config_path,
        {host_name: host_labels},
    )

    assert notify_labels_path.exists()

    monkeypatch.setattr(
        cmk.base.core_config,
        "_get_host_file_path",
        lambda host_name: notify_labels_path / host_name,
    )
    assert read_notify_host_file(host_name) == expected
