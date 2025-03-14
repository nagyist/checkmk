#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Modes for services and discovery"""

import json
import pprint
import traceback
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import Any, Literal, NamedTuple

from livestatus import SiteId

import cmk.utils.render
from cmk.utils.check_utils import worst_service_state
from cmk.utils.defines import short_service_state_name
from cmk.utils.exceptions import MKGeneralException
from cmk.utils.html import get_html_state_marker
from cmk.utils.site import omd_site
from cmk.utils.type_defs import HostName

from cmk.automations.results import CheckPreviewEntry

from cmk.gui.background_job import JobStatusStates
from cmk.gui.breadcrumb import Breadcrumb, make_main_menu_breadcrumb
from cmk.gui.config import active_config
from cmk.gui.exceptions import MKUserError
from cmk.gui.htmllib.foldable_container import foldable_container
from cmk.gui.htmllib.generator import HTMLWriter
from cmk.gui.htmllib.html import html
from cmk.gui.http import request
from cmk.gui.i18n import _, ungettext
from cmk.gui.logged_in import user
from cmk.gui.page_menu import (
    make_display_options_dropdown,
    make_javascript_action,
    make_javascript_link,
    make_simple_link,
    PageMenu,
    PageMenuDropdown,
    PageMenuEntry,
    PageMenuRenderer,
    PageMenuTopic,
)
from cmk.gui.page_menu_entry import disable_page_menu_entry, enable_page_menu_entry
from cmk.gui.pages import AjaxPage, page_registry, PageResult
from cmk.gui.plugins.wato.utils import mode_registry, WatoMode
from cmk.gui.plugins.wato.utils.context_buttons import make_host_status_link
from cmk.gui.site_config import sitenames
from cmk.gui.table import Foldable, Table, table_element
from cmk.gui.type_defs import PermissionName
from cmk.gui.utils.csrf_token import check_csrf_token
from cmk.gui.utils.html import HTML
from cmk.gui.utils.output_funnel import output_funnel
from cmk.gui.utils.transaction_manager import transactions
from cmk.gui.utils.urls import DocReference
from cmk.gui.view_utils import format_plugin_output, render_labels
from cmk.gui.wato.pages.hosts import ModeEditHost
from cmk.gui.watolib.activate_changes import ActivateChanges, get_pending_changes_tooltip
from cmk.gui.watolib.audit_log_url import make_object_audit_log_url
from cmk.gui.watolib.automation_commands import automation_command_registry, AutomationCommand
from cmk.gui.watolib.check_mk_automations import active_check
from cmk.gui.watolib.hosts_and_folders import CREHost, Folder, folder_preserving_link, Host
from cmk.gui.watolib.rulespecs import rulespec_registry
from cmk.gui.watolib.services import (
    checkbox_id,
    DiscoveryAction,
    DiscoveryOptions,
    DiscoveryResult,
    DiscoveryState,
    execute_discovery_job,
    get_check_table,
    has_discovery_action_specific_permissions,
    has_modification_specific_permissions,
    initial_discovery_result,
    perform_fix_all,
    perform_host_label_discovery,
    perform_service_discovery,
    StartDiscoveryRequest,
    UpdateType,
)
from cmk.gui.watolib.utils import may_edit_ruleset, mk_repr

AjaxDiscoveryRequest = dict[str, Any]


class TableGroupEntry(NamedTuple):
    table_group: str
    show_bulk_actions: bool
    title: str
    help_text: str


@mode_registry.register
class ModeDiscovery(WatoMode):
    """This mode is the entry point to the discovery page.

    It renders the static containter elements for the discovery page and optionally
    starts a the discovery data update process. Additional processing is done by
    ModeAjaxServiceDiscovery()
    """

    @classmethod
    def name(cls) -> str:
        return "inventory"

    @staticmethod
    def static_permissions() -> Collection[PermissionName]:
        return ["hosts"]

    @classmethod
    def parent_mode(cls) -> type[WatoMode] | None:
        return ModeEditHost

    def _from_vars(self):
        self._host = Folder.current().load_host(request.get_ascii_input_mandatory("host"))
        if not self._host:
            raise MKUserError("host", _("You called this page with an invalid host name."))

        self._host.need_permission("read")

        action = DiscoveryAction.NONE
        if user.may("wato.services"):
            show_checkboxes = user.discovery_checkboxes
            if request.var("_scan") == "1":
                action = DiscoveryAction.REFRESH
        else:
            show_checkboxes = False

        show_parameters = user.parameter_column
        show_discovered_labels = user.discovery_show_discovered_labels
        show_plugin_names = user.discovery_show_plugin_names

        self._options = DiscoveryOptions(
            action=action,
            show_checkboxes=show_checkboxes,
            show_parameters=show_parameters,
            show_discovered_labels=show_discovered_labels,
            show_plugin_names=show_plugin_names,
            # Continue discovery even when one discovery function raises an exception. The detail
            # output will show which discovery function failed. This is better than failing the
            # whole discovery.
            ignore_errors=True,
        )

    def title(self) -> str:
        return _("Services of host %s") % self._host.name()

    def page_menu(self, breadcrumb: Breadcrumb) -> PageMenu:
        return service_page_menu(breadcrumb, self._host, self._options)

    def page(self) -> None:
        # This is needed to make the discovery page show the help toggle
        # button. The help texts on this page are only added dynamically via
        # AJAX.
        html.enable_help_toggle()
        self._container("datasources")
        self._container("fixall", True)
        self._async_progress_msg_container()
        self._container("service", True)
        html.javascript(
            "cmk.service_discovery.start(%s, %s, %s)"
            % (
                json.dumps(self._host.name()),
                json.dumps(self._host.folder().path()),
                json.dumps(self._options._asdict()),
            )
        )

    def _container(self, name: str, hidden: bool = False) -> None:
        html.open_div(id_=f"{name}_container", style=("display:none" if hidden else ""))
        html.close_div()

    def _async_progress_msg_container(self):
        html.open_div(id_="async_progress_msg")
        html.show_message(_("Loading..."))
        html.close_div()


@automation_command_registry.register
class AutomationServiceDiscoveryJob(AutomationCommand):
    """Is called by _get_check_table() to execute the background job on a remote site"""

    def command_name(self):
        return "service-discovery-job"

    def get_request(self) -> StartDiscoveryRequest:
        user.need_permission("wato.hosts")

        host_name = request.get_ascii_input("host_name")
        if host_name is None:
            raise MKGeneralException(_("Host is missing"))
        host = Host.host(host_name)
        if host is None:
            raise MKGeneralException(
                _(
                    "Host %s does not exist on remote site %s. This "
                    "may be caused by a failed configuration synchronization. Have a look at "
                    'the <a href="wato.py?folder=&mode=changelog">activate changes page</a> '
                    "for further information."
                )
                % (host_name, omd_site())
            )
        host.need_permission("read")

        ascii_input = request.get_ascii_input("options")
        if ascii_input is not None:
            options = json.loads(ascii_input)
        else:
            options = {}
        return StartDiscoveryRequest(
            host=host, folder=host.folder(), options=DiscoveryOptions(**options)
        )

    def execute(self, api_request: StartDiscoveryRequest) -> str:
        return execute_discovery_job(api_request).serialize()


@page_registry.register_page("ajax_service_discovery")
class ModeAjaxServiceDiscovery(AjaxPage):
    def page(self) -> PageResult:
        check_csrf_token()
        user.need_permission("wato.hosts")

        api_request: AjaxDiscoveryRequest = self.webapi_request()
        request.del_var("request")  # Do not add this to URLs constructed later
        update_target = (
            None if (raw := api_request.get("update_target", None)) is None else UpdateType(raw)
        )
        update_source = api_request.get("update_source", None)
        api_request.setdefault("update_services", [])
        update_services = api_request.get("update_services", [])

        # Make Folder() be able to detect the current folder correctly
        request.set_var("folder", api_request["folder_path"])

        folder = Folder.folder(api_request["folder_path"])
        host = folder.host(api_request["host_name"])
        if not host:
            raise MKUserError("host", _("You called this page with an invalid host name."))
        host.need_permission("read")

        discovery_options = DiscoveryOptions(**api_request["discovery_options"])
        # Reuse the discovery result already known to the GUI or fetch a new one?
        previous_discovery_result = (
            DiscoveryResult.deserialize(raw)
            if (raw := api_request.get("discovery_result"))
            else None
        )

        # If the user has the wrong permissions, then we still return a discovery result
        # which is different from the REST API behavior.
        if not has_discovery_action_specific_permissions(discovery_options.action, update_target):
            discovery_options = discovery_options._replace(action=DiscoveryAction.NONE)

        discovery_result = self._perform_discovery_action(
            host=host,
            discovery_options=discovery_options,
            previous_discovery_result=previous_discovery_result,
            update_source=update_source,
            update_target=None if update_target is None else update_target.value,
            update_services=update_services,
        )
        if self._sources_failed_on_first_attempt(previous_discovery_result, discovery_result):
            discovery_result = discovery_result._replace(
                check_table=(), host_labels={}, new_labels={}, vanished_labels={}, changed_labels={}
            )

        if not discovery_result.check_table_created and previous_discovery_result:
            discovery_result = previous_discovery_result._replace(
                job_status=discovery_result.job_status
            )

        show_checkboxes = user.discovery_checkboxes
        if show_checkboxes != discovery_options.show_checkboxes:
            user.discovery_checkboxes = discovery_options.show_checkboxes
        show_parameters = user.parameter_column
        if show_parameters != discovery_options.show_parameters:
            user.parameter_column = discovery_options.show_parameters
        show_discovered_labels = user.discovery_show_discovered_labels
        if show_discovered_labels != discovery_options.show_discovered_labels:
            user.discovery_show_discovered_labels = discovery_options.show_discovered_labels
        show_plugin_names = user.discovery_show_plugin_names
        if show_plugin_names != discovery_options.show_plugin_names:
            user.discovery_show_plugin_names = discovery_options.show_plugin_names

        renderer = DiscoveryPageRenderer(
            host,
            discovery_options,
        )
        page_code = renderer.render(discovery_result, api_request)
        datasources_code = renderer.render_datasources(discovery_result.sources)
        fix_all_code = renderer.render_fix_all(discovery_result)

        # Clean the requested action after performing it
        performed_action = discovery_options.action
        discovery_options = discovery_options._replace(action=DiscoveryAction.NONE)
        message = (
            self._get_status_message(previous_discovery_result, discovery_result, performed_action)
            if discovery_result.sources
            else None
        )

        return {
            "is_finished": not discovery_result.is_active(),
            "job_state": discovery_result.job_status["state"],
            "message": message,
            "body": page_code,
            "datasources": datasources_code,
            "fixall": fix_all_code,
            "page_menu": self._get_page_menu(discovery_options, host),
            "pending_changes_info": ActivateChanges().get_pending_changes_info().message,
            "pending_changes_tooltip": get_pending_changes_tooltip(),
            "discovery_options": discovery_options._asdict(),
            "discovery_result": discovery_result.serialize(),
        }

    def _perform_discovery_action(
        self,
        host: CREHost,
        discovery_options: DiscoveryOptions,
        previous_discovery_result: DiscoveryResult | None,
        update_source: str | None,
        update_target: str | None,
        update_services: list[str],
    ) -> DiscoveryResult:
        if discovery_options.action == DiscoveryAction.NONE:
            return initial_discovery_result(discovery_options, host, previous_discovery_result)

        if discovery_options.action in (
            DiscoveryAction.REFRESH,
            DiscoveryAction.TABULA_RASA,
            DiscoveryAction.STOP,
        ):
            if transactions.check_transaction():
                return get_check_table(
                    StartDiscoveryRequest(host, host.folder(), discovery_options)
                )
            return initial_discovery_result(discovery_options, host, previous_discovery_result)

        discovery_result = initial_discovery_result(
            discovery_options, host, previous_discovery_result
        )
        if not transactions.check_transaction():
            return discovery_result

        match discovery_options.action:
            case DiscoveryAction.FIX_ALL:
                discovery_result = perform_fix_all(
                    discovery_options=discovery_options,
                    discovery_result=discovery_result,
                    host=host,
                )
            case DiscoveryAction.UPDATE_HOST_LABELS:
                discovery_result = perform_host_label_discovery(
                    discovery_options=discovery_options,
                    discovery_result=discovery_result,
                    host=host,
                )
            case DiscoveryAction.SINGLE_UPDATE | DiscoveryAction.BULK_UPDATE | DiscoveryAction.UPDATE_SERVICES:
                discovery_result = perform_service_discovery(
                    discovery_options=discovery_options,
                    discovery_result=discovery_result,
                    update_services=update_services,
                    update_source=update_source,
                    update_target=update_target,
                    host=host,
                )
            case DiscoveryAction.UPDATE_SERVICES:
                discovery_result = perform_service_discovery(
                    discovery_options=discovery_options,
                    discovery_result=discovery_result,
                    update_services=update_services,
                    update_source=None,
                    update_target=None,
                    host=host,
                )
            case _:
                raise MKUserError(
                    "discovery", f"Unknown discovery action: {discovery_options.action}"
                )

        return discovery_result

    def _get_page_menu(self, discovery_options: DiscoveryOptions, host: CREHost) -> str:
        """Render the page menu contents to reflect contect changes

        The page menu needs to be updated, just like the body of the page. We previously tried an
        incremental approach (render the page menu once and update it's elements later during each
        refresh), but it was a lot more complex to realize and resulted in inconsistencies. This is
        the simpler solution and less error prone.
        """
        page_menu = service_page_menu(self._get_discovery_breadcrumb(host), host, discovery_options)
        with output_funnel.plugged():
            PageMenuRenderer().show(
                page_menu, hide_suggestions=not user.get_tree_state("suggestions", "all", True)
            )
            return output_funnel.drain()

    def _get_discovery_breadcrumb(self, host: CREHost) -> Breadcrumb:
        with request.stashed_vars():
            request.set_var("host", host.name())
            mode = ModeDiscovery()
            return make_main_menu_breadcrumb(mode.main_menu()) + mode.breadcrumb()

    def _get_status_message(
        self,
        previous_discovery_result: DiscoveryResult | None,
        discovery_result: DiscoveryResult,
        performed_action: DiscoveryAction,
    ) -> str:
        if performed_action is DiscoveryAction.UPDATE_HOST_LABELS:
            return _("The discovered host labels have been updated.")

        cmk_check_entries = [
            e for e in discovery_result.check_table if DiscoveryState.is_discovered(e.check_source)
        ]

        if discovery_result.job_status["state"] == JobStatusStates.INITIALIZED:
            if discovery_result.is_active():
                return _("Initializing discovery...")
            if not cmk_check_entries:
                return _("No discovery information available. Please perform a rescan.")
            return _(
                "No fresh discovery information available. Using latest cached information. "
                "Please perform a rescan in case you want to discover the current state."
            )

        job_title = discovery_result.job_status.get("title", _("Service discovery"))
        duration_txt = cmk.utils.render.Age(discovery_result.job_status["duration"])
        finished_time = (
            discovery_result.job_status["started"] + discovery_result.job_status["duration"]
        )
        finished_txt = cmk.utils.render.date_and_time(finished_time)

        if discovery_result.job_status["state"] == JobStatusStates.RUNNING:
            return _("%s running for %s") % (job_title, duration_txt)

        if discovery_result.job_status["state"] == JobStatusStates.EXCEPTION:
            return _(
                "%s failed after %s: %s (see <tt>var/log/web.log</tt> for further information)"
            ) % (
                job_title,
                duration_txt,
                "\n".join(discovery_result.job_status["loginfo"]["JobException"]),
            )

        messages = []
        if self._sources_failed_on_first_attempt(previous_discovery_result, discovery_result):
            messages.append(
                _("The problems above might be caused by missing caches. Please trigger a rescan.")
            )

        if discovery_result.job_status["state"] == JobStatusStates.STOPPED:
            messages.append(
                _("%s was stopped after %s at %s.") % (job_title, duration_txt, finished_txt)
            )

        progress_update_log = discovery_result.job_status["loginfo"]["JobProgressUpdate"]
        warnings = [f"<br>{line}" for line in progress_update_log if line.startswith("WARNING")]

        if discovery_result.job_status["state"] == JobStatusStates.FINISHED and not warnings:
            return " ".join(messages)

        messages.extend(warnings)

        with output_funnel.plugged():
            with foldable_container(
                treename="service_discovery",
                id_="options",
                isopen=False,
                title=_("Job details"),
                indent=False,
            ):
                html.open_div(class_="log_output", style="height: 400px;", id_="progress_log")
                html.pre("\n".join(progress_update_log))
                html.close_div()
            messages.append(output_funnel.drain())

        return " ".join(messages)

    @staticmethod
    def _sources_failed_on_first_attempt(
        previous: DiscoveryResult | None,
        current: DiscoveryResult,
    ) -> bool:
        """Only consider CRIT sources failed"""
        return previous is None and any(source[0] == 2 for source in current.sources.values())


class DiscoveryPageRenderer:
    def __init__(self, host: CREHost, options: DiscoveryOptions) -> None:
        super().__init__()
        self._host = host
        self._options = options

    def render(self, discovery_result: DiscoveryResult, api_request: dict) -> str:
        with output_funnel.plugged():
            self._toggle_action_page_menu_entries(discovery_result)
            enable_page_menu_entry(html, "inline_help")
            self._show_discovered_host_labels(discovery_result)
            self._show_discovery_details(discovery_result, api_request)
            return output_funnel.drain()

    def render_fix_all(self, discovery_result: DiscoveryResult) -> str:
        with output_funnel.plugged():
            self._show_fix_all(discovery_result)
            return output_funnel.drain()

    def render_datasources(self, sources: Mapping[str, tuple[int, str]]) -> str:
        states = [s for s, _output in sources.values()]
        overall_state = worst_service_state(*states, default=0)

        with output_funnel.plugged():
            if sources:
                # Colored overall state field
                html.open_div(class_="datasources_state state%s" % overall_state)
                html.open_span()
                match overall_state:
                    case 0:
                        html.icon("check")
                    case 1:
                        html.icon("host_svc_problems_dark")
                    case 2 | 3:
                        html.icon("host_svc_problems")
                html.close_span()
                html.close_div()

            # Output per data source
            html.open_div(class_="datasources_output")
            if not sources:
                html.h2(_("There are no configured datasources"))
                html.close_div()
                return output_funnel.drain()

            if overall_state == 0:
                html.h2(_("All datasources are OK"))
            else:
                num_problem_states: int = len([s for s in states if s != 0])
                html.h2(
                    ungettext(
                        "Problems with %d datasource detected",
                        "Problems with %d datasources detected",
                        num_problem_states,
                    )
                    % num_problem_states
                )

            html.open_table()
            for state, output in sources.values():
                html.open_tr()
                html.open_td()
                html.write_html(HTML(get_html_state_marker(state)))
                html.close_td()
                # Make sure not to show long output
                html.td(format_plugin_output(output.split("\n", 1)[0].replace(" ", ": ", 1)))
                html.close_tr()
            html.close_table()

            html.close_div()

            return output_funnel.drain()

    def _show_discovered_host_labels(self, discovery_result: DiscoveryResult) -> None:
        if not discovery_result.host_labels:
            return None

        with table_element(
            table_id="host_labels",
            title=_("Discovered host labels (%s)") % len(discovery_result.host_labels),
            css="data",
            searchable=False,
            limit=False,
            sortable=False,
            foldable=Foldable.FOLDABLE_STATELESS,
            omit_update_header=False,
        ) as table:
            return self._render_host_labels(
                table,
                discovery_result,
            )

    def _render_host_labels(  # type: ignore[no-untyped-def]
        self,
        table,
        discovery_result: DiscoveryResult,
    ) -> None:
        active_host_labels: dict[str, dict[str, str]] = {}
        changed_host_labels: dict[str, dict[str, str]] = {}

        for label_id, label in discovery_result.host_labels.items():
            # For visualization of the changed host labels the old value and the new value
            # of the host label are used the values are seperated with an arrow (\u279c)
            if label_id in discovery_result.changed_labels:
                changed_host_labels.setdefault(
                    label_id,
                    {
                        "value": "%s \u279c %s"
                        % (discovery_result.changed_labels[label_id]["value"], label["value"]),
                        "plugin_name": label["plugin_name"],
                    },
                )
            if label_id not in {
                **discovery_result.new_labels,
                **discovery_result.vanished_labels,
                **discovery_result.changed_labels,
            }:
                active_host_labels.setdefault(label_id, label)

        self._create_host_label_row(
            table,
            discovery_result.new_labels,
            _("New"),
        )
        self._create_host_label_row(
            table,
            discovery_result.vanished_labels,
            _("Vanished"),
        )
        self._create_host_label_row(
            table,
            changed_host_labels,
            _("Changed"),
        )
        self._create_host_label_row(
            table,
            active_host_labels,
            _("Active"),
        )

    def _create_host_label_row(  # type: ignore[no-untyped-def]
        self, table, host_labels, text
    ) -> None:
        if not host_labels:
            return

        table.row()
        table.cell(_("Status"), text, css="labelstate")

        if not self._options.show_plugin_names:
            labels_html = render_labels(
                {label_id: label["value"] for label_id, label in host_labels.items()},
                "host",
                with_links=False,
                label_sources={label_id: "discovered" for label_id in host_labels.keys()},
            )
            table.cell(_("Host labels"), labels_html, css="expanding")
            return

        plugin_names = HTML("")
        labels_html = HTML("")
        for label_id, label in host_labels.items():
            label_data = {label_id: label["value"]}
            ctype = label["plugin_name"]

            manpage_url = folder_preserving_link([("mode", "check_manpage"), ("check_type", ctype)])
            plugin_names += (
                HTMLWriter.render_a(content=ctype, href=manpage_url) + HTMLWriter.render_br()
            )
            labels_html += render_labels(
                label_data,
                "host",
                with_links=False,
                label_sources={label_id: "discovered"},
            )

        table.cell(_("Host labels"), labels_html, css="expanding")
        table.cell(_("Check Plugin"), plugin_names, css="plugins")
        return

    def _show_discovery_details(self, discovery_result: DiscoveryResult, api_request: dict) -> None:
        if not discovery_result.check_table:
            if not discovery_result.is_active() and self._host.is_cluster():
                self._show_empty_cluster_hint()
            return

        # We currently don't get correct information from cmk.base (the data sources). Better
        # don't display this until we have the information.
        # html.write_text("Using discovery information from %s" % cmk.utils.render.date_and_time(
        #    discovery_result.check_table_created))

        by_group = self._group_check_table_by_state(discovery_result.check_table)
        for entry in self._ordered_table_groups():
            checks = by_group.get(entry.table_group, [])
            if not checks:
                continue

            html.begin_form("checks_%s" % entry.table_group, method="POST", action="wato.py")
            with table_element(
                table_id="checks_%s" % entry.table_group,
                title=f"{entry.title} ({len(checks)})",
                css="data",
                searchable=False,
                limit=False,
                sortable=False,
                foldable=Foldable.FOLDABLE_STATELESS,
                omit_update_header=False,
                help=entry.help_text,
                isopen=entry.table_group
                not in (
                    DiscoveryState.CLUSTERED_NEW,
                    DiscoveryState.CLUSTERED_OLD,
                    DiscoveryState.CLUSTERED_VANISHED,
                ),
            ) as table:
                for check in sorted(checks, key=lambda e: e.description.lower()):
                    self._show_check_row(
                        table, discovery_result, api_request, check, entry.show_bulk_actions
                    )

            if entry.show_bulk_actions:
                self._toggle_bulk_action_page_menu_entries(entry.table_group)
            html.hidden_fields()
            html.end_form()

    @staticmethod
    def _show_empty_cluster_hint() -> None:
        html.br()
        url = folder_preserving_link([("mode", "edit_ruleset"), ("varname", "clustered_services")])
        html.show_message(
            _(
                "Could not find any service for your cluster. You first need to "
                "specify which services of your nodes shal be added to the "
                'cluster. This is done using the <a href="%s">%s</a> ruleset.'
            )
            % (url, _("Clustered services"))
        )

    def _group_check_table_by_state(
        self, check_table: Iterable[CheckPreviewEntry]
    ) -> Mapping[str, Sequence[CheckPreviewEntry]]:
        by_group: dict[str, list[CheckPreviewEntry]] = {}
        for entry in check_table:
            by_group.setdefault(entry.check_source, []).append(entry)
        return by_group

    def _render_fix_all_element(self, title: str, count: int, href: str) -> None:
        html.open_li()
        html.open_a(href=href)
        html.span(title)
        html.span(str(count), class_="changed" if count else "")
        html.close_a()
        html.close_li()

    def _show_fix_all(self, discovery_result: DiscoveryResult) -> None:
        if not discovery_result:
            return

        if not user.may("wato.services"):
            return

        undecided_services = 0
        vanished_services = 0
        new_host_labels = len(discovery_result.new_labels)
        vanished_host_labels = len(discovery_result.vanished_labels)
        changed_host_labels = len(discovery_result.changed_labels)

        for service in discovery_result.check_table:
            if service.check_source == DiscoveryState.UNDECIDED:
                undecided_services += 1
            if service.check_source == DiscoveryState.VANISHED:
                vanished_services += 1

        if all(
            v == 0
            for v in [
                undecided_services,
                vanished_services,
                new_host_labels,
                vanished_host_labels,
                changed_host_labels,
            ]
        ):
            return

        html.icon("fixall", _("Service discovery details"))

        html.open_ul()
        self._render_fix_all_element(
            ungettext("Undecided service: ", "Undecided services: ", undecided_services),
            undecided_services,
            "#tree.table.checks_new",
        )
        self._render_fix_all_element(
            ungettext("Vanished service: ", "Vanished services: ", vanished_services),
            vanished_services,
            "#tree.table.checks_vanished",
        )
        self._render_fix_all_element(
            ungettext("New host label: ", "New host labels: ", new_host_labels),
            new_host_labels,
            "#tree.table.host_labels",
        )
        self._render_fix_all_element(
            ungettext("Vanished host label: ", "Vanished host labels: ", new_host_labels),
            vanished_host_labels,
            "#tree.table.host_labels",
        )
        self._render_fix_all_element(
            ungettext("Changed host label: ", "Changed host labels: ", new_host_labels),
            changed_host_labels,
            "#tree.table.host_labels",
        )
        html.close_ul()

        if any(
            [
                undecided_services,
                vanished_services,
                new_host_labels,
                vanished_host_labels,
                changed_host_labels,
            ]
        ):
            enable_page_menu_entry(html, "fixall")
        else:
            disable_page_menu_entry(html, "fixall")

    def _toggle_action_page_menu_entries(self, discovery_result: DiscoveryResult) -> None:
        if not user.may("wato.services"):
            return

        has_changes = any(
            check.check_source in (DiscoveryState.UNDECIDED, DiscoveryState.VANISHED)
            for check in discovery_result.check_table
        )
        had_services_before = any(
            check.check_source in (DiscoveryState.MONITORED, DiscoveryState.VANISHED)
            for check in discovery_result.check_table
        )

        if discovery_result.is_active():
            enable_page_menu_entry(html, "stop")
            return

        disable_page_menu_entry(html, "stop")
        enable_page_menu_entry(html, "refresh")

        if (
            has_changes
            and user.may("wato.service_discovery_to_monitored")
            and user.may("wato.service_discovery_to_removed")
        ):
            enable_page_menu_entry(html, "fix_all")

        if (
            had_services_before
            and user.may("wato.service_discovery_to_undecided")
            and user.may("wato.service_discovery_to_monitored")
            and user.may("wato.service_discovery_to_ignored")
            and user.may("wato.service_discovery_to_removed")
        ):
            enable_page_menu_entry(html, "tabula_rasa")

        if discovery_result.host_labels:
            enable_page_menu_entry(html, "update_host_labels")

        if had_services_before:
            enable_page_menu_entry(html, "show_checkboxes")
            enable_page_menu_entry(html, "show_parameters")
            enable_page_menu_entry(html, "show_discovered_labels")
            enable_page_menu_entry(html, "show_plugin_names")

    def _toggle_bulk_action_page_menu_entries(  # pylint: disable=too-many-branches
        self, table_source: str
    ) -> None:
        if not user.may("wato.services"):
            return

        if table_source == DiscoveryState.MONITORED:
            if has_modification_specific_permissions(UpdateType.UNDECIDED):
                self._enable_bulk_button(table_source, DiscoveryState.UNDECIDED)
            if has_modification_specific_permissions(UpdateType.IGNORED):
                self._enable_bulk_button(table_source, DiscoveryState.IGNORED)

        elif table_source == DiscoveryState.IGNORED:
            if may_edit_ruleset("ignored_services"):
                if has_modification_specific_permissions(UpdateType.MONITORED):
                    self._enable_bulk_button(table_source, DiscoveryState.MONITORED)
                if has_modification_specific_permissions(UpdateType.UNDECIDED):
                    self._enable_bulk_button(table_source, DiscoveryState.UNDECIDED)

        elif table_source == DiscoveryState.VANISHED:
            if has_modification_specific_permissions(UpdateType.REMOVED):
                self._enable_bulk_button(table_source, DiscoveryState.REMOVED)
            if has_modification_specific_permissions(UpdateType.IGNORED):
                self._enable_bulk_button(table_source, DiscoveryState.IGNORED)

        elif table_source == DiscoveryState.UNDECIDED:
            if has_modification_specific_permissions(UpdateType.MONITORED):
                self._enable_bulk_button(table_source, DiscoveryState.MONITORED)
            if has_modification_specific_permissions(UpdateType.IGNORED):
                self._enable_bulk_button(table_source, DiscoveryState.IGNORED)

    def _enable_bulk_button(self, source, target):
        enable_page_menu_entry(html, f"bulk_{source}_{target}")

    def _show_check_row(
        self,
        table: Table,
        discovery_result: DiscoveryResult,
        api_request: dict,
        entry: CheckPreviewEntry,
        show_bulk_actions: bool,
    ) -> None:
        statename = "" if entry.state is None else short_service_state_name(entry.state, "")
        if statename == "":
            statename = short_service_state_name(-1)
            stateclass = "state svcstate statep"
            table.row(css=["data"], state=0)
        else:
            assert entry.state is not None
            stateclass = f"state svcstate state{entry.state}"
            table.row(css=["data"], state=entry.state)

        self._show_bulk_checkbox(
            table,
            discovery_result,
            api_request,
            entry.check_plugin_name,
            entry.item,
            show_bulk_actions,
        )
        self._show_actions(table, discovery_result, entry)

        table.cell(
            _("State"),
            HTMLWriter.render_span(statename, class_=["state_rounded_fill"]),
            css=[stateclass],
        )
        table.cell(_("Service"), entry.description, css=["service"])
        table.cell(_("Status detail"), css=["expanding"])
        self._show_status_detail(entry)

        if entry.check_source in [DiscoveryState.ACTIVE, DiscoveryState.ACTIVE_IGNORED]:
            ctype = "check_" + entry.check_plugin_name
        else:
            ctype = entry.check_plugin_name
        manpage_url = folder_preserving_link([("mode", "check_manpage"), ("check_type", ctype)])

        if self._options.show_parameters:
            table.cell(_("Check parameters"), css=["expanding"])
            self._show_check_parameters(entry)

        if self._options.show_discovered_labels:
            table.cell(_("Discovered labels"))
            self._show_discovered_labels(entry.labels)

        if self._options.show_plugin_names:
            table.cell(
                _("Check plugin"),
                HTMLWriter.render_a(content=ctype, href=manpage_url),
                css=["plugins"],
            )

    def _show_status_detail(self, entry: CheckPreviewEntry) -> None:
        if entry.check_source not in [
            DiscoveryState.CUSTOM,
            DiscoveryState.ACTIVE,
            DiscoveryState.CUSTOM_IGNORED,
            DiscoveryState.ACTIVE_IGNORED,
        ]:
            # Do not show long output
            output, *_details = entry.output.split("\n", 1)
            if output:
                html.write_html(
                    HTML(
                        format_plugin_output(
                            output, shall_escape=active_config.escape_plugin_output
                        )
                    )
                )
            return

        div_id = f"activecheck_{entry.description}"
        html.div(html.render_icon("reload", cssclass="reloading"), id_=div_id)
        html.javascript(
            "cmk.service_discovery.register_delayed_active_check(%s, %s, %s, %s, %s, %s);"
            % (
                json.dumps(self._host.site_id() or ""),
                json.dumps(self._host.folder().path()),
                json.dumps(self._host.name()),
                json.dumps(entry.check_plugin_name),
                json.dumps(entry.item),
                json.dumps(div_id),
            )
        )

    def _show_check_parameters(self, entry: CheckPreviewEntry) -> None:
        varname = self._get_ruleset_name(entry)
        if not varname or varname not in rulespec_registry:
            return

        params = entry.effective_parameters
        if not params:
            # Avoid error message in this case.
            # For instance: Ruleset requires a key, but discovered parameters are empty.
            return

        rulespec = rulespec_registry[varname]
        try:
            if isinstance(params, dict) and "tp_computed_params" in params:
                html.write_text(
                    _("Timespecific parameters computed at %s")
                    % cmk.utils.render.date_and_time(params["tp_computed_params"]["computed_at"])
                )
                html.br()
                params = params["tp_computed_params"]["params"]
            rulespec.valuespec.validate_datatype(params, "")
            rulespec.valuespec.validate_value(params, "")
            paramtext = rulespec.valuespec.value_to_html(params)
            html.write_html(HTML(paramtext))
        except Exception as e:
            if active_config.debug:
                err = traceback.format_exc()
            else:
                err = "%s" % e
            paramtext = "<b>{}</b>: {}<br>".format(_("Invalid check parameter"), err)
            paramtext += "{}: <tt>{}</tt><br>".format(_("Variable"), varname)
            paramtext += _("Parameters:")
            paramtext += "<pre>%s</pre>" % (pprint.pformat(params))
            html.write_text(paramtext)

    def _show_discovered_labels(self, service_labels):
        label_code = render_labels(
            service_labels,
            "service",
            with_links=False,
            label_sources={k: "discovered" for k in service_labels.keys()},
        )
        html.write_html(label_code)

    def _show_bulk_checkbox(
        self, table, discovery_result, api_request, check_type, item, show_bulk_actions
    ):
        if not self._options.show_checkboxes or not user.may("wato.services"):
            return

        if not show_bulk_actions:
            table.cell(css="checkbox")
            return

        css_classes = ["service_checkbox"]
        if discovery_result.is_active():
            css_classes.append("disabled")

        table.cell(
            html.render_input(
                "_toggle_group",
                type_="button",
                class_="checkgroup",
                onclick="cmk.selection.toggle_group_rows(this);",
                value="X",
            ),
            sortable=False,
            css="checkbox",
        )
        name = checkbox_id(check_type, item)
        checked = (
            self._options.action == DiscoveryAction.BULK_UPDATE
            and name in api_request["update_services"]
        )
        html.checkbox(varname=name, deflt=checked, class_=css_classes)

    def _show_actions(  # pylint: disable=too-many-branches
        self,
        table: Table,
        discovery_result: DiscoveryResult,
        entry: CheckPreviewEntry,
    ) -> None:
        table.cell(css=["buttons"])
        if not user.may("wato.services"):
            html.empty_icon()
            html.empty_icon()
            html.empty_icon()
            html.empty_icon()
            return

        button_classes = ["service_button"]
        if discovery_result.is_active():
            button_classes.append("disabled")

        checkbox_name = checkbox_id(entry.check_plugin_name, entry.item)

        num_buttons = 0
        if entry.check_source == DiscoveryState.MONITORED:
            if has_modification_specific_permissions(UpdateType.UNDECIDED):
                num_buttons += self._icon_button(
                    DiscoveryState.MONITORED,
                    checkbox_name,
                    DiscoveryState.UNDECIDED,
                    "undecided",
                    button_classes,
                )
            if has_modification_specific_permissions(UpdateType.IGNORED):
                num_buttons += self._icon_button(
                    DiscoveryState.MONITORED,
                    checkbox_name,
                    DiscoveryState.IGNORED,
                    "disabled",
                    button_classes,
                )

        elif entry.check_source == DiscoveryState.IGNORED:
            if may_edit_ruleset("ignored_services") and has_modification_specific_permissions(
                UpdateType.MONITORED
            ):
                num_buttons += self._icon_button(
                    DiscoveryState.IGNORED,
                    checkbox_name,
                    DiscoveryState.MONITORED,
                    "monitored",
                    button_classes,
                )
            if has_modification_specific_permissions(UpdateType.IGNORED):
                num_buttons += self._icon_button(
                    DiscoveryState.IGNORED,
                    checkbox_name,
                    DiscoveryState.UNDECIDED,
                    "undecided",
                    button_classes,
                )
                num_buttons += self._disabled_services_button(entry.description)

        elif entry.check_source == DiscoveryState.VANISHED:
            if has_modification_specific_permissions(UpdateType.REMOVED):
                num_buttons += self._icon_button_removed(
                    entry.check_source, checkbox_name, button_classes
                )

            if has_modification_specific_permissions(UpdateType.IGNORED):
                num_buttons += self._icon_button(
                    DiscoveryState.VANISHED,
                    checkbox_name,
                    DiscoveryState.IGNORED,
                    "disabled",
                    button_classes,
                )

        elif entry.check_source == DiscoveryState.UNDECIDED:
            if has_modification_specific_permissions(UpdateType.MONITORED):
                num_buttons += self._icon_button(
                    DiscoveryState.UNDECIDED,
                    checkbox_name,
                    DiscoveryState.MONITORED,
                    "monitored",
                    button_classes,
                )
            if has_modification_specific_permissions(UpdateType.IGNORED):
                num_buttons += self._icon_button(
                    DiscoveryState.UNDECIDED,
                    checkbox_name,
                    DiscoveryState.IGNORED,
                    "disabled",
                    button_classes,
                )

        while num_buttons < 2:
            html.empty_icon()
            num_buttons += 1

        if entry.check_source not in [
            DiscoveryState.UNDECIDED,
            DiscoveryState.IGNORED,
        ] and user.may("wato.rulesets"):
            num_buttons += self._rulesets_button(entry.description)
            num_buttons += self._check_parameters_button(entry)

        while num_buttons < 4:
            html.empty_icon()
            num_buttons += 1

    def _icon_button(
        self,
        table_source: Literal["new", "old", "ignored", "vanished"],
        checkbox_name: str,
        table_target: Literal["new", "old", "ignored"],
        descr_target: Literal["undecided", "monitored", "disabled"],
        button_classes: list[str],
    ) -> Literal[1]:
        options = self._options._replace(action=DiscoveryAction.SINGLE_UPDATE)
        html.icon_button(
            url="",
            title=_("Move to %s services") % descr_target,
            icon="service_to_%s" % descr_target,
            class_=button_classes,
            onclick=_start_js_call(
                self._host,
                options,
                request_vars={
                    "update_target": table_target,
                    "update_source": table_source,
                    "update_services": [checkbox_name],
                },
            ),
        )
        return 1

    def _icon_button_removed(self, table_source, checkbox_name, button_classes):
        options = self._options._replace(action=DiscoveryAction.SINGLE_UPDATE)
        html.icon_button(
            url="",
            title=_("Remove service"),
            icon="service_to_removed",
            class_=button_classes,
            onclick=_start_js_call(
                self._host,
                options,
                request_vars={
                    "update_target": DiscoveryState.REMOVED,
                    "update_source": table_source,
                    "update_services": [checkbox_name],
                },
            ),
        )
        return 1

    def _rulesets_button(self, descr):
        # Link to list of all rulesets affecting this service
        html.icon_button(
            folder_preserving_link(
                [
                    ("mode", "object_parameters"),
                    ("host", self._host.name()),
                    ("service", descr),
                ]
            ),
            _("View and edit the parameters for this service"),
            "rulesets",
        )
        return 1

    def _check_parameters_button(self, entry: CheckPreviewEntry):  # type: ignore[no-untyped-def]
        if not entry.ruleset_name:
            return 0

        if entry.check_source == DiscoveryState.MANUAL:
            url = folder_preserving_link(
                [
                    ("mode", "edit_ruleset"),
                    ("varname", "static_checks:" + entry.ruleset_name),
                    ("host", self._host.name()),
                ]
            )
        else:
            ruleset_name = self._get_ruleset_name(entry)
            if ruleset_name is None:
                return 0

            url = folder_preserving_link(
                [
                    ("mode", "edit_ruleset"),
                    ("varname", ruleset_name),
                    ("host", self._host.name()),
                    (
                        "item",
                        mk_repr(entry.item).decode(),
                    ),
                    (
                        "service",
                        mk_repr(entry.description).decode(),
                    ),
                ]
            )

        html.icon_button(
            url, _("Edit and analyze the check parameters of this service"), "check_parameters"
        )
        return 1

    def _disabled_services_button(self, descr):
        html.icon_button(
            folder_preserving_link(
                [
                    ("mode", "edit_ruleset"),
                    ("varname", "ignored_services"),
                    ("host", self._host.name()),
                    (
                        "item",
                        mk_repr(descr).decode(),
                    ),
                ]
            ),
            _("Edit and analyze the disabled services rules"),
            "rulesets",
        )
        return 1

    def _get_ruleset_name(self, entry: CheckPreviewEntry) -> str | None:
        if entry.ruleset_name == "logwatch":
            return "logwatch_rules"
        if entry.ruleset_name:
            return f"checkgroup_parameters:{entry.ruleset_name}"
        if entry.check_source in [DiscoveryState.ACTIVE, DiscoveryState.ACTIVE_IGNORED]:
            return f"active_checks:{entry.check_plugin_name}"
        return None

    @staticmethod
    def _ordered_table_groups() -> list[TableGroupEntry]:
        return [
            TableGroupEntry(
                table_group=DiscoveryState.UNDECIDED,
                show_bulk_actions=True,
                title=_("Undecided services - currently not monitored"),
                help_text=_(
                    "These services have been found by the service discovery but are not yet added "
                    "to the monitoring. You should either decide to monitor them or to permanently "
                    "disable them. If you are sure that they are just transitional, just leave them "
                    "until they vanish."
                ),
            ),
            TableGroupEntry(
                DiscoveryState.VANISHED,
                show_bulk_actions=True,
                title=_("Vanished services - monitored, but no longer exist"),
                help_text=_(
                    "These services had been added to the monitoring by a previous discovery "
                    "but the actual items that are monitored are not present anymore. This might "
                    "be due to a real failure. In that case you should leave them in the monitoring. "
                    "If the actually monitored things are really not relevant for the monitoring "
                    "anymore then you should remove them in order to avoid UNKNOWN services in the "
                    "monitoring."
                ),
            ),
            TableGroupEntry(
                DiscoveryState.CLUSTERED_VANISHED,
                show_bulk_actions=False,
                title=_("Vanished clustered services - located on cluster host"),
                help_text=_(
                    "These services are mapped to a cluster host by a rule in one of the rulesets "
                    "<i>%s</i> or <i>%s</i>."
                )
                % (
                    _("Clustered services"),
                    _("Clustered services for overlapping clusters"),
                )
                + " "
                + _(
                    "They have been found on this host previously, but have now disappeared. "
                    "Note that they may still be monitored on the cluster."
                ),
            ),
            TableGroupEntry(
                DiscoveryState.MONITORED,
                show_bulk_actions=True,
                title=_("Monitored services"),
                help_text=_(
                    "These services had been found by a discovery and are currently configured "
                    "to be monitored."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.IGNORED,
                show_bulk_actions=True,
                title=_("Disabled services"),
                help_text=_(
                    "These services are being discovered but have been disabled by creating a rule "
                    "in the rule set <i>Disabled services</i> or <i>Disabled checks</i>."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.ACTIVE,
                show_bulk_actions=False,
                title=_("Active checks"),
                help_text=_(
                    "These services do not use the Checkmk agent or Checkmk-SNMP engine but actively "
                    "call classical check plugins. They have been added by a rule in the section "
                    "<i>Active checks</i> or implicitely by Checkmk."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.MANUAL,
                show_bulk_actions=False,
                title=_("Enforced services"),
                help_text=_(
                    "These services have not been found by the discovery but have been added "
                    "manually by a Setup rule <i>Enforced services</i>."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.CUSTOM,
                show_bulk_actions=False,
                title=_("Custom checks - defined via rule"),
                help_text=_(
                    "These services do not use the Checkmk agent or Checkmk-SNMP engine but actively "
                    "call a classical check plugin, that you have installed yourself."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.CLUSTERED_OLD,
                show_bulk_actions=False,
                title=_("Monitored clustered services - located on cluster host"),
                help_text=_(
                    "These services are mapped to a cluster host by a rule in one of the rulesets "
                    "<i>%s</i> or <i>%s</i>."
                )
                % (
                    _("Clustered services"),
                    _("Clustered services for overlapping clusters"),
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.CLUSTERED_NEW,
                show_bulk_actions=False,
                title=_("Undecided clustered services"),
                help_text=_(
                    "These services are mapped to a cluster host by a rule in one of the rulesets "
                    "<i>%s</i> or <i>%s</i>."
                )
                % (
                    _("Clustered services"),
                    _("Clustered services for overlapping clusters"),
                )
                + " "
                + _("They appear to be new to this node, but may still be already monitored."),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.CLUSTERED_IGNORED,
                show_bulk_actions=False,
                title=_("Disabled clustered services - located on cluster host"),
                help_text=_(
                    "These services have been found on this host and have been mapped to "
                    "a cluster host by a rule in the set <i>Clustered services</i> but disabled via "
                    "<i>Disabled services</i> or <i>Disabled checks</i>."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.ACTIVE_IGNORED,
                show_bulk_actions=False,
                title=_("Disabled active checks"),
                help_text=_(
                    "These services do not use the Checkmk agent or Checkmk-SNMP engine but actively "
                    "call classical check plugins. They have been added by a rule in the section "
                    "<i>Active checks</i> or implicitely by Checkmk. "
                    "These services have been disabled by creating a rule in the rule set "
                    "<i>Disabled services</i> oder <i>Disabled checks</i>."
                ),
            ),
            TableGroupEntry(
                table_group=DiscoveryState.CUSTOM_IGNORED,
                show_bulk_actions=False,
                title=_("Disabled custom checks - defined via rule"),
                help_text=_(
                    "These services do not use the Checkmk agent or Checkmk-SNMP engine but actively "
                    "call a classical check plugin, that you have installed yourself. "
                    "These services have been disabled by creating a rule in the rule set "
                    "<i>Disabled services</i> oder <i>Disabled checks</i>."
                ),
            ),
        ]


@page_registry.register_page("wato_ajax_execute_check")
class ModeAjaxExecuteCheck(AjaxPage):
    def _from_vars(self):
        self._site = SiteId(request.get_ascii_input_mandatory("site"))
        if self._site not in sitenames():
            raise MKUserError("site", _("You called this page with an invalid site."))

        self._host_name = HostName(request.get_ascii_input_mandatory("host"))
        self._host = Folder.current().host(self._host_name)
        if not self._host:
            raise MKUserError("host", _("You called this page with an invalid host name."))
        self._host.need_permission("read")

        # TODO: Validate
        self._check_type = request.get_ascii_input_mandatory("checktype")
        # TODO: Validate
        self._item = request.get_str_input_mandatory("item")

    def page(self) -> PageResult:
        check_csrf_token()
        try:
            active_check_result = active_check(
                self._site,
                self._host_name,
                self._check_type,
                self._item,
            )
            state = 3 if active_check_result.state is None else active_check_result.state
            output = active_check_result.output
        except Exception as e:
            state = 3
            output = "%s" % e

        return {
            "state": state,
            "state_name": short_service_state_name(state, "UNKN"),
            "output": output,
        }


def service_page_menu(  # type: ignore[no-untyped-def]
    breadcrumb, host: CREHost, options: DiscoveryOptions
):
    menu = PageMenu(
        dropdowns=[
            PageMenuDropdown(
                name="actions",
                title=_("Actions"),
                topics=[
                    PageMenuTopic(
                        title=_("Discovery"),
                        entries=list(_page_menu_service_configuration_entries(host, options)),
                    ),
                    PageMenuTopic(
                        title=_("Services"),
                        entries=list(_page_menu_selected_services_entries(host, options)),
                    ),
                    PageMenuTopic(
                        title=_("Host labels"),
                        entries=list(_page_menu_host_labels_entries(host, options)),
                    ),
                ],
            ),
            PageMenuDropdown(
                name="host",
                title=_("Host"),
                topics=[
                    PageMenuTopic(
                        title=_("For this host"),
                        entries=list(_page_menu_host_entries(host)),
                    ),
                ],
            ),
            PageMenuDropdown(
                name="settings",
                title=_("Settings"),
                topics=[
                    PageMenuTopic(
                        title=_("Settings"),
                        entries=list(_page_menu_settings_entries(host)),
                    ),
                ],
            ),
        ],
        breadcrumb=breadcrumb,
    )

    _extend_display_dropdown(menu, host, options)
    _extend_help_dropdown(menu)
    return menu


def _page_menu_host_entries(host: CREHost) -> Iterator[PageMenuEntry]:
    yield PageMenuEntry(
        title=_("Properties"),
        icon_name="edit",
        item=make_simple_link(
            folder_preserving_link([("mode", "edit_host"), ("host", host.name())])
        ),
    )

    if not host.is_cluster():
        yield PageMenuEntry(
            title=_("Connection tests"),
            icon_name="diagnose",
            item=make_simple_link(
                folder_preserving_link([("mode", "diag_host"), ("host", host.name())])
            ),
        )

    if user.may("wato.rulesets"):
        yield PageMenuEntry(
            title=_("Effective parameters"),
            icon_name="rulesets",
            item=make_simple_link(
                folder_preserving_link([("mode", "object_parameters"), ("host", host.name())])
            ),
        )

    yield make_host_status_link(host_name=host.name(), view_name="hoststatus")

    if user.may("wato.auditlog"):
        yield PageMenuEntry(
            title=_("Audit log"),
            icon_name="auditlog",
            item=make_simple_link(make_object_audit_log_url(host.object_ref())),
        )


def _page_menu_settings_entries(host: CREHost) -> Iterator[PageMenuEntry]:
    if not user.may("wato.rulesets"):
        return

    if host.is_cluster():
        yield PageMenuEntry(
            title=_("Clustered services"),
            icon_name="rulesets",
            item=make_simple_link(
                folder_preserving_link(
                    [("mode", "edit_ruleset"), ("varname", "clustered_services")]
                )
            ),
        )

    yield PageMenuEntry(
        title=_("Disabled services"),
        icon_name={
            "icon": "services",
            "emblem": "disable",
        },
        item=make_simple_link(
            folder_preserving_link([("mode", "edit_ruleset"), ("varname", "ignored_services")])
        ),
    )

    yield PageMenuEntry(
        title=_("Disabled checks"),
        icon_name={
            "icon": "check_plugins",
            "emblem": "disable",
        },
        item=make_simple_link(
            folder_preserving_link([("mode", "edit_ruleset"), ("varname", "ignored_checks")])
        ),
    )


def _extend_display_dropdown(menu: PageMenu, host: CREHost, options: DiscoveryOptions) -> None:
    display_dropdown = menu.get_dropdown_by_name("display", make_display_options_dropdown())
    display_dropdown.topics.insert(
        0,
        PageMenuTopic(
            title=_("Details"),
            entries=[
                _page_menu_entry_show_checkboxes(host, options),
                _page_menu_entry_show_parameters(host, options),
                _page_menu_entry_show_discovered_labels(host, options),
                _page_menu_entry_show_plugin_names(host, options),
            ],
        ),
    )


def _extend_help_dropdown(menu: PageMenu) -> None:
    menu.add_doc_reference(_("Beginner's guide: Configuring services"), DocReference.INTRO_SERVICES)
    menu.add_doc_reference(_("Understanding and configuring services"), DocReference.WATO_SERVICES)


def _page_menu_entry_show_parameters(host: CREHost, options: DiscoveryOptions) -> PageMenuEntry:
    return PageMenuEntry(
        title=_("Show check parameters"),
        icon_name="toggle_on" if options.show_parameters else "toggle_off",
        item=make_simple_link(
            _checkbox_js_url(
                host,
                options._replace(show_parameters=not options.show_parameters),
            )
        ),
        name="show_parameters",
        css_classes=["toggle"],
    )


def _page_menu_entry_show_checkboxes(host: CREHost, options: DiscoveryOptions) -> PageMenuEntry:
    return PageMenuEntry(
        title=_("Show checkboxes"),
        icon_name="toggle_on" if options.show_checkboxes else "toggle_off",
        item=make_simple_link(
            _checkbox_js_url(
                host,
                options._replace(show_checkboxes=not options.show_checkboxes),
            )
        ),
        name="show_checkboxes",
        css_classes=["toggle"],
    )


def _checkbox_js_url(host: CREHost, options: DiscoveryOptions) -> str:
    return "javascript:%s" % make_javascript_action(_start_js_call(host, options))


def _page_menu_entry_show_discovered_labels(
    host: CREHost, options: DiscoveryOptions
) -> PageMenuEntry:
    return PageMenuEntry(
        title=_("Show discovered service labels"),
        icon_name="toggle_on" if options.show_discovered_labels else "toggle_off",
        item=make_simple_link(
            _checkbox_js_url(
                host,
                options._replace(show_discovered_labels=not options.show_discovered_labels),
            )
        ),
        name="show_discovered_labels",
        css_classes=["toggle"],
    )


def _page_menu_entry_show_plugin_names(host: CREHost, options: DiscoveryOptions) -> PageMenuEntry:
    return PageMenuEntry(
        title=_("Show plugin names"),
        icon_name="toggle_on" if options.show_plugin_names else "toggle_off",
        item=make_simple_link(
            _checkbox_js_url(
                host,
                options._replace(show_plugin_names=not options.show_plugin_names),
            )
        ),
        name="show_plugin_names",
        css_classes=["toggle"],
    )


def _page_menu_service_configuration_entries(
    host: CREHost, options: DiscoveryOptions
) -> Iterator[PageMenuEntry]:
    yield PageMenuEntry(
        title=_("Accept all"),
        icon_name="accept",
        item=make_javascript_link(
            _start_js_call(
                host,
                options._replace(action=DiscoveryAction.FIX_ALL),
            ),
        ),
        name="fixall",
        is_enabled=False,
        is_shortcut=True,
        css_classes=["action"],
    )

    yield PageMenuEntry(
        title=_("Rescan"),
        icon_name="services_refresh",
        item=make_javascript_link(
            _start_js_call(host, options._replace(action=DiscoveryAction.REFRESH))
        ),
        name="refresh",
        is_enabled=False,
        is_shortcut=True,
        css_classes=["action"],
    )

    yield PageMenuEntry(
        title=_("Remove all and find new"),
        icon_name="services_tabula_rasa",
        item=make_javascript_link(
            _start_js_call(host, options._replace(action=DiscoveryAction.TABULA_RASA))
        ),
        name="tabula_rasa",
        is_enabled=False,
        css_classes=["action"],
    )

    yield PageMenuEntry(
        title=_("Stop job"),
        icon_name="services_stop",
        item=make_javascript_link(
            _start_js_call(host, options._replace(action=DiscoveryAction.STOP))
        ),
        name="stop",
        is_enabled=False,
        css_classes=["action"],
    )


class BulkEntry(NamedTuple):
    is_shortcut: bool
    is_show_more: bool
    source: str
    target: str
    title: str
    explanation: str | None


def _page_menu_selected_services_entries(
    host: CREHost, options: DiscoveryOptions
) -> Iterator[PageMenuEntry]:
    yield PageMenuEntry(
        title=_("Add missing, remove vanished"),
        icon_name="services_fix_all",
        item=make_javascript_link(
            _start_js_call(host, options._replace(action=DiscoveryAction.UPDATE_SERVICES))
        ),
        name="fix_all",
        is_enabled=False,
        is_shortcut=False,
        css_classes=["action"],
    )

    for entry in [
        BulkEntry(
            True,
            False,
            DiscoveryState.UNDECIDED,
            DiscoveryState.MONITORED,
            _("Monitor undecided services"),
            _("Add all detected but not yet monitored services to the monitoring."),
        ),
        BulkEntry(
            False,
            False,
            DiscoveryState.UNDECIDED,
            DiscoveryState.IGNORED,
            _("Disable undecided services"),
            None,
        ),
        BulkEntry(
            False,
            True,
            DiscoveryState.MONITORED,
            DiscoveryState.UNDECIDED,
            _("Declare monitored services as undecided"),
            None,
        ),
        BulkEntry(
            False,
            True,
            DiscoveryState.MONITORED,
            DiscoveryState.IGNORED,
            _("Disable monitored services"),
            None,
        ),
        BulkEntry(
            False,
            True,
            DiscoveryState.IGNORED,
            DiscoveryState.MONITORED,
            _("Monitor disabled services"),
            None,
        ),
        BulkEntry(
            False,
            True,
            DiscoveryState.IGNORED,
            DiscoveryState.UNDECIDED,
            _("Declare disabled services as undecided"),
            None,
        ),
        BulkEntry(
            True,
            False,
            DiscoveryState.VANISHED,
            DiscoveryState.REMOVED,
            _("Remove vanished services"),
            None,
        ),
        BulkEntry(
            False,
            True,
            DiscoveryState.VANISHED,
            DiscoveryState.IGNORED,
            _("Disable vanished services"),
            None,
        ),
    ]:
        yield PageMenuEntry(
            title=entry.title,
            icon_name="service_to_%s" % entry.target,
            item=make_javascript_link(
                _start_js_call(
                    host,
                    options._replace(action=DiscoveryAction.BULK_UPDATE),
                    request_vars={
                        "update_target": entry.target,
                        "update_source": entry.source,
                    },
                )
            ),
            name=f"bulk_{entry.source}_{entry.target}",
            is_enabled=False,
            is_shortcut=entry.is_shortcut,
            is_show_more=entry.is_show_more,
            css_classes=["action"],
        )


def _page_menu_host_labels_entries(
    host: CREHost, options: DiscoveryOptions
) -> Iterator[PageMenuEntry]:
    yield PageMenuEntry(
        title=_("Update host labels"),
        icon_name="update_host_labels",
        item=make_javascript_link(
            _start_js_call(host, options._replace(action=DiscoveryAction.UPDATE_HOST_LABELS))
        ),
        name="update_host_labels",
        is_enabled=False,
        is_shortcut=False,
        is_suggested=True,
        css_classes=["action"],
    )


def _start_js_call(
    host: CREHost, options: DiscoveryOptions, request_vars: dict | None = None
) -> str:
    return "cmk.service_discovery.start({}, {}, {}, {}, {})".format(
        json.dumps(host.name()),
        json.dumps(host.folder().path()),
        json.dumps(options._asdict()),
        json.dumps(transactions.get()),
        json.dumps(request_vars),
    )
