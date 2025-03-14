#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import collections
import time
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from cmk.utils.misc import (  # pylint: disable=cmk-module-layer-violation
    is_daily_build_version,
    normalize_ip_addresses,
)
from cmk.utils.type_defs import HostName  # pylint: disable=cmk-module-layer-violation

# The only reasonable thing to do here is use our own version parsing. It's to big to duplicate.
from cmk.utils.version import (  # pylint: disable=cmk-module-layer-violation
    __version__,
    parse_check_mk_version,
)

# We need config and host_name() because the "only_from" configuration is not a check parameter.
# It is configured as an agent bakery rule, and controls the *deployment* of the only_from setting.
# We wan't to use that very setting to check whether it is deployed correctly.
# I currently see no better soluton than this API violation.
from cmk.base.check_api import host_name  # pylint: disable=cmk-module-layer-violation
from cmk.base.config import get_config_cache  # pylint: disable=cmk-module-layer-violation

from .agent_based_api.v1 import check_levels, regex, register, render, Result, Service, State
from .agent_based_api.v1.type_defs import CheckResult, DiscoveryResult
from .utils.checkmk import (
    CheckmkSection,
    CMKAgentUpdateSection,
    ControllerSection,
    Plugin,
    PluginSection,
)


def _get_configured_only_from() -> Union[None, str, list[str]]:
    return get_config_cache().only_from(HostName(host_name()))


def discover_checkmk_agent(
    section_check_mk: Optional[CheckmkSection],
    section_checkmk_agent_plugins: Optional[PluginSection],
    section_cmk_agent_ctl_status: Optional[ControllerSection],
    section_cmk_update_agent_status: Optional[CMKAgentUpdateSection],
) -> DiscoveryResult:
    # If we're called, at least one section is not None, so just disocver.
    yield Service()


def _check_cmk_agent_installation(
    params: Mapping[str, Any],
    agent_info: CheckmkSection,
    controller_info: Optional[ControllerSection],
) -> CheckResult:
    yield from _check_version(
        agent_info.get("version"),
        __version__,
        params["agent_version"],
        State(params["agent_version_missmatch"]),
    )
    if agent_info["agentos"] is not None:
        yield Result(state=State.OK, summary="OS: %s" % agent_info["agentos"])

    yield from _check_transport(
        bool(agent_info.get("sshclient")),
        controller_info,
        State(params["legacy_pull_mode"]),
    )
    yield from _check_only_from(
        agent_info.get("onlyfrom") if controller_info is None else controller_info.ip_allowlist,
        _get_configured_only_from(),
        State(params["restricted_address_mismatch"]),
    )
    yield from _check_agent_update(
        agent_info.get("updatefailed"), agent_info.get("updaterecoveraction")
    )
    yield from _check_python_plugins(
        agent_info.get("failedpythonplugins"), agent_info.get("failedpythonreason")
    )
    yield from _check_encryption_panic(agent_info.get("encryptionpanic"))


def _check_version(
    agent_version: Optional[str],
    site_version: str,
    expected_version: tuple[str, dict[str, str]],
    fail_state: State,
) -> CheckResult:
    if not agent_version:
        return

    rendered_mismatch = _render_agent_version_mismatch(
        agent_version, site_version, *expected_version
    )
    yield Result(
        state=fail_state if rendered_mismatch else State.OK,
        summary=f"Version: {agent_version}{rendered_mismatch}",
    )


def _render_agent_version_mismatch(
    agent_version: str,
    site_version: str,
    spec_type: str,
    spec: dict[str, str],
) -> str:
    if spec_type == "ignore":
        return ""

    if spec_type in ("specific", "site"):
        literal = spec.get("literal", site_version)
        return "" if literal == agent_version else f" (expected {literal})"

    # spec_type == "at_least"
    if is_daily_build_version(agent_version) and (at_least := spec.get("daily_build")) is not None:
        if int(agent_version.split("-")[-1].replace(".", "")) < int(at_least.replace(".", "")):
            return f" (expected at least {at_least})"

    if (at_least := spec.get("release")) is None:
        return ""

    return (
        f" (expected at least {at_least})"
        if is_daily_build_version(agent_version)
        or (parse_check_mk_version(agent_version) < parse_check_mk_version(at_least))
        else ""
    )


def _check_only_from(
    agent_only_from: Union[None, str, Sequence[str]],
    config_only_from: Union[None, str, list[str]],
    fail_state: State,
) -> CheckResult:
    if agent_only_from is None or config_only_from is None:
        return

    # do we really need 'normalize_ip_addresses'? It deals with '{' expansion.
    allowed_nets = set(normalize_ip_addresses(agent_only_from))
    expected_nets = set(normalize_ip_addresses(config_only_from))
    if allowed_nets == expected_nets:
        yield Result(
            state=State.OK,
            notice=f"Allowed IP ranges: {' '.join(allowed_nets)}",
        )
        return

    infotexts = []
    exceeding = allowed_nets - expected_nets
    if exceeding:
        infotexts.append("exceeding: %s" % " ".join(sorted(exceeding)))

    missing = expected_nets - allowed_nets
    if missing:
        infotexts.append("missing: %s" % " ".join(sorted(missing)))

    yield Result(
        state=fail_state,
        summary=f"Unexpected allowed IP ranges ({', '.join(infotexts)})",
    )


def _check_python_plugins(
    agent_failed_plugins: Optional[str],
    agent_fail_reason: Optional[str],
) -> CheckResult:
    if agent_failed_plugins:
        yield Result(
            state=State.WARN,
            summary=f"Failed to execute python plugins: {agent_failed_plugins}"
            + (f" ({agent_fail_reason})" if agent_fail_reason else ""),
        )


def _check_encryption_panic(
    panic: Optional[str],
) -> CheckResult:
    if panic:
        yield Result(
            state=State.CRIT,
            summary="Failed to apply symmetric encryption, aborting communication.",
        )


def _check_agent_update(
    update_fail_reason: Optional[str],
    on_update_fail_action: Optional[str],
) -> CheckResult:
    if update_fail_reason and on_update_fail_action:
        yield Result(state=State.WARN, summary=f"{update_fail_reason} {on_update_fail_action}")


def _check_transport(
    ssh_transport: bool,
    controller_info: Optional[ControllerSection],
    fail_state: State,
) -> CheckResult:
    if ssh_transport:
        yield Result(state=State.OK, summary="Transport via SSH")
        return

    if (
        not controller_info
        or not controller_info.allow_legacy_pull
        or not controller_info.agent_socket_operational
    ):
        return

    yield Result(
        state=fail_state,
        summary="TLS is not activated on monitored host (see details)",
        details=(
            "The hosts agent supports TLS, but it is not being used.\n"
            "We strongly recommend to enable TLS by registering the host to the site "
            "(using the `cmk-agent-ctl register` command on the monitored host).\n"
            "NOTE: A registered host will refuse all unencrypted connections. "
            "If the host is monitored by multiple sites, you must register to all of them. "
            "This can be problematic if you are monitoring the same host from a site running "
            "Checkmk version 2.0 or earlier.\n"
            "If you can not register the host, you can configure missing TLS to be OK in the "
            'setting "State in case of available but not enabled TLS" of the ruleset '
            '"Checkmk Agent installation auditing".'
        ),
    )


def _get_error_result(error: str, params: Mapping[str, Any]) -> CheckResult:
    # Sometimes we get duplicate output. Until we find out why, fix the error message:
    if "last_check" in error and "last_update" in error and "error" in error:
        error = error.split("error", 1)[1].strip()

    if error == "None" or not error:
        return

    default_state = State.WARN
    summary = first_line if (first_line := error.split("\n")[0].strip()) else "See details"
    details = None if summary == error else error  # drop details if same as the summary
    if "deployment is currently globally disabled" in error.lower():
        yield Result(
            state=State(params.get("error_deployment_globally_disabled", default_state)),
            summary=summary,
            details=details,
        )
    elif "agent updates are disabled for hostname" in error.lower():
        yield Result(
            state=State(params.get("error_deployment_disabled_for_hostname", default_state)),
            summary=summary,
            details=details,
        )
    else:
        yield Result(
            state=default_state,
            summary=f"Update error: {summary}",
            details=details,
        )


def _check_cmk_agent_update_certificates(parsed: CMKAgentUpdateSection) -> CheckResult:
    """check the certificate part of the agent updater section

    * Warn if a certificate is corrupt
    * Warn if a certificate is not valid anymore
    * Warn if a certificate is about to become invalid
    * Crit if there is no trusted certificate
    * Warn/Crit if there will be no valid cert in 90/30 days.
    """

    if parsed.trusted_certs is None:
        return

    amount_trusted = 0  # How many trusted certificates are configured
    longest_valid = -1.0  # How long is the longest running certificate valid?
    for number, cert_info in parsed.trusted_certs.items():
        if cert_info.corrupt:
            yield Result(state=State.WARN, notice=f"Updater certificate #{number} is corrupt")
            continue

        assert cert_info.not_after is not None  # It is only None if cert is corrupt

        # We get tz aware datetimes and we must not compare them to naive datetimes
        duration_valid = cert_info.not_after - datetime.now().astimezone()

        if duration_valid.total_seconds() < 0:
            yield Result(
                state=State.WARN,
                notice=f"Updater certificate #{number} (CN={cert_info.common_name!r}) is expired",
            )
        else:
            amount_trusted += 1
            longest_valid = max(longest_valid, duration_valid.total_seconds())
            yield from check_levels(
                duration_valid.total_seconds(),
                levels_lower=(90 * 3600 * 24, None),  # type: ignore[arg-type]
                render_func=render.timespan,
                label=f"Time until updater certificate #{number} (CN={cert_info.common_name!r}) will expire",
                notice_only=True,
            )

    if amount_trusted == 0:
        yield Result(state=State.CRIT, notice="Updater has no trusted certificates")
    else:
        yield from check_levels(
            longest_valid,
            levels_lower=(90 * 3600 * 24, 30 * 3600 * 24),
            render_func=render.timespan,
            label="Time until all updater certificates are expired",
            notice_only=True,
        )


def _check_cmk_agent_update(
    params: Mapping[str, Any],
    section_check_mk: CheckmkSection | None,
    section_cmk_update_agent_status: CMKAgentUpdateSection | None,
) -> CheckResult:
    if (
        section := (
            section_cmk_update_agent_status
            or CMKAgentUpdateSection.parse_checkmk_section(section_check_mk)
        )
    ) is None:
        return

    if section.error is not None:
        yield from _get_error_result(section.error, params)

    if (last_check := section.last_check) is None:
        yield Result(state=State.WARN, summary="No successful connect to server yet")
    else:
        if (age := time.time() - last_check) >= 0:
            yield from check_levels(
                age,
                levels_upper=(2 * 3600 * 24, None),  # type: ignore[arg-type]
                render_func=render.timespan,
                label="Time since last update check",
                notice_only=True,
            )
        else:
            yield Result(
                state=State.OK,
                summary=(
                    f"Last update check appears to be {render.timespan(-age)}"
                    " in the future (check your system time)"
                ),
            )
        yield Result(
            state=State.OK,
            notice=f"Last update check: {render.datetime(last_check)}",
        )

    if last_update := section.last_update:
        yield Result(
            state=State.OK,
            summary=f"Last update: {render.datetime(float(last_update))}",
        )

    if update_url := section.update_url:
        # Note: Transformation of URLs from this check (check_mk-check_mk_agent) to icons
        # is disabled explicitly in cmk.gui.view_utils:format_plugin_output
        yield Result(state=State.OK, notice=f"Update URL: {update_url}")

    if aghash := section.aghash:
        yield Result(state=State.OK, notice=f"Agent configuration: {aghash[:8]}")

    if pending := section.pending_hash:
        yield Result(state=State.OK, notice=f"Pending installation: {pending[:8]}")

    yield from _check_cmk_agent_update_certificates(section)


def _check_plugins(
    params: Mapping[str, Any],
    section: PluginSection,
) -> CheckResult:
    yield Result(
        state=State.OK,
        summary=f"Agent plugins: {len(section.plugins)}",
    )
    yield Result(
        state=State.OK,
        summary=f"Local checks: {len(section.local_checks)}",
    )
    yield from _check_versions_and_duplicates(
        section.plugins,
        params.get("versions_plugins"),
        params.get("exclude_pattern_plugins"),
        "Agent plugin",
    )
    yield from _check_versions_and_duplicates(
        section.local_checks,
        params.get("versions_lchecks"),
        params.get("exclude_pattern_lchecks"),
        "Local check",
    )


def _check_versions_and_duplicates(
    plugins: Iterable[Plugin],
    version_params: Optional[Mapping[str, Any]],
    exclude_pattern: Optional[str],
    type_: str,
) -> CheckResult:
    if exclude_pattern is None:
        plugins = list(plugins)
    else:
        comp = regex(exclude_pattern)
        plugins = [p for p in plugins if not comp.search(p.name)]

    if version_params:
        yield from _check_min_version(
            plugins,
            version_params["min_versions"],
            State(version_params["mon_state_unparsable"]),
            type_,
        )

    yield from _check_duplicates(
        plugins,
        type_,
    )


def _check_min_version(
    plugins: Iterable[Plugin],
    levels_str: tuple[str, str],
    mon_state_unparsable: State,
    type_: str,
) -> CheckResult:
    levels = (parse_check_mk_version(levels_str[0]), parse_check_mk_version(levels_str[1]))

    render_info = {p.version_int: p.version for p in plugins}
    render_info.update(zip(levels, levels_str))

    for plugin in plugins:
        if plugin.version == "unversioned":
            yield Result(
                state=State.OK,
                notice=f"{type_} {plugin.name!r}: no version specified",
            )
        elif plugin.version_int is None:
            yield Result(
                state=mon_state_unparsable,
                summary=f"{type_} {plugin.name!r}: unable to parse version {plugin.version!r}",
            )
        else:
            yield from check_levels(
                plugin.version_int,
                levels_lower=levels,
                render_func=lambda v: render_info[int(v)],
                label=f"{type_} {plugin.name!r}",
                notice_only=True,
            )


def _check_duplicates(
    plugins: Iterable[Plugin],
    type_: str,
) -> CheckResult:
    plugins_by_name: dict[str, list[Plugin]] = collections.defaultdict(list)
    for p in plugins:
        plugins_by_name[p.name].append(p)
    for name, plugins_with_name in plugins_by_name.items():
        if (count := len(plugins_with_name)) > 1:
            yield Result(
                state=State.WARN,
                summary=f"{type_} {name}: found {count} times",
                details="Consult the hardware/software inventory for a complete list of files",
            )


def _check_controller_cert_validity(section: ControllerSection, now: float) -> CheckResult:
    for connection in section.connections:
        yield from check_levels(
            connection.local.cert_info.to.timestamp() - now,
            levels_lower=(30 * 24 * 3600, 15 * 24 * 3600),  # (30 days, 15 days)
            render_func=render.timespan,
            label=(
                f"Time until controller certificate for `{site_id}`, "
                f"issued by `{connection.local.cert_info.issuer}`, expires"
            )
            if (site_id := connection.get_site_id())
            else (
                "Time until controller certificate issued by "
                f"`{connection.local.cert_info.issuer}` (imported connection) expires"
            ),
            notice_only=True,
        )


def check_checkmk_agent(
    params: Mapping[str, Any],
    section_check_mk: Optional[CheckmkSection],
    section_checkmk_agent_plugins: Optional[PluginSection],
    section_cmk_agent_ctl_status: Optional[ControllerSection],
    section_cmk_update_agent_status: Optional[CMKAgentUpdateSection],
) -> CheckResult:
    if section_check_mk is not None:
        yield from _check_cmk_agent_installation(
            params, section_check_mk, section_cmk_agent_ctl_status
        )
    yield from _check_cmk_agent_update(params, section_check_mk, section_cmk_update_agent_status)

    if section_checkmk_agent_plugins is not None:
        yield from _check_plugins(params, section_checkmk_agent_plugins)

    if section_cmk_agent_ctl_status:
        yield from _check_controller_cert_validity(section_cmk_agent_ctl_status, time.time())


register.check_plugin(
    name="checkmk_agent",
    service_name="Check_MK Agent",
    sections=[
        "check_mk",
        "checkmk_agent_plugins",
        "cmk_agent_ctl_status",
        "cmk_update_agent_status",
    ],
    discovery_function=discover_checkmk_agent,
    check_function=check_checkmk_agent,
    # TODO: rename the ruleset?
    check_ruleset_name="agent_update",
    check_default_parameters={
        "agent_version": ("ignore", {}),
        "agent_version_missmatch": 1,
        "restricted_address_mismatch": 1,
        "legacy_pull_mode": 1,
    },
)
