#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import json
import traceback
from collections.abc import Collection, Iterator

import cmk.gui.gui_background_job as gui_background_job
from cmk.gui.background_job import BackgroundJob, BackgroundStatusSnapshot, job_registry
from cmk.gui.breadcrumb import Breadcrumb
from cmk.gui.htmllib.html import html
from cmk.gui.http import request
from cmk.gui.i18n import _
from cmk.gui.log import logger
from cmk.gui.page_menu import (
    make_simple_link,
    PageMenu,
    PageMenuDropdown,
    PageMenuEntry,
    PageMenuTopic,
)
from cmk.gui.pages import AjaxPage, page_registry, PageResult
from cmk.gui.plugins.wato.utils import (
    ABCMainModule,
    main_module_registry,
    MainModuleTopicMaintenance,
    mode_registry,
    mode_url,
    redirect,
    WatoMode,
)
from cmk.gui.plugins.wato.utils.main_menu import MainModuleTopic
from cmk.gui.type_defs import ActionResult, Icon, PermissionName
from cmk.gui.utils.output_funnel import output_funnel
from cmk.gui.utils.urls import makeuri_contextless


@main_module_registry.register
class MainModuleBackgroundJobs(ABCMainModule):
    @property
    def mode_or_url(self) -> str:
        return "background_jobs_overview"

    @property
    def topic(self) -> MainModuleTopic:
        return MainModuleTopicMaintenance

    @property
    def title(self) -> str:
        return _("Background jobs")

    @property
    def icon(self) -> Icon:
        return "background_jobs"

    @property
    def permission(self) -> None | str:
        return "background_jobs.manage_jobs"

    @property
    def description(self) -> str:
        return _("Manage longer running tasks in the Checkmk GUI")

    @property
    def sort_index(self) -> int:
        return 60

    @property
    def is_show_more(self) -> bool:
        return True


@mode_registry.register
class ModeBackgroundJobsOverview(WatoMode):
    @classmethod
    def name(cls) -> str:
        return "background_jobs_overview"

    @staticmethod
    def static_permissions() -> Collection[PermissionName]:
        return ["background_jobs.manage_jobs"]

    def title(self) -> str:
        return _("Background jobs overview")

    def page(self) -> None:
        job_manager = gui_background_job.GUIBackgroundJobManager()

        back_url = makeuri_contextless(request, [("mode", "background_jobs_overview")])
        job_manager.show_status_of_job_classes(job_registry.values(), job_details_back_url=back_url)

        if any(job_manager.get_running_job_ids(c) for c in job_registry.values()):
            html.immediate_browser_redirect(0.8, "")

    # Mypy requires the explicit return, pylint does not like it.
    def action(self) -> ActionResult:  # pylint: disable=useless-return
        action_handler = gui_background_job.ActionHandler(self.breadcrumb())
        action_handler.handle_actions()
        return None


@mode_registry.register
class ModeBackgroundJobDetails(WatoMode):
    @classmethod
    def name(cls) -> str:
        return "background_job_details"

    @staticmethod
    def static_permissions() -> Collection[PermissionName]:
        return []

    @classmethod
    def parent_mode(cls) -> type[WatoMode] | None:
        return ModeBackgroundJobsOverview

    def title(self) -> str:
        return _("Background job details")

    def page_menu(self, breadcrumb: Breadcrumb) -> PageMenu:
        return PageMenu(
            dropdowns=[
                PageMenuDropdown(
                    name="related",
                    title=_("Related"),
                    topics=[
                        PageMenuTopic(
                            title=_("Setup"),
                            entries=list(self._page_menu_entries_related()),
                        ),
                    ],
                ),
            ],
            breadcrumb=breadcrumb,
        )

    def _page_menu_entries_related(self) -> Iterator[PageMenuEntry]:
        back_url = self.back_url()
        # Small hack to not have a "up" and "back" link both pointing to the parent mode
        if back_url and "mode=background_jobs_overview" not in back_url:
            yield PageMenuEntry(
                title=_("Back"),
                icon_name="back",
                item=make_simple_link(back_url),
                is_shortcut=True,
                is_suggested=True,
            )

    def back_url(self):
        return request.get_url_input("back_url", deflt="")

    def page(self) -> None:
        html.div(html.render_message(_("Loading...")), id_="async_progress_msg")
        html.div("", id_="status_container")
        html.javascript(
            "cmk.background_job.start('ajax_background_job_details.py', %s)"
            % json.dumps(request.get_ascii_input_mandatory("job_id"))
        )


@page_registry.register_page("ajax_background_job_details")
class ModeAjaxBackgroundJobDetails(AjaxPage):
    """AJAX handler for supporting the background job state update"""

    def handle_page(self) -> None:
        self.action()
        super().handle_page()

    def page(self) -> PageResult:
        with output_funnel.plugged():
            api_request = request.get_request()
            job_snapshot = self._show_details_page(api_request["job_id"])
            content = output_funnel.drain()

        return {
            "status_container_content": content,
            "is_finished": job_snapshot and not job_snapshot.is_active,
        }

    def _show_details_page(self, job_id: str) -> BackgroundStatusSnapshot | None:
        job = BackgroundJob(job_id)
        if not job.exists():
            html.show_message(_("Background job info is not available"))
            return None

        try:
            job_snapshot = job.get_status_snapshot()
        except Exception:
            html.show_message(_("Background job info is not available"))
            logger.error(traceback.format_exc())
            return None

        job_manager = gui_background_job.GUIBackgroundJobManager()
        job_manager.show_job_details_from_snapshot(job_snapshot)
        return job_snapshot

    def action(self) -> None:
        job_details_page = ModeBackgroundJobDetails()
        action_handler = gui_background_job.ActionHandler(job_details_page.breadcrumb())
        action_handler.handle_actions()
        if action_handler.did_delete_job():
            if job_details_page.back_url():
                raise redirect(job_details_page.back_url())
            raise redirect(mode_url("background_jobs_overview"))
