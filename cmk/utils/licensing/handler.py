#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import auto, Enum
from typing import NamedTuple


class LicenseState(Enum):
    """All possible license states of the Checkmk site"""

    TRIAL = auto()
    FREE = auto()
    LICENSED = auto()
    UNLICENSED = auto()

    @property
    def readable(self) -> str:
        if self is LicenseState.TRIAL:
            return "trial"
        if self is LicenseState.FREE:
            return "free"
        if self is LicenseState.LICENSED:
            return "licensed"
        if self is LicenseState.UNLICENSED:
            return "unlicensed"
        raise ValueError()


class LicenseStateError(Exception):
    pass


@dataclass
class EmailNotification:
    period: timedelta
    remaining_time: timedelta
    subject: str
    message: str


@dataclass
class HeaderNotification:
    roles: Sequence[str]
    message: str


@dataclass
class ActivationBlock:
    message: str


@dataclass
class UserEffect:
    header: HeaderNotification | None
    email: EmailNotification | None
    block: ActivationBlock | None


class NotificationHandler(abc.ABC):
    def __init__(self, email_notification: EmailNotification | None) -> None:
        self._email_notification = email_notification

    @abc.abstractmethod
    def manage_notification(self) -> None:
        raise NotImplementedError()


class RemainingTrialTime(NamedTuple):
    days: int
    perc: float


class LicensingHandler(abc.ABC):
    @classmethod
    @abc.abstractmethod
    def make(cls) -> LicensingHandler:
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def state(self) -> LicenseState:
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def message(self) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    def effect_core(self, num_services: int, num_hosts_shadow: int) -> UserEffect:
        raise NotImplementedError()

    @abc.abstractmethod
    def effect(self, licensing_settings_link: str | None = None) -> UserEffect:
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def notification_handler(self) -> NotificationHandler:
        raise NotImplementedError()

    @property
    def remaining_trial_time(self) -> RemainingTrialTime:
        raise NotImplementedError()
