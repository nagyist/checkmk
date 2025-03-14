#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# pylint: disable=redefined-outer-name

from collections.abc import Callable, Mapping, Sequence
from typing import Literal

import pytest

from cmk.special_agents.agent_aws import (
    AWSConfig,
    NamingConvention,
    OverallTags,
    ResultDistributor,
    WAFV2Limits,
    WAFV2Summary,
    WAFV2WebACL,
)

from .agent_aws_fake_clients import (
    FakeCloudwatchClient,
    WAFV2GetWebACLIB,
    WAFV2ListOperationIB,
    WAFV2ListTagsForResourceIB,
)


class FakeWAFV2Client:
    def __init__(self) -> None:
        self._web_acls = WAFV2GetWebACLIB.create_instances(amount=3)

    def list_web_acls(self, Scope=None):
        return {"WebACLs": WAFV2ListOperationIB.create_instances(amount=3)}

    def list_rule_groups(self, Scope=None):
        return {"RuleGroups": WAFV2ListOperationIB.create_instances(amount=4)}

    def list_ip_sets(self, Scope=None):
        return {"IPSets": WAFV2ListOperationIB.create_instances(amount=5)}

    def list_regex_pattern_sets(self, Scope=None):
        return {"RegexPatternSets": WAFV2ListOperationIB.create_instances(amount=6)}

    def get_web_acl(self, Name=None, Scope=None, Id=None):
        idx = int(Name[-1])
        return {"WebACL": self._web_acls[idx], "LockToken": "string"}

    def list_tags_for_resource(self, ResourceARN=None):
        if ResourceARN == "ARN-2":  # the third Web ACL has no tags
            tags = {}
        else:
            tags = WAFV2ListTagsForResourceIB.create_instances(amount=1)[0]
        return {"TagInfoForResource": tags, "NextMarker": "string"}


Wafv2Sections = Mapping[str, WAFV2Limits | WAFV2Summary | WAFV2WebACL]


def create_sections(names: object | None, tags: OverallTags, is_regional: bool) -> Wafv2Sections:
    region = "region" if is_regional else "us-east-1"
    scope: Literal["REGIONAL", "CLOUDFRONT"] = "REGIONAL" if is_regional else "CLOUDFRONT"

    config = AWSConfig("hostname", [], ([], []), NamingConvention.ip_region_instance)
    config.add_single_service_config("wafv2_names", names)
    config.add_service_tags("wafv2_tags", tags)

    fake_wafv2_client = FakeWAFV2Client()
    fake_cloudwatch_client = FakeCloudwatchClient()

    distributor = ResultDistributor()

    # TODO: FakeWAFV2Client shoud actually subclass WAFV2Client.
    wafv2_limits = WAFV2Limits(fake_wafv2_client, region, config, scope, distributor=distributor)  # type: ignore[arg-type]
    wafv2_summary = WAFV2Summary(fake_wafv2_client, region, config, scope, distributor=distributor)  # type: ignore[arg-type]
    wafv2_web_acl = WAFV2WebACL(fake_cloudwatch_client, region, config, is_regional)  # type: ignore[arg-type]

    distributor.add(wafv2_limits.name, wafv2_summary)
    distributor.add(wafv2_summary.name, wafv2_web_acl)

    return {
        "wafv2_limits": wafv2_limits,
        "wafv2_summary": wafv2_summary,
        "wafv2_web_acl": wafv2_web_acl,
    }


CreateWafv2Sections = Callable[[object | None, OverallTags], tuple[Wafv2Sections, Wafv2Sections]]


@pytest.fixture()
def get_wafv2_sections() -> CreateWafv2Sections:
    def _create_wafv2_sections(names, tags):
        return create_sections(names, tags, True), create_sections(names, tags, False)

    return _create_wafv2_sections


wafv2_params = [
    (
        None,
        (None, None),
        ["Name-0", "Name-1", "Name-2"],
    ),
    (
        None,
        ([["FOO"]], [["BAR"]]),
        [],
    ),
    (
        None,
        ([["Key-0"]], [["Value-0"]]),
        ["Name-0", "Name-1"],
    ),
    (
        None,
        ([["Key-0", "Foo"]], [["Value-0", "Bar"]]),
        ["Name-0", "Name-1"],
    ),
    (
        ["Name-0"],
        (None, None),
        ["Name-0"],
    ),
    (
        ["Name-0", "Foobar"],
        (None, None),
        ["Name-0"],
    ),
    (
        ["Name-0", "Name-1"],
        (None, None),
        ["Name-0", "Name-1"],
    ),
    (
        ["Name-0", "Name-2"],
        ([["FOO"]], [["BAR"]]),
        ["Name-0", "Name-2"],
    ),
]


def test_agent_aws_wafv2_regional_cloudfront() -> None:
    config = AWSConfig("hostname", [], ([], []), NamingConvention.ip_region_instance)

    region = "region"
    # TODO: This is plainly wrong, the client can't be None.
    wafv2_limits_regional = WAFV2Limits(None, region, config, "REGIONAL")  # type: ignore[arg-type]
    assert wafv2_limits_regional._region_report == region

    wafv2_limits_regional = WAFV2Limits(None, "us-east-1", config, "CLOUDFRONT")  # type: ignore[arg-type]
    assert wafv2_limits_regional._region_report == "CloudFront"

    with pytest.raises(AssertionError):
        WAFV2Limits(None, "region", config, "CLOUDFRONT")  # type: ignore[arg-type]
        WAFV2Limits(None, "region", config, "WRONG")  # type: ignore[arg-type]
        WAFV2WebACL(None, "region", config, False)  # type: ignore[arg-type]

    assert len(WAFV2WebACL(None, "region", config, True)._metric_dimensions) == 3  # type: ignore[arg-type]
    assert len(WAFV2WebACL(None, "us-east-1", config, False)._metric_dimensions) == 2  # type: ignore[arg-type]


def _test_limits(wafv2_sections):
    wafv2_limits = wafv2_sections["wafv2_limits"]
    wafv2_limits_results = wafv2_limits.run().results

    assert wafv2_limits.cache_interval == 300
    assert wafv2_limits.period == 600
    assert wafv2_limits.name == "wafv2_limits"

    for result in wafv2_limits_results:
        if result.piggyback_hostname == "":
            assert len(result.content) == 4
        else:
            assert len(result.content) == 1


@pytest.mark.parametrize("names,tags,found_instances", wafv2_params)
def test_agent_aws_wafv2_limits(
    get_wafv2_sections: CreateWafv2Sections,
    names: Sequence[str] | None,
    tags: OverallTags,
    found_instances: Sequence[str],
) -> None:
    for wafv2_sections in get_wafv2_sections(names, tags):
        _test_limits(wafv2_sections)


def _test_summary(wafv2_summary, found_instances):
    wafv2_summary_results = wafv2_summary.run().results

    assert wafv2_summary.cache_interval == 300
    assert wafv2_summary.period == 600
    assert wafv2_summary.name == "wafv2_summary"

    if found_instances:
        assert len(wafv2_summary_results) == 1
        wafv2_summary_results = wafv2_summary_results[0]
        assert wafv2_summary_results.piggyback_hostname == ""
        assert len(wafv2_summary_results.content) == len(found_instances)

    else:
        assert len(wafv2_summary_results) == 0


@pytest.mark.parametrize("names,tags,found_instances", wafv2_params)
def test_agent_aws_wafv2_summary_w_limits(
    get_wafv2_sections: CreateWafv2Sections,
    names: Sequence[str] | None,
    tags: OverallTags,
    found_instances: Sequence[str],
) -> None:
    for wafv2_sections in get_wafv2_sections(names, tags):
        _wafv2_limits_results = wafv2_sections["wafv2_limits"].run().results
        _test_summary(wafv2_sections["wafv2_summary"], found_instances)


@pytest.mark.parametrize("names,tags,found_instances", wafv2_params)
def test_agent_aws_wafv2_summary_wo_limits(
    get_wafv2_sections: CreateWafv2Sections,
    names: Sequence[str] | None,
    tags: OverallTags,
    found_instances: Sequence[str],
) -> None:
    for wafv2_sections in get_wafv2_sections(names, tags):
        _test_summary(wafv2_sections["wafv2_summary"], found_instances)


def _test_web_acl(wafv2_sections, found_instances):
    _wafv2_summary_results = wafv2_sections["wafv2_summary"].run().results
    wafv2_web_acl = wafv2_sections["wafv2_web_acl"]
    wafv2_web_acl_results = wafv2_web_acl.run().results

    assert wafv2_web_acl.cache_interval == 300
    assert wafv2_web_acl.period == 600
    assert wafv2_web_acl.name == "wafv2_web_acl"
    assert len(wafv2_web_acl_results) == len(found_instances)

    for result in wafv2_web_acl_results:
        assert result.piggyback_hostname != ""
        assert len(result.content) == 2


@pytest.mark.parametrize("names,tags,found_instances", wafv2_params)
def test_agent_aws_wafv2_web_acls_w_limits(
    get_wafv2_sections: CreateWafv2Sections,
    names: Sequence[str] | None,
    tags: OverallTags,
    found_instances: Sequence[str],
) -> None:
    for wafv2_sections in get_wafv2_sections(names, tags):
        _wafv2_limits_results = wafv2_sections["wafv2_limits"].run().results
        _test_web_acl(wafv2_sections, found_instances)


@pytest.mark.parametrize("names,tags,found_instances", wafv2_params)
def test_agent_aws_wafv2_web_acls_wo_limits(
    get_wafv2_sections: CreateWafv2Sections,
    names: Sequence[str] | None,
    tags: OverallTags,
    found_instances: Sequence[str],
) -> None:
    for wafv2_sections in get_wafv2_sections(names, tags):
        _test_web_acl(wafv2_sections, found_instances)
