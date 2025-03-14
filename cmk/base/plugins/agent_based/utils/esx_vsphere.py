#!/usr/bin/env python3
# Copyright (C) 2020 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
import enum
from collections import OrderedDict
from typing import Mapping, Sequence

from pydantic import BaseModel

Section = OrderedDict

CounterValues = Sequence[str]
SubSectionCounter = Mapping[str, list[tuple[CounterValues, str]]]
SectionCounter = Mapping[str, SubSectionCounter]


class ESXMemory(BaseModel):
    """ESX VSphere VM memory model
    host_usage:
        consumed host memory
    guest_usage:
        active guest memory
    ballooned:
        size of the balloon driver in the VM
    shared:
        the portion of memory, in MB, that is granted to this VM from non-shared memory
        (must not be set)
    private:
        the portion of memory, in MB, that is granted to this VM from host memory that is shared
        between VMs.
    """

    host_usage: float
    guest_usage: float
    ballooned: float
    shared: float
    private: float


class ESXCpu(BaseModel):
    """ESX VSphere VM cpu model

    overall_usage:
        overall cpu usage in Mhz
    cpus_count:
        count of virtual CPUs
    cores_per_socket:
        number of cores per socket
    """

    overall_usage: int
    cpus_count: int
    cores_per_socket: int

    class Config:
        allow_mutation = False


class ESXDataStore(BaseModel):
    name: str
    free_space: float
    capacity: float


class ESXStatus(enum.Enum):
    guestToolsCurrent = "guestToolsCurrent"
    guestToolsNeedUpgrade = "guestToolsNeedUpgrade"
    guestToolsNotInstalled = "guestToolsNotInstalled"
    guestToolsUnmanaged = "guestToolsUnmanaged"


class HeartBeatStatus(enum.Enum):
    GRAY = "GRAY"
    GREEN = "GREEN"
    RED = "RED"
    YELLOW = "YELLOW"
    UNKNOWN = "UNKNOWN"


class HeartBeat(BaseModel):
    status: HeartBeatStatus
    value: str


class ESXVm(BaseModel):
    mounted_devices: Sequence[str]
    snapshots: Sequence[str]
    status: ESXStatus | None
    power_state: str | None
    memory: ESXMemory | None
    cpu: ESXCpu | None
    datastores: Sequence[ESXDataStore] | None
    heartbeat: HeartBeat | None
    host: str | None
    name: str | None

    class Config:
        allow_mutation = False


SectionVM = ESXVm | None


def average_parsed_data(values: CounterValues) -> float:
    """
    >>> average_parsed_data(['1', '2'])
    1.5
    >>> average_parsed_data(['1'])
    1.0
    >>> average_parsed_data([])
    0
    """
    return sum(map(int, values)) / len(values) if values else 0
