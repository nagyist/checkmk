#!/usr/bin/env python3
# Copyright (C) 2021 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Synchronize discovered host labels from remote site to central site"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Any

from livestatus import SiteConfiguration, SiteId

import cmk.utils.store as store
from cmk.utils.exceptions import MKGeneralException
from cmk.utils.labels import (
    get_host_labels_entry_of_host,
    get_updated_host_label_files,
    save_updated_host_label_files,
    UpdatedHostLabelsEntry,
)
from cmk.utils.type_defs import HostName

import cmk.gui.log as log
from cmk.gui.background_job import (
    BackgroundJob,
    BackgroundJobAlreadyRunning,
    BackgroundProcessInterface,
    InitialStatusArgs,
    job_registry,
)
from cmk.gui.exceptions import MKUserError
from cmk.gui.http import request
from cmk.gui.i18n import _
from cmk.gui.log import logger
from cmk.gui.site_config import get_site_config, has_wato_slave_sites, wato_slave_sites
from cmk.gui.utils.request_context import copy_request_context
from cmk.gui.watolib.automation_commands import automation_command_registry, AutomationCommand
from cmk.gui.watolib.automations import do_remote_automation, MKAutomationException
from cmk.gui.watolib.hosts_and_folders import Host
from cmk.gui.watolib.paths import wato_var_dir


@dataclass
class SiteResult:
    site_id: SiteId
    success: bool
    error: str
    updated_host_labels: list[UpdatedHostLabelsEntry]


@dataclass
class EnforcedHostRequest:
    site_id: SiteId
    host_name: HostName


@dataclass
class SiteRequest:
    newest_host_labels: float
    enforce_host: EnforcedHostRequest | None

    @classmethod
    def deserialize(cls, serialized: dict[str, Any]) -> SiteRequest:
        enforce_host = (
            EnforcedHostRequest(**serialized["enforce_host"])
            if serialized["enforce_host"]
            else None
        )

        if enforce_host:
            host = Host.host(enforce_host.host_name)
            if host is None:
                raise MKGeneralException(
                    _(
                        "Host %s does not exist on remote site %s. This "
                        "may be caused by a failed configuration synchronization. Have a look at "
                        'the <a href="wato.py?folder=&mode=changelog">activate changes page</a> '
                        "for further information."
                    )
                    % (enforce_host.host_name, enforce_host.site_id)
                )
            host.need_permission("read")

        newest_host_labels = serialized["newest_host_labels"]
        assert isinstance(newest_host_labels, float)
        return cls(newest_host_labels, enforce_host)

    def serialize(self) -> dict[str, Any]:
        return {
            "newest_host_labels": self.newest_host_labels,
            "enforce_host": asdict(self.enforce_host) if self.enforce_host else None,
        }


@dataclass
class DiscoveredHostLabelSyncResponse:
    updated_host_labels: list[UpdatedHostLabelsEntry]


def execute_host_label_sync(host_name: HostName, site_id: SiteId) -> None:
    """Contacts the given remote site to synchronize the labels of the given host"""
    site_spec = get_site_config(site_id)
    result = _execute_site_sync(
        site_id,
        site_spec,
        SiteRequest(
            newest_host_labels=0.0,
            enforce_host=EnforcedHostRequest(site_id, host_name),
        ),
    )
    save_updated_host_label_files(result.updated_host_labels)


def execute_host_label_sync_job() -> DiscoveredHostLabelSyncJob | None:
    """This function is called by the GUI cron job once a minute.
    Errors are logged to var/log/web.log."""
    if not has_wato_slave_sites():
        return None

    job = DiscoveredHostLabelSyncJob()

    try:
        job.start(job.do_sync)
    except BackgroundJobAlreadyRunning:
        logger.debug("Another synchronization job is already running: Skipping this sync")

    return job


@job_registry.register
class DiscoveredHostLabelSyncJob(BackgroundJob):
    """This job synchronizes the discovered host labels from remote sites to the central site

    Currently they are only needed for the agent bakery, but may be used in other places in the
    future.
    """

    job_prefix = "discovered_host_label_sync"

    @classmethod
    def gui_title(cls) -> str:
        return _("Discovered host label synchronization")

    def __init__(self) -> None:
        super().__init__(
            job_id=self.job_prefix,
            initial_status_args=InitialStatusArgs(
                title=self.gui_title(),
                stoppable=False,
            ),
        )

    def do_sync(self, job_interface: BackgroundProcessInterface) -> None:
        job_interface.send_progress_update(_("Synchronization started..."))
        self._execute_sync()
        job_interface.send_result_message(_("The synchronization finished."))

    def _execute_sync(self) -> None:
        newest_host_labels = self._load_newest_host_labels_per_site()

        with ThreadPool(20) as pool:
            results = pool.map(
                copy_request_context(self._execute_site_sync_bg),
                [
                    (
                        site_id,
                        site_spec,
                        SiteRequest(newest_host_labels.get(site_id, 0.0), None),
                    )
                    for site_id, site_spec in wato_slave_sites().items()
                ],
            )

        self._process_site_sync_results(newest_host_labels, results)

    def _execute_site_sync_bg(
        self,
        args: tuple[
            SiteId,
            SiteConfiguration,
            SiteRequest,
        ],
    ) -> SiteResult:
        log.init_logging()  # NOTE: We run in a subprocess!
        return _execute_site_sync(*args)

    def _process_site_sync_results(
        self, newest_host_labels: dict[SiteId, float], results: list[SiteResult]
    ) -> None:
        """Persist the sync results received from the remote site on the central site"""
        for site_result in results:
            if not site_result.updated_host_labels:
                continue

            newest_host_labels[site_result.site_id] = max(
                [newest_host_labels.get(site_result.site_id, 0.0)]
                + [e[1] for e in site_result.updated_host_labels]
            )
            save_updated_host_label_files(site_result.updated_host_labels)

        self._save_newest_host_labels_per_site(newest_host_labels)

    @staticmethod
    def newest_host_labels_per_site_path() -> Path:
        return wato_var_dir() / "newest_host_labels_per_site.mk"

    def _load_newest_host_labels_per_site(self) -> dict[SiteId, float]:
        return store.load_object_from_file(
            DiscoveredHostLabelSyncJob.newest_host_labels_per_site_path(), default={}
        )

    def _save_newest_host_labels_per_site(self, newest_host_labels: dict[SiteId, float]) -> None:
        store.save_object_to_file(
            DiscoveredHostLabelSyncJob.newest_host_labels_per_site_path(), newest_host_labels
        )


def _execute_site_sync(
    site_id: SiteId, site_spec: SiteConfiguration, site_request: SiteRequest
) -> SiteResult:
    """Executes the sync with a site. Is executed in a dedicated subprocess (One per site)"""
    try:
        logger.debug(_("[%s] Starting sync for site"), site_id)

        # timeout=100: Use a value smaller than the default apache request timeout
        raw_result = do_remote_automation(
            site_spec,
            "discovered-host-label-sync",
            [
                ("request", repr(site_request.serialize())),
            ],
            timeout=100,
        )
        assert isinstance(raw_result, dict)
        result = DiscoveredHostLabelSyncResponse(**raw_result)

        logger.debug(_("[%s] Finished sync for site"), site_id)
        return SiteResult(
            site_id=site_id,
            success=True,
            error="",
            updated_host_labels=result.updated_host_labels,
        )

    except MKAutomationException as e:
        return SiteResult(
            site_id=site_id,
            success=False,
            error=str(e),
            updated_host_labels=[],
        )

    except Exception as e:
        logger.error("Exception (%s, discovered_host_label_sync)", site_id, exc_info=True)
        return SiteResult(
            site_id=site_id,
            success=False,
            error=str(e),
            updated_host_labels=[],
        )


@automation_command_registry.register
class AutomationDiscoveredHostLabelSync(AutomationCommand):
    """Called by execute_site_sync to perform the sync with a remote site"""

    def command_name(self) -> str:
        return "discovered-host-label-sync"

    def get_request(self) -> SiteRequest:
        ascii_input = request.get_ascii_input("request")
        if ascii_input is None:
            raise MKUserError("request", _('The parameter "%s" is missing.') % "request")
        return SiteRequest.deserialize(ast.literal_eval(ascii_input))

    def execute(self, api_request: SiteRequest) -> dict[str, Any]:
        if api_request.enforce_host:
            try:
                response = DiscoveredHostLabelSyncResponse(
                    [get_host_labels_entry_of_host(api_request.enforce_host.host_name)]
                )
            except FileNotFoundError:
                response = DiscoveredHostLabelSyncResponse([])
        else:
            response = DiscoveredHostLabelSyncResponse(
                get_updated_host_label_files(newer_than=api_request.newest_host_labels)
            )

        return asdict(response)
