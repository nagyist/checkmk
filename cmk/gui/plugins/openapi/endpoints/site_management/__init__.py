#!/usr/bin/env python3
# Copyright (C) 2022 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Site Management

The site management endpoints give you the flexibility to configure connections with
distributed sites the same way you would via the web interface.

The site management endpoints allow for:

* POST for creating new site configurations.
* PUT for updating current site configurations.
* LIST for listing all current site configurations.
* GET for getting a single site configuration.
* DELETE for deleting a single site configuration via its site id.
* LOGIN for logging into an existing site.
* LOGOUT for logging out of an existing site.

"""

from collections.abc import Mapping
from typing import Any

from livestatus import SiteConfiguration, SiteConfigurations, SiteId

from cmk.gui.exceptions import MKUserError
from cmk.gui.http import Response
from cmk.gui.logged_in import user
from cmk.gui.plugins.openapi.endpoints.site_management.request_schemas import (
    SITE_ID,
    SITE_ID_EXISTS,
    SiteConnectionRequestCreate,
    SiteConnectionRequestUpdate,
    SiteLoginRequest,
)
from cmk.gui.plugins.openapi.endpoints.site_management.response_schemas import (
    SiteConnectionResponse,
    SiteConnectionResponseCollection,
)
from cmk.gui.plugins.openapi.restful_objects import constructors, Endpoint, permissions
from cmk.gui.plugins.openapi.restful_objects.constructors import domain_object
from cmk.gui.plugins.openapi.restful_objects.type_defs import DomainObject
from cmk.gui.plugins.openapi.utils import problem, serve_json
from cmk.gui.watolib.site_management import (
    add_changes_after_editing_site_connection,
    LoginException,
    SiteConfig,
    SiteDoesNotExistException,
    SitesApiMgr,
)

PERMISSIONS = permissions.Perm("wato.sites")


def _problem_from_user_error(e: MKUserError) -> Response:
    return problem(
        status=404,
        title="User Error",
        detail=str(e),
    )


@Endpoint(
    constructors.object_href("site_connection", "{site_id}"),
    "cmk/show",
    method="get",
    tag_group="Setup",
    path_params=[SITE_ID_EXISTS],
    response_schema=SiteConnectionResponse,
    permissions_required=PERMISSIONS,
)
def show_site(params: Mapping[str, Any]) -> Response:
    """Show a site connection"""
    user.need_permission("wato.sites")
    site_id = SiteId(params["site_id"])
    site: SiteConfiguration = SitesApiMgr().get_a_site(site_id)
    return serve_json(_serialize_site(SiteConfig.from_internal(site_id, site)))


@Endpoint(
    constructors.collection_href("site_connection"),
    ".../collection",
    method="get",
    tag_group="Setup",
    response_schema=SiteConnectionResponseCollection,
    permissions_required=PERMISSIONS,
)
def show_sites(params: Mapping[str, Any]) -> Response:
    """Show all site connections"""
    user.need_permission("wato.sites")
    all_sites: SiteConfigurations = SitesApiMgr().get_all_sites()
    all_site_objs: list[SiteConfig] = [
        SiteConfig.from_internal(site_id, site) for site_id, site in all_sites.items()
    ]
    return serve_json(
        constructors.collection_object(
            domain_type="site_connection",
            value=[_serialize_site(site) for site in all_site_objs],
        )
    )


@Endpoint(
    constructors.collection_href("site_connection"),
    "cmk/create",
    method="post",
    tag_group="Setup",
    request_schema=SiteConnectionRequestCreate,
    response_schema=SiteConnectionResponse,
    permissions_required=PERMISSIONS,
)
def post_site(params: Mapping[str, Any]) -> Response:
    """Create a site connection"""
    user.need_permission("wato.sites")
    return _convert_validate_and_save_site_data(
        site_id=params["body"]["site_config"]["basic_settings"]["site_id"],
        site_config=params["body"]["site_config"],
        is_new_connection=True,
    )


@Endpoint(
    constructors.object_href("site_connection", "{site_id}"),
    "cmk/update",
    method="put",
    tag_group="Setup",
    path_params=[SITE_ID_EXISTS],
    request_schema=SiteConnectionRequestUpdate,
    response_schema=SiteConnectionResponse,
    permissions_required=PERMISSIONS,
)
def put_site(params: Mapping[str, Any]) -> Response:
    """Update a site connection"""
    user.need_permission("wato.sites")
    return _convert_validate_and_save_site_data(
        site_id=params["site_id"],
        site_config=params["body"]["site_config"],
        is_new_connection=False,
    )


@Endpoint(
    constructors.object_action_href("site_connection", "{site_id}", "delete"),
    ".../delete",
    method="post",
    tag_group="Setup",
    path_params=[SITE_ID],
    output_empty=True,
    permissions_required=PERMISSIONS,
)
def delete_site(params: Mapping[str, Any]) -> Response:
    """Delete a site connection"""
    user.need_permission("wato.sites")
    try:
        SitesApiMgr().delete_a_site(SiteId(params["site_id"]))
    except MKUserError as exc:
        return _problem_from_user_error(exc)
    except SiteDoesNotExistException:
        pass
    return Response(status=204)


@Endpoint(
    constructors.object_action_href("site_connection", "{site_id}", "login"),
    "cmk/site_login",
    method="post",
    tag_group="Setup",
    path_params=[SITE_ID_EXISTS],
    request_schema=SiteLoginRequest,
    output_empty=True,
    additional_status_codes=[401],
    permissions_required=PERMISSIONS,
)
def site_login(params: Mapping[str, Any]) -> Response:
    """Login to a remote site"""
    user.need_permission("wato.sites")
    body = params["body"]
    try:
        SitesApiMgr().login_to_site(
            site_id=SiteId(params["site_id"]),
            username=body["username"],
            password=body["password"],
        )
    except LoginException as exc:
        return problem(
            status=400,
            title="Login problem",
            detail=str(exc),
        )
    return Response(status=204)


@Endpoint(
    constructors.object_action_href("site_connection", "{site_id}", "logout"),
    "cmk/site_logout",
    method="post",
    tag_group="Setup",
    path_params=[SITE_ID_EXISTS],
    output_empty=True,
    permissions_required=PERMISSIONS,
)
def site_logout(params: Mapping[str, Any]) -> Response:
    """Logout from a remote site"""
    user.need_permission("wato.sites")
    SitesApiMgr().logout_of_site(params["site_id"])
    return Response(status=204)


def _serialize_site(site: SiteConfig) -> DomainObject:
    return domain_object(
        domain_type="site_connection",
        identifier=site.basic_settings.site_id,
        title=site.basic_settings.alias,
        extensions=dict(site.to_external()),
        editable=True,
        deletable=True,
    )


def _convert_validate_and_save_site_data(
    *,
    site_id: SiteId,
    site_config: dict[str, Any],
    is_new_connection: bool,
) -> Response:
    site_config["basic_settings"]["site_id"] = site_id
    try:
        site_obj: SiteConfig = SiteConfig.from_external(site_config)
        internal_config: SiteConfiguration = site_obj.to_internal()
        SitesApiMgr().validate_and_save_site(site_id, internal_config)
    except MKUserError as exc:
        return _problem_from_user_error(exc)

    add_changes_after_editing_site_connection(
        site_id=site_id,
        is_new_connection=is_new_connection,
        replication_enabled=site_obj.configuration_connection.enable_replication,
    )

    return serve_json(data=_serialize_site(site_obj), status=200)
