#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import subprocess

import pytest

from tests.testlib import create_linux_test_host
from tests.testlib.site import Site

from cmk.utils import version as cmk_version

from cmk.checkers.discovery._autochecks import _AutochecksSerializer


# Test whether or not registration of check configuration variables works
@pytest.mark.skipif(cmk_version.is_raw_edition(), reason="flaky on raw edition")
def test_test_check_1(request: pytest.FixtureRequest, site: Site) -> None:
    host_name = "check-variables-test-host"

    create_linux_test_host(request, site, host_name)
    site.write_text_file(f"var/check_mk/agent_output/{host_name}", "<<<test_check_1>>>\n1 2\n")

    test_check_path = "local/share/check_mk/checks/test_check_1"

    def cleanup():
        if site.file_exists("etc/check_mk/conf.d/test_check_1.mk"):
            site.delete_file("etc/check_mk/conf.d/test_check_1.mk")

        site.delete_file(test_check_path)

    request.addfinalizer(cleanup)

    site.write_text_file(
        test_check_path,
        """

test_check_1_default_levels = 10.0, 20.0

def inventory(info):
    return [(None, "test_check_1_default_levels")]

def check(item, params, info):
    return 0, "OK - %r" % (test_check_1_default_levels, )

check_info["test_check_1"] = {
    "check_function"          : check,
    "inventory_function"      : inventory,
    "service_description"     : "Testcheck 1",
#    "default_levels_variable" : "test_check_1_default_levels"
}
""",
    )

    site.activate_changes_and_wait_for_core_reload()
    site.openapi.discover_services_and_wait_for_completion(host_name)

    # Verify that the discovery worked as expected
    entries = _AutochecksSerializer().deserialize(
        site.read_file(f"var/check_mk/autochecks/{host_name}.mk").encode("utf-8")
    )
    assert str(entries[0].check_plugin_name) == "test_check_1"
    assert entries[0].item is None
    assert entries[0].parameters == (10.0, 20.0)
    assert entries[0].service_labels == {}

    # Now execute the check function to verify the variable is available
    p = site.execute(["cmk", "-nv", host_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    assert "OK - (10.0, 20.0)" in stdout
    assert stderr == ""
    assert p.returncode == 0


# Test whether or not registration of discovery variables work
@pytest.mark.skipif(cmk_version.is_raw_edition(), reason="flaky on raw edition")
def test_test_check_2(request: pytest.FixtureRequest, site: Site) -> None:
    host_name = "check-variables-test-host"

    create_linux_test_host(request, site, host_name)
    site.write_text_file(f"var/check_mk/agent_output/{host_name}", "<<<test_check_2>>>\n1 2\n")

    test_check_path = "local/share/check_mk/checks/test_check_2"

    def cleanup():
        if site.file_exists("etc/check_mk/conf.d/test_check_2.mk"):
            site.delete_file("etc/check_mk/conf.d/test_check_2.mk")

        site.delete_file(test_check_path)

    request.addfinalizer(cleanup)

    site.write_text_file(
        test_check_path,
        """

discover_service = False

def inventory(info):
    if discover_service:
        return [(None, {})]

def check(item, params, info):
    return 0, "OK, discovered!"

check_info["test_check_2"] = {
    "check_function"      : check,
    "inventory_function"  : inventory,
    "service_description" : "Testcheck 2",
}
""",
    )

    site.activate_changes_and_wait_for_core_reload()
    site.openapi.discover_services_and_wait_for_completion(host_name)

    # Should have discovered nothing so far
    entries = _AutochecksSerializer().deserialize(
        site.read_file(f"var/check_mk/autochecks/{host_name}.mk").encode("utf-8")
    )
    assert entries == []

    site.openapi.discover_services_and_wait_for_completion(host_name)


# Test whether or not factory settings and checkgroup parameters work
@pytest.mark.skipif(cmk_version.is_raw_edition(), reason="flaky on raw edition")
def test_check_factory_settings(request: pytest.FixtureRequest, site: Site) -> None:
    host_name = "check-variables-test-host"

    create_linux_test_host(request, site, host_name)
    site.write_text_file(f"var/check_mk/agent_output/{host_name}", "<<<test_check_3>>>\n1 2\n")

    test_check_path = "local/share/check_mk/checks/test_check_3"

    def cleanup():
        if site.file_exists("etc/check_mk/conf.d/test_check_3.mk"):
            site.delete_file("etc/check_mk/conf.d/test_check_3.mk")

        site.delete_file(test_check_path)

    request.addfinalizer(cleanup)

    site.write_text_file(
        test_check_path,
        """

factory_settings["test_check_3_default_levels"] = {
    "param1": 123,
}

def inventory(info):
    return [(None, {})]

def check(item, params, info):
    return 0, "OK - %r" % (params, )

check_info["test_check_3"] = {
    "check_function"          : check,
    "inventory_function"      : inventory,
    "service_description"     : "Testcheck 3",
    "group"                   : "asd",
    "default_levels_variable" : "test_check_3_default_levels",
}
""",
    )

    site.activate_changes_and_wait_for_core_reload()
    site.openapi.discover_services_and_wait_for_completion(host_name)

    # Verify that the discovery worked as expected
    entries = _AutochecksSerializer().deserialize(
        site.read_file(f"var/check_mk/autochecks/{host_name}.mk").encode("utf-8")
    )
    assert str(entries[0].check_plugin_name) == "test_check_3"
    assert entries[0].item is None
    assert entries[0].parameters == {}
    assert entries[0].service_labels == {}

    # Now execute the check function to verify the variable is available
    p = site.execute(["cmk", "-nv", host_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    assert "OK - {'param1': 123}\n" in stdout, stdout
    assert stderr == ""
    assert p.returncode == 0

    # And now overwrite the setting in the config
    site.write_text_file(
        "etc/check_mk/conf.d/test_check_3.mk",
        """
checkgroup_parameters.setdefault('asd', [])

checkgroup_parameters['asd'] = [
    {'condition': {}, 'options': {}, 'value': {'param2': 'xxx'}},
] + checkgroup_parameters['asd']
""",
    )

    # And execute the check again to check for the parameters
    p = site.execute(["cmk", "-nv", host_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    assert "'param1': 123" in stdout
    assert "'param2': 'xxx'" in stdout
    assert stderr == ""
    assert p.returncode == 0
