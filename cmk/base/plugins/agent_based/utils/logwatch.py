#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

#########################################################################################
#                                                                                       #
#                                 !!   W A T C H   O U T   !!                           #
#                                                                                       #
#   The logwatch plugin is notorious for being an exception to just about every rule    #
#   or best practice that applies to check plugin development.                          #
#   It is highly discouraged to use this a an example!                                  #
#                                                                                       #
#########################################################################################

import re
from typing import (
    Any,
    Counter,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    TypedDict,
)

from cmk.checkers.plugin_contexts import host_name  # pylint: disable=cmk-module-layer-violation

# from cmk.base.config import logwatch_rule will NOT work!
import cmk.base.config  # pylint: disable=cmk-module-layer-violation

from ..agent_based_api.v1 import regex, Result, State


class ItemData(TypedDict):
    attr: str
    lines: dict[str, list[str]]


class Section(NamedTuple):
    errors: Sequence[str]
    logfiles: Mapping[str, ItemData]


def service_extra_conf(service: str) -> list:
    return cmk.base.config.get_config_cache().service_extra_conf(
        host_name(), service, cmk.base.config.logwatch_rules
    )


def extract_unseen_lines(
    value_store: MutableMapping[str, Any],
    batches_of_lines: Mapping[str, list[str]],
) -> list[str]:
    # Watch out: we cannot write an empty set to the value_store :-(
    seen_batches = value_store.get("seen_batches", ())
    value_store["seen_batches"] = tuple(batches_of_lines)

    return [
        line
        for batch, lines in sorted(batches_of_lines.items())
        if batch not in seen_batches
        for line in lines
    ]


def get_ec_rule_params() -> list:
    """Isolate the remaining API violation w.r.t. parameters"""
    return cmk.base.config.get_config_cache().host_extra_conf(
        host_name(),
        cmk.base.config.checkgroup_parameters.get("logwatch_ec", []),
    )


def discoverable_items(*sections: Section) -> List[str]:
    """only consider files which are 'ok' on at least one node"""
    return sorted(
        {
            item
            for node_data in sections
            for item, item_data in node_data.logfiles.items()
            if item_data["attr"] == "ok"
        }
    )


def ec_forwarding_enabled(params: Mapping[str, Any], item: str) -> bool:
    if "restrict_logfiles" not in params:
        return True  # matches all logs on this host

    # only logs which match the specified patterns
    return any(re.match(pattern, item) for pattern in params["restrict_logfiles"])


def select_forwarded(
    items: Sequence[str],
    forward_settings: Sequence[Mapping[str, Any]],
    *,
    invert: bool = False,
) -> Set[str]:
    # Is forwarding enabled in general?
    if not (forward_settings and forward_settings[0]):
        return set(items) if invert else set()

    return {
        item for item in items if ec_forwarding_enabled(forward_settings[0], item) is not invert
    }


def reclassify(
    counts: Counter[int],
    patterns: Dict[str, Any],  # all I know right now :-(
    text: str,
    old_level: str,
) -> str:
    # Reclassify state if a given regex pattern matches
    # A match overrules the previous state->state reclassification
    for level, pattern, _ in patterns.get("reclassify_patterns", []):
        # not necessary to validate regex: already done by GUI
        reg = regex(pattern, re.UNICODE)
        if reg.search(text):
            # If the level is not fixed like 'C' or 'W' but a pair like (10, 20),
            # then we count how many times this pattern has already matched and
            # assign the levels according to the number of matches of this pattern.
            if isinstance(level, tuple):
                warn, crit = level
                newcount = counts[id(pattern)] + 1
                counts[id(pattern)] = newcount
                if newcount >= crit:
                    return "C"
                return "W" if newcount >= warn else "I"
            return level

    # Reclassify state to another state
    change_state_paramkey = ("%s_to" % old_level).lower()
    return patterns.get("reclassify_states", {}).get(change_state_paramkey, old_level)


def check_errors(cluster_section: Mapping[Optional[str], Section]) -> Iterable[Result]:
    """
    >>> cluster_section = {
    ...     None: Section(errors=["error w/o node info"], logfiles={}),
    ...     "node": Section(errors=["some error"], logfiles={}),
    ... }
    >>> for r in check_errors(cluster_section):
    ...     print((r.state, r.summary))
    (<State.UNKNOWN: 3>, 'error w/o node info')
    (<State.UNKNOWN: 3>, '[node] some error')
    """
    for node, node_data in cluster_section.items():
        for error_msg in node_data.errors:
            yield Result(
                state=State.UNKNOWN,
                summary=error_msg if node is None else "[%s] %s" % (node, error_msg),
            )
