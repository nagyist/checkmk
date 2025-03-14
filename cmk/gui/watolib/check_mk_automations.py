#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple, TypeVar

from livestatus import SiteId

from cmk.utils.diagnostics import DiagnosticsCLParameters
from cmk.utils.exceptions import MKGeneralException
from cmk.utils.type_defs import HostName, ServiceName

from cmk.automations import results
from cmk.automations.results import DiscoveredHostLabelsDict, SetAutochecksTable

from cmk.gui.i18n import _
from cmk.gui.site_config import site_is_local
from cmk.gui.watolib.activate_changes import sync_changes_before_remote_automation
from cmk.gui.watolib.automations import (
    check_mk_local_automation_serialized,
    check_mk_remote_automation_serialized,
    local_automation_failure,
    MKAutomationException,
)


class AutomationResponse(NamedTuple):
    command: str
    serialized_result: results.SerializedResult
    local: bool
    cmdline: Iterable[str]


def _automation_serialized(
    command: str,
    *,
    siteid: SiteId | None = None,
    args: Sequence[str] | None = None,
    indata: Any = "",
    stdin_data: str | None = None,
    timeout: int | None = None,
    sync: bool = True,
    non_blocking_http: bool = False,
) -> AutomationResponse:
    if args is None:
        args = []

    if not siteid or site_is_local(siteid):
        cmdline, serialized_result = check_mk_local_automation_serialized(
            command=command,
            args=args,
            indata=indata,
            stdin_data=stdin_data,
            timeout=timeout,
        )
        return AutomationResponse(
            command=command,
            serialized_result=serialized_result,
            local=True,
            cmdline=cmdline,
        )

    return AutomationResponse(
        command=command,
        serialized_result=check_mk_remote_automation_serialized(
            site_id=siteid,
            command=command,
            args=args,
            indata=indata,
            stdin_data=stdin_data,
            timeout=timeout,
            sync=sync_changes_before_remote_automation if sync else lambda site_id: None,
            non_blocking_http=non_blocking_http,
        ),
        local=False,
        cmdline=[],
    )


def _automation_failure(
    response: AutomationResponse,
    exception: SyntaxError,
) -> MKGeneralException:
    if response.local:
        return local_automation_failure(
            command=response.command,
            cmdline=response.cmdline,
            out=response.serialized_result,
            exc=exception,
        )
    return MKAutomationException(
        "%s: <pre>%s</pre>"
        % (
            _("Got invalid data"),
            response.serialized_result,
        )
    )


_ResultType = TypeVar("_ResultType", bound=results.ABCAutomationResult)


def _deserialize(
    response: AutomationResponse,
    result_type: type[_ResultType],
) -> _ResultType:
    try:
        return result_type.deserialize(response.serialized_result)
    except SyntaxError as excpt:
        raise _automation_failure(
            response,
            excpt,
        )


def discovery(
    site_id: SiteId,
    mode: str,
    host_names: Iterable[HostName],
    *,
    scan: bool,
    raise_errors: bool,
    timeout: int | None = None,
    non_blocking_http: bool = False,
) -> results.ServiceDiscoveryResult:
    return _deserialize(
        _automation_serialized(
            "service-discovery",
            siteid=site_id,
            args=[
                *(("@scan",) if scan else ()),
                *(("@raiseerrors",) if raise_errors else ()),
                mode,
                *host_names,
            ],
            timeout=timeout,
            non_blocking_http=non_blocking_http,
        ),
        results.ServiceDiscoveryResult,
    )


def discovery_preview(
    site_id: SiteId,
    host_name: HostName,
    *,
    prevent_fetching: bool,
    raise_errors: bool,
) -> results.ServiceDiscoveryPreviewResult:
    return _deserialize(
        _automation_serialized(
            "service-discovery-preview",
            siteid=site_id,
            args=[
                *(("@nofetch",) if prevent_fetching else ()),
                *(("@raiseerrors",) if raise_errors else ()),
                host_name,
            ],
        ),
        results.ServiceDiscoveryPreviewResult,
    )


def set_autochecks(
    site_id: SiteId,
    host_name: HostName,
    checks: SetAutochecksTable,
) -> results.SetAutochecksResult:
    return _deserialize(
        _automation_serialized(
            "set-autochecks",
            siteid=site_id,
            args=[host_name],
            indata=checks,
        ),
        results.SetAutochecksResult,
    )


def update_host_labels(
    site_id: SiteId,
    host_name: HostName,
    host_labels: DiscoveredHostLabelsDict,
) -> results.UpdateHostLabelsResult:
    return _deserialize(
        _automation_serialized(
            "update-host-labels",
            siteid=site_id,
            args=[host_name],
            indata=host_labels,
        ),
        results.UpdateHostLabelsResult,
    )


def rename_hosts(
    site_id: SiteId,
    name_pairs: Sequence[tuple[HostName, HostName]],
) -> results.RenameHostsResult:
    return _deserialize(
        _automation_serialized(
            "rename-hosts",
            siteid=site_id,
            indata=name_pairs,
            non_blocking_http=True,
        ),
        results.RenameHostsResult,
    )


def get_services_labels(
    site_id: SiteId,
    host_name: HostName,
    service_names: Sequence[ServiceName],
) -> results.GetServicesLabelsResult:
    return _deserialize(
        _automation_serialized(
            "get-services-labels",
            siteid=site_id,
            args=[host_name, *service_names],
        ),
        results.GetServicesLabelsResult,
    )


def analyse_service(
    site_id: SiteId,
    host_name: HostName,
    service_name: ServiceName,
) -> results.AnalyseServiceResult:
    return _deserialize(
        _automation_serialized(
            "analyse-service",
            siteid=site_id,
            args=[host_name, service_name],
        ),
        results.AnalyseServiceResult,
    )


def analyse_host(
    site_id: SiteId,
    host_name: HostName,
) -> results.AnalyseHostResult:
    return _deserialize(
        _automation_serialized(
            "analyse-host",
            siteid=site_id,
            args=[host_name],
        ),
        results.AnalyseHostResult,
    )


def delete_hosts(
    site_id: SiteId,
    host_names: Sequence[HostName],
) -> results.DeleteHostsResult:
    return _deserialize(
        _automation_serialized(
            "delete-hosts",
            siteid=site_id,
            args=host_names,
        ),
        results.DeleteHostsResult,
    )


def restart(hosts_to_update: list[HostName] | None = None) -> results.RestartResult:
    return _deserialize(
        _automation_serialized("restart", args=hosts_to_update),
        results.RestartResult,
    )


def reload(hosts_to_update: list[HostName] | None = None) -> results.ReloadResult:
    return _deserialize(
        _automation_serialized("reload", args=hosts_to_update),
        results.ReloadResult,
    )


def get_configuration(*config_var_names: str) -> results.GetConfigurationResult:
    return _deserialize(
        _automation_serialized(
            "get-configuration",
            indata=list(config_var_names),
        ),
        results.GetConfigurationResult,
    )


def get_check_information() -> results.GetCheckInformationResult:
    return _deserialize(
        _automation_serialized("get-check-information"),
        results.GetCheckInformationResult,
    )


def get_section_information() -> results.GetSectionInformationResult:
    return _deserialize(
        _automation_serialized("get-section-information"),
        results.GetSectionInformationResult,
    )


def scan_parents(
    site_id: SiteId,
    host_name: HostName,
    *params: str,
) -> results.ScanParentsResult:
    return _deserialize(
        _automation_serialized(
            "scan-parents",
            siteid=site_id,
            args=[*params, host_name],
        ),
        results.ScanParentsResult,
    )


def diag_host(
    site_id: SiteId,
    host_name: HostName,
    test: str,
    *args: str,
) -> results.DiagHostResult:
    return _deserialize(
        _automation_serialized(
            "diag-host",
            siteid=site_id,
            args=[host_name, test, *args],
        ),
        results.DiagHostResult,
    )


def active_check(
    site_id: SiteId,
    host_name: HostName,
    check_type: str,
    item: str,
) -> results.ActiveCheckResult:
    return _deserialize(
        _automation_serialized(
            "active-check",
            siteid=site_id,
            args=[host_name, check_type, item],
            sync=False,
        ),
        results.ActiveCheckResult,
    )


def update_dns_cache(site_id: SiteId) -> results.UpdateDNSCacheResult:
    return _deserialize(
        _automation_serialized(
            "update-dns-cache",
            siteid=site_id,
        ),
        results.UpdateDNSCacheResult,
    )


def get_agent_output(
    site_id: SiteId,
    host_name: HostName,
    agent_type: str,
) -> results.GetAgentOutputResult:
    return _deserialize(
        _automation_serialized(
            "get-agent-output",
            siteid=site_id,
            args=[host_name, agent_type],
        ),
        results.GetAgentOutputResult,
    )


def notification_replay(notification_number: int) -> results.NotificationReplayResult:
    return _deserialize(
        _automation_serialized(
            "notification-replay",
            args=[str(notification_number)],
        ),
        results.NotificationReplayResult,
    )


def notification_analyse(notification_number: int) -> results.NotificationAnalyseResult:
    return _deserialize(
        _automation_serialized(
            "notification-analyse",
            args=[str(notification_number)],
        ),
        results.NotificationAnalyseResult,
    )


def notification_get_bulks(only_ripe: bool) -> results.NotificationGetBulksResult:
    return _deserialize(
        _automation_serialized(
            "notification-get-bulks",
            args=[str(int(only_ripe))],
        ),
        results.NotificationGetBulksResult,
    )


def create_diagnostics_dump(
    site_id: SiteId,
    serialized_params: DiagnosticsCLParameters,
    timeout: int,
) -> results.CreateDiagnosticsDumpResult:
    return _deserialize(
        _automation_serialized(
            "create-diagnostics-dump",
            siteid=site_id,
            args=serialized_params,
            timeout=timeout,
            non_blocking_http=True,
        ),
        results.CreateDiagnosticsDumpResult,
    )


def bake_agents(indata: Mapping[str, Any] | None = None) -> results.BakeAgentsResult:
    return _deserialize(
        _automation_serialized(
            "bake-agents",
            indata="" if indata is None else indata,
        ),
        results.BakeAgentsResult,
    )
