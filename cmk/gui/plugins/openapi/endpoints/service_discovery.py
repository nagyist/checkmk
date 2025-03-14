#!/usr/bin/env python3
# Copyright (C) 2020 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Service discovery

A service discovery is the automatic and reliable detection of all services to be monitored on
a host.

You can find an introduction to services including service discovery in the
[Checkmk guide](https://docs.checkmk.com/latest/en/wato_services.html).
"""
import enum
from collections.abc import Mapping, Sequence
from typing import Any, assert_never

from cmk.automations.results import CheckPreviewEntry

from cmk.gui import fields as gui_fields
from cmk.gui.fields.utils import BaseSchema
from cmk.gui.http import request, Response
from cmk.gui.logged_in import user
from cmk.gui.plugins.openapi.restful_objects import (
    constructors,
    Endpoint,
    permissions,
    response_schemas,
)
from cmk.gui.plugins.openapi.restful_objects.constructors import (
    collection_href,
    domain_object,
    link_rel,
    object_property,
)
from cmk.gui.plugins.openapi.restful_objects.parameters import HOST_NAME
from cmk.gui.plugins.openapi.restful_objects.request_schemas import EXISTING_HOST_NAME
from cmk.gui.plugins.openapi.restful_objects.type_defs import LinkType
from cmk.gui.plugins.openapi.utils import problem, ProblemException, serve_json
from cmk.gui.watolib.bulk_discovery import (
    bulk_discovery_job_status,
    BulkDiscoveryBackgroundJob,
    prepare_hosts_for_discovery,
    start_bulk_discovery,
)
from cmk.gui.watolib.hosts_and_folders import CREHost, Host
from cmk.gui.watolib.services import (
    checkbox_id,
    Discovery,
    DiscoveryAction,
    DiscoveryOptions,
    DiscoveryResult,
    get_check_table,
    has_discovery_action_specific_permissions,
    perform_fix_all,
    perform_host_label_discovery,
    perform_service_discovery,
    ServiceDiscoveryBackgroundJob,
    StartDiscoveryRequest,
)

from cmk import fields

DISCOVERY_PERMISSIONS = permissions.AllPerm(
    [
        permissions.Perm("wato.edit"),
        permissions.Optional(permissions.Perm("wato.service_discovery_to_undecided")),
        permissions.Optional(permissions.Perm("wato.service_discovery_to_monitored")),
        permissions.Optional(permissions.Perm("wato.service_discovery_to_ignored")),
        permissions.Optional(permissions.Perm("wato.service_discovery_to_removed")),
        permissions.Optional(permissions.Perm("wato.services")),
    ]
)

SERVICE_DISCOVERY_PHASES = {
    "undecided": "new",
    "vanished": "vanished",
    "monitored": "old",
    "ignored": "ignored",
    "removed": "removed",
    "manual": "manual",
    "active": "active",
    "custom": "custom",
    "clustered_monitored": "clustered_old",
    "clustered_undecided": "clustered_new",
    "clustered_vanished": "clustered_vanished",
    "clustered_ignored": "clustered_ignored",
    "active_ignored": "active_ignored",
    "custom_ignored": "custom_ignored",
    "legacy": "legacy",
    "legacy_ignored": "legacy_ignored",
}


class APIDiscoveryAction(enum.Enum):
    new = "new"
    remove = "remove"
    fix_all = "fix_all"
    refresh = "refresh"
    only_host_labels = "only_host_labels"
    tabula_rasa = "tabula_rasa"


def _discovery_mode(default_mode: str):  # type: ignore[no-untyped-def]
    return fields.String(
        description="""The mode of the discovery action. The 'refresh' mode starts a new service
        discovery which will contact the host and identify undecided and vanished services and host
        labels. Those services and host labels can be added or removed accordingly with the
        'fix_all' mode. The 'tabula_rasa' mode combines these two procedures. The 'new', 'remove'
        and 'only_host_labels' modes give you more granular control. The corresponding user
        interface option for each discovery mode is shown below.

 * `new` - Monitor undecided services
 * `remove` - Remove vanished services
 * `fix_all` - Accept all
 * `tabula_rasa` - Remove all and find new
 * `refresh` - Rescan
 * `only_host_labels` - Update host labels
    """,
        enum=[a.value for a in APIDiscoveryAction],
        example="refresh",
        load_default=default_mode,
    )


DISCOVERY_ACTION = {
    "new": DiscoveryAction.BULK_UPDATE,
    "remove": DiscoveryAction.BULK_UPDATE,
    "fix_all": DiscoveryAction.FIX_ALL,
    "refresh": DiscoveryAction.REFRESH,
    "only_host_labels": DiscoveryAction.UPDATE_HOST_LABELS,
    "tabula_rasa": DiscoveryAction.TABULA_RASA,
}


@Endpoint(
    constructors.object_href("service_discovery", "{host_name}"),
    "cmk/list",
    method="get",
    response_schema=response_schemas.DomainObject,
    tag_group="Setup",
    path_params=[
        {
            "host_name": gui_fields.HostField(
                description="The host of the service discovery result",
                example="example.com",
                required=True,
            )
        }
    ],
)
def show_service_discovery_result(params: Mapping[str, Any]) -> Response:
    """Show the current service discovery result"""
    host = Host.load_host(params["host_name"])
    discovery_result = get_check_table(
        StartDiscoveryRequest(
            host=host, folder=host.folder(), options=_discovery_options(DiscoveryAction.NONE)
        )
    )
    return serve_json(serialize_discovery_result(host, discovery_result))


@Endpoint(
    collection_href("service", "services"),
    ".../collection",
    method="get",
    response_schema=response_schemas.DomainObject,
    tag_group="Setup",
    query_params=[
        {
            "host_name": gui_fields.HostField(
                description="The host of the discovered services.",
                example="example.com",
                required=True,
            ),
            "discovery_phase": fields.String(
                description="The discovery phase of the services.",
                enum=sorted(SERVICE_DISCOVERY_PHASES.keys()),
                example="monitored",
                required=True,
            ),
        }
    ],
    deprecated_urls={"/domain-types/service/collections/services": 14537},
)
def show_services(params: Mapping[str, Any]) -> Response:
    """Show all services of specific phase"""
    host = Host.load_host(params["host_name"])
    discovery_request = StartDiscoveryRequest(
        host=host,
        folder=host.folder(),
        options=DiscoveryOptions(
            action=DiscoveryAction.NONE,
            show_checkboxes=False,
            show_parameters=False,
            show_discovered_labels=False,
            show_plugin_names=False,
            ignore_errors=True,
        ),
    )
    discovery_result = get_check_table(discovery_request)
    return _serve_services(
        host,
        discovery_result.check_table,
        [params["discovery_phase"]],
    )


class UpdateDiscoveryPhase(BaseSchema):
    check_type = fields.String(
        description="The name of the check which this service uses.",
        example="df",
        required=True,
    )
    service_item = fields.String(
        description="The value uniquely identifying the service on a given host.",
        example="/home",
        required=True,
        allow_none=True,
    )
    target_phase = fields.String(
        description="The target phase of the service.",
        enum=sorted(SERVICE_DISCOVERY_PHASES.keys()),
        example="monitored",
        required=True,
    )


@Endpoint(
    constructors.object_action_href("host", "{host_name}", "update_discovery_phase"),
    ".../modify",
    method="put",
    output_empty=True,
    tag_group="Setup",
    path_params=[
        {
            "host_name": gui_fields.HostField(
                description="The host of the service which shall be updated.",
                example="example.com",
            ),
        }
    ],
    status_descriptions={
        404: "Host could not be found",
    },
    request_schema=UpdateDiscoveryPhase,
    # TODO: CMK-10911 (permissions)
    permissions_required=permissions.AnyPerm(
        [
            permissions.Optional(
                permissions.AnyPerm(
                    [
                        permissions.Perm("wato.service_discovery_to_monitored"),
                        permissions.Perm("wato.service_discovery_to_ignored"),
                        permissions.Perm("wato.service_discovery_to_undecided"),
                        permissions.Perm("wato.service_discovery_to_removed"),
                    ]
                )
            ),
        ]
    ),
)
def update_service_phase(params: Mapping[str, Any]) -> Response:
    """Update the phase of a service"""
    body = params["body"]
    host = Host.load_host(params["host_name"])
    target_phase = body["target_phase"]
    check_type = body["check_type"]
    service_item = body["service_item"]
    _update_single_service_phase(
        SERVICE_DISCOVERY_PHASES[target_phase],
        host,
        check_type,
        service_item,
    )
    return Response(status=204)


def _update_single_service_phase(
    target_phase: str,
    host: CREHost,
    check_type: str,
    service_item: str | None,
) -> None:
    discovery = Discovery(
        host=host,
        discovery_options=DiscoveryOptions(
            action=DiscoveryAction.SINGLE_UPDATE,
            show_checkboxes=False,
            show_parameters=False,
            show_discovered_labels=False,
            show_plugin_names=False,
            ignore_errors=True,
        ),
        update_target=target_phase,
        update_services=[checkbox_id(check_type, service_item)],
    )
    discovery.execute_discovery()


@Endpoint(
    constructors.object_href("service_discovery_run", "{host_name}"),
    "cmk/show",
    method="get",
    tag_group="Setup",
    path_params=[HOST_NAME],
    response_schema=response_schemas.DomainObject,
)
def show_service_discovery_run(params: Mapping[str, Any]) -> Response:
    """Show the last service discovery background job on a host"""
    host = Host.load_host(params["host_name"])
    job = ServiceDiscoveryBackgroundJob(host.name())
    job_id = job.get_job_id()
    job_status = job.get_status()
    return serve_json(
        constructors.domain_object(
            domain_type="service_discovery_run",
            identifier=job_id,
            title=f"Service discovery background job {job_id} is {job_status.state}",
            extensions={
                "active": job_status.is_active,
                "state": job_status.state,
                "logs": {
                    "result": job_status.loginfo["JobResult"],
                    "progress": job_status.loginfo["JobProgressUpdate"],
                },
            },
            deletable=False,
            editable=False,
        )
    )


@Endpoint(
    constructors.object_action_href("service_discovery_run", "{host_name}", "wait-for-completion"),
    "cmk/wait-for-completion",
    method="get",
    status_descriptions={
        204: "The service discovery has been completed.",
        302: "The service discovery is still running. Redirecting to the "
        "'Wait for completion' endpoint.",
        404: "There is no running service discovery",
    },
    path_params=[HOST_NAME],
    additional_status_codes=[302],
    output_empty=True,
)
def service_discovery_run_wait_for_completion(params: Mapping[str, Any]) -> Response:
    """Wait for service discovery completion

    This endpoint will periodically redirect on itself to prevent timeouts.
    """
    host = Host.load_host(params["host_name"])
    job = ServiceDiscoveryBackgroundJob(host.name())
    if not job.exists():
        raise ProblemException(
            status=404,
            title="The requested service discovery job was not found",
            detail=f"Could not find a service discovery for host {host.name()}",
        )

    if job.is_active():
        response = Response(status=302)
        response.location = request.url
        return response
    return Response(status=204)


class DiscoverServicesDeprecated(BaseSchema):
    mode = _discovery_mode(default_mode="fix_all")


class DiscoverServices(BaseSchema):
    host_name = gui_fields.HostField(
        description="The host of the service which shall be updated.",
        example="example.com",
    )
    mode = _discovery_mode(default_mode="fix_all")


@Endpoint(
    constructors.domain_type_action_href("service_discovery_run", "start"),
    ".../update",
    method="post",
    tag_group="Setup",
    status_descriptions={
        302: "The service discovery background job has been initialized. Redirecting to the "
        "'Wait for service discovery completion' endpoint.",
        409: "A service discovery background job is currently running",
    },
    additional_status_codes=[302, 409],
    request_schema=DiscoverServices,
    response_schema=response_schemas.DomainObject,
    permissions_required=DISCOVERY_PERMISSIONS,
)
def execute_service_discovery(params: Mapping[str, Any]) -> Response:
    """Execute a service discovery on a host"""
    user.need_permission("wato.edit")
    body = params["body"]
    host = Host.load_host(body["host_name"])
    discovery_action = APIDiscoveryAction(body["mode"])
    return _execute_service_discovery(discovery_action, host)


@Endpoint(
    constructors.object_action_href("host", "{host_name}", "discover_services"),
    ".../update",
    method="post",
    tag_group="Setup",
    status_descriptions={
        302: "The service discovery background job has been initialized. Redirecting to the "
        "'Wait for service discovery completion' endpoint.",
        404: "Host could not be found",
        409: "A service discovery background job is currently running",
    },
    additional_status_codes=[302, 409],
    path_params=[HOST_NAME],
    request_schema=DiscoverServicesDeprecated,
    response_schema=response_schemas.DomainObject,
    permissions_required=DISCOVERY_PERMISSIONS,
    deprecated_urls={
        "/objects/host/{host_name}/actions/discover_services/invoke": 14538,
    },
)
def execute(params: Mapping[str, Any]) -> Response:
    """Execute a service discovery on a host"""
    user.need_permission("wato.edit")
    host = Host.load_host(params["host_name"])
    body = params["body"]
    discovery_action = APIDiscoveryAction(body["mode"])
    return _execute_service_discovery(discovery_action, host)


def _execute_service_discovery(discovery_action: APIDiscoveryAction, host: CREHost) -> Response:
    service_discovery_job = ServiceDiscoveryBackgroundJob(host.name())
    if service_discovery_job.is_active():
        return Response(status=409)
    discovery_options = _discovery_options(DISCOVERY_ACTION[discovery_action.value])
    if not has_discovery_action_specific_permissions(discovery_options.action, None):
        return problem(
            403,
            "You do not have the necessary permissions to execute this action",
        )
    discovery_result = get_check_table(
        StartDiscoveryRequest(
            host,
            host.folder(),
            options=discovery_options,
        )
    )
    match discovery_action:
        case APIDiscoveryAction.new:
            discovery_result = perform_service_discovery(
                discovery_options=discovery_options,
                discovery_result=discovery_result,
                update_services=[],
                update_source="new",
                update_target="old",
                host=host,
            )
        case APIDiscoveryAction.remove:
            discovery_result = perform_service_discovery(
                discovery_options=discovery_options,
                discovery_result=discovery_result,
                update_services=[],
                update_source="vanished",
                update_target="removed",
                host=host,
            )
        case APIDiscoveryAction.fix_all:
            discovery_result = perform_fix_all(
                discovery_options=discovery_options,
                host=host,
                discovery_result=discovery_result,
            )
        case APIDiscoveryAction.refresh | APIDiscoveryAction.tabula_rasa:
            discovery_run = _discovery_wait_for_completion_link(host.name())
            response = Response(status=302)
            response.location = discovery_run["href"]
            return response
        case APIDiscoveryAction.only_host_labels:
            discovery_result = perform_host_label_discovery(
                discovery_options=discovery_options, host=host, discovery_result=discovery_result
            )
        case _:
            assert_never(discovery_action)

    return serve_json(serialize_discovery_result(host, discovery_result))


def _discovery_wait_for_completion_link(host_name: str) -> LinkType:
    return constructors.link_endpoint(
        "cmk.gui.plugins.openapi.endpoints.service_discovery",
        "cmk/wait-for-completion",
        parameters={"host_name": host_name},
    )


def _in_phase(phase_to_check: str, discovery_phases: list[str]) -> bool:
    for phase in list(discovery_phases):
        if SERVICE_DISCOVERY_PHASES[phase] == phase_to_check:
            return True
    return False


def _lookup_phase_name(internal_phase_name: str) -> str:
    for key, value in SERVICE_DISCOVERY_PHASES.items():
        if value == internal_phase_name:
            return key
    raise ValueError(f"Key {internal_phase_name} not found in dict.")


def serialize_discovery_result(  # type: ignore[no-untyped-def]
    host: CREHost,
    discovery_result: DiscoveryResult,
):
    services = {}
    host_name = host.name()
    for entry in discovery_result.check_table:
        service_phase = _lookup_phase_name(entry.check_source)
        services[f"{entry.check_plugin_name}-{entry.item}"] = object_property(
            name=entry.description,
            title=f"The service is currently {service_phase!r}",
            value=service_phase,
            prop_format="string",
            linkable=False,
            extensions={
                "host_name": host_name,
                "check_plugin_name": entry.check_plugin_name,
                "service_name": entry.description,
                "service_item": entry.item,
                "service_phase": service_phase,
            },
            base="",
            links=[
                link_rel(
                    rel="cmk/service.move-monitored",
                    href=update_service_phase.path.format(host_name=host_name),
                    body_params={
                        "target_phase": "monitored",
                        "check_type": entry.check_plugin_name,
                        "service_item": entry.item,
                    },
                    method="put",
                    title="Move the service to monitored",
                ),
                link_rel(
                    rel="cmk/service.move-undecided",
                    href=update_service_phase.path.format(host_name=host_name),
                    body_params={
                        "target_phase": "undecided",
                        "check_type": entry.check_plugin_name,
                        "service_item": entry.item,
                    },
                    method="put",
                    title="Move the service to undecided",
                ),
                link_rel(
                    rel="cmk/service.move-ignored",
                    href=update_service_phase.path.format(host_name=host_name),
                    body_params={
                        "target_phase": "ignored",
                        "check_type": entry.check_plugin_name,
                        "service_item": entry.item,
                    },
                    method="put",
                    title="Move the service to ignored",
                ),
            ],
        )

    return domain_object(
        domain_type="service_discovery",
        identifier=f"service_discovery-{host_name}",
        title=f"Service discovery result of host {host_name}",
        editable=False,
        deletable=False,
        extensions={
            "check_table": services,
            "host_labels": discovery_result.host_labels,
            "vanished_labels": discovery_result.vanished_labels,
            "changed_labels": discovery_result.changed_labels,
        },
    )


# Bulk discovery

JOB_ID = {
    "job_id": fields.String(
        description="The unique identifier of the background job executing the bulk discovery",
        example="bulk_discovery",
        required=True,
    ),
}


class BulkDiscovery(BaseSchema):
    hostnames = fields.List(
        EXISTING_HOST_NAME,
        required=True,
        example=["example", "sample"],
        description="A list of host names",
    )
    mode = _discovery_mode(default_mode="new")
    do_full_scan = fields.Boolean(
        required=False,
        description="The option whether to perform a full scan or not.",
        example=True,
        load_default=True,
    )
    bulk_size = fields.Integer(
        required=False,
        description="The number of hosts to be handled at once.",
        example=10,
        load_default=10,
    )
    ignore_errors = fields.Boolean(
        required=False,
        description="The option whether to ignore errors in single check plugins.",
        example=True,
        load_default=True,
    )


@Endpoint(
    constructors.domain_type_action_href("discovery_run", "bulk-discovery-start"),
    "cmk/activate",
    method="post",
    status_descriptions={
        409: "A bulk discovery job is already active",
    },
    additional_status_codes=[409],
    request_schema=BulkDiscovery,
    response_schema=response_schemas.DiscoveryBackgroundJobStatusObject,
)
def execute_bulk_discovery(params: Mapping[str, Any]) -> Response:
    """Start a bulk discovery job"""
    body = params["body"]
    job = BulkDiscoveryBackgroundJob()
    if job.is_active():
        return Response(status=409)

    hosts_to_discover = prepare_hosts_for_discovery(body["hostnames"])
    start_bulk_discovery(
        job,
        hosts_to_discover,
        body["mode"],
        body["do_full_scan"],
        body["ignore_errors"],
        body["bulk_size"],
    )

    return _serve_background_job(job)


@Endpoint(
    constructors.object_href("discovery_run", "{job_id}"),
    "cmk/show",
    method="get",
    path_params=[JOB_ID],
    status_descriptions={
        404: "There is no running background job with this job_id.",
    },
    response_schema=response_schemas.DiscoveryBackgroundJobStatusObject,
)
def show_bulk_discovery_status(params: Mapping[str, Any]) -> Response:
    """Show the status of a bulk discovery job"""

    job_id = params["job_id"]
    job = BulkDiscoveryBackgroundJob()
    if job.get_job_id() != job_id:
        raise ProblemException(
            status=404,
            title="The requested background job_id was not found",
            detail=f"Could not find a background job with id {job_id}.",
        )
    return _serve_background_job(job)


def _discovery_options(action_mode: DiscoveryAction) -> DiscoveryOptions:
    return DiscoveryOptions(
        action=action_mode,
        show_checkboxes=False,
        show_parameters=False,
        show_discovered_labels=False,
        show_plugin_names=False,
        ignore_errors=True,
    )


def _serve_background_job(job: BulkDiscoveryBackgroundJob) -> Response:
    job_id = job.get_job_id()
    status_details = bulk_discovery_job_status(job)
    return serve_json(
        constructors.domain_object(
            domain_type="discovery_run",
            identifier=job_id,
            title=f"Background job {job_id} {'is active' if status_details['is_active'] else 'is finished'}",
            extensions={
                "active": status_details["is_active"],
                "state": status_details["job_state"],
                "logs": status_details["logs"],
            },
            deletable=False,
            editable=False,
        )
    )


def _serve_services(
    host: CREHost,
    discovered_services: Sequence[CheckPreviewEntry],
    discovery_phases: list[str],
) -> Response:
    members = {}
    host_name = host.name()
    for entry in discovered_services:
        if _in_phase(entry.check_source, discovery_phases):
            service_phase = _lookup_phase_name(entry.check_source)
            members[f"{entry.check_plugin_name}-{entry.item}"] = object_property(
                name=entry.description,
                title=f"The service is currently {service_phase!r}",
                value=service_phase,
                prop_format="string",
                linkable=False,
                extensions={
                    "host_name": host_name,
                    "check_plugin_name": entry.check_plugin_name,
                    "service_name": entry.description,
                    "service_item": entry.item,
                    "service_phase": service_phase,
                },
                base="",
                links=[
                    link_rel(
                        rel="cmk/service.move-monitored",
                        href=update_service_phase.path.format(host_name=host_name),
                        body_params={
                            "target_phase": "monitored",
                            "check_type": entry.check_plugin_name,
                            "service_item": entry.item,
                        },
                        method="put",
                        title="Move the service to monitored",
                    ),
                    link_rel(
                        rel="cmk/service.move-undecided",
                        href=update_service_phase.path.format(host_name=host_name),
                        body_params={
                            "target_phase": "undecided",
                            "check_type": entry.check_plugin_name,
                            "service_item": entry.item,
                        },
                        method="put",
                        title="Move the service to undecided",
                    ),
                    link_rel(
                        rel="cmk/service.move-ignored",
                        href=update_service_phase.path.format(host_name=host_name),
                        body_params={
                            "target_phase": "ignored",
                            "check_type": entry.check_plugin_name,
                            "service_item": entry.item,
                        },
                        method="put",
                        title="Move the service to ignored",
                    ),
                ],
            )

    return serve_json(
        domain_object(
            domain_type="service_discovery",
            identifier=f"{host_name}-services-wato",
            title="Services discovery",
            members=members,
            editable=False,
            deletable=False,
            extensions={},
        )
    )
