#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
import contextlib
import re
import time
from dataclasses import dataclass
from html import escape
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    Iterator,
    List,
    Literal,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from ..agent_based_api.v1 import (
    check_levels,
    get_average,
    get_rate,
    get_value_store,
    HostLabel,
    IgnoreResultsError,
    Metric,
    regex,
    render,
    Result,
    Service,
    State,
)
from ..agent_based_api.v1.type_defs import CheckResult, DiscoveryResult, HostLabelGenerator
from . import cpu, memory

# typing: nothing intentional, just adapt to sad reality
_ProcessValue = Tuple[Union[str, float], str]
_Process = List[Tuple[str, _ProcessValue]]


@dataclass(frozen=True)
class PsInfo:
    user: Optional[str] = None
    virtual: Optional[int] = None
    physical: Optional[int] = None
    # TODO: not all of these should be strings, I guess.
    cputime: Optional[str] = None
    process_id: Optional[str] = None
    pagefile: Optional[str] = None
    usermode_time: Optional[str] = None
    kernelmode_time: Optional[str] = None
    handles: Optional[str] = None
    threads: Optional[str] = None
    uptime: Optional[str] = None
    cgroup: Optional[str] = None

    _FIELDS = (
        "user",
        "virtual",
        "physical",
        "cputime",
        "process_id",
        "pagefile",
        "usermode_time",
        "kernelmode_time",
        "handles",
        "threads",
        "uptime",
        "cgroup",
    )

    @classmethod
    def from_raw(cls, raw: str) -> "PsInfo":
        match = regex(r"^\((.*)\)$").match(raw)
        if match is None:
            raise ValueError(raw)

        kwargs = dict(zip(cls._FIELDS, match.group(1).split(",")))
        virt = kwargs.pop("virtual", "")
        phys = kwargs.pop("physical", "")

        return cls(
            virtual=int(virt) if virt else None,
            physical=int(phys) if phys else None,
            **kwargs,
        )


Section = Tuple[int, Sequence[Tuple[PsInfo, Sequence[str]]]]

_InventorySpec = Tuple[
    str,
    Optional[str],
    Optional[Union[str, Literal[False]]],
    Tuple[Optional[str], bool],
    Mapping[str, str],
    Mapping[str, Any],
]


def get_discovery_specs(params: Sequence[Mapping[str, Any]]) -> Sequence[_InventorySpec]:
    inventory_specs = []
    for value in params[:-1]:  # skip empty default parameters
        inventory_specs.append(
            (
                value["descr"],
                value.get("match"),
                value.get("user"),
                value.get("cgroup", (None, False)),
                value.get("label", {}),
                value["default_params"],
            )
        )
    return inventory_specs


def host_labels_ps(
    params: Sequence[Mapping[str, Any]],
    section: Section,
) -> HostLabelGenerator:
    """Host label function

    Labels:
        This function creates labels according to the user configuration.

    """
    specs = get_discovery_specs(params)
    for process_info, command_line in section[1]:
        for _servicedesc, pattern, userspec, cgroupspec, labels, _default_params in specs:
            # First entry in line is the node name or None for non-clusters
            if not process_attributes_match(process_info, userspec, cgroupspec):
                continue
            matches = process_matches(command_line, pattern)
            if not matches:
                continue  # skip not matched lines
            yield from (HostLabel(*item) for item in labels.items())


def minn(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def maxx(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def replace_service_description(service_description, match_groups, pattern):
    # New in 1.2.2b4: All %1, %2, etc. to be replaced with first, second, ...
    # group. This allows a reordering of the matched groups
    # replace all %1:
    description_template, count = re.subn(r"%(\d+)", r"{\1}", service_description)
    # replace plain %s:
    total_replacements_count = count + description_template.count("%s")
    for number in range(count + 1, total_replacements_count + 1):
        description_template = description_template.replace("%s", "{%d}" % number, 1)

    # It is allowed (1.1.4) that the pattern contains more subexpressions
    # then the service description. In that case only the first
    # subexpressions are used as item.
    try:
        # First argument is None, because format is zero indexed
        return description_template.format(None, *(g or "" for g in match_groups))
    except IndexError:
        raise ValueError(
            "Invalid entry in inventory_processes_rules: service description '%s' contains %d "
            "replaceable elements, but regular expression %r contains only %d subexpression(s)."
            % (service_description, total_replacements_count, pattern, len(match_groups))
        )


def match_attribute(attribute, pattern):
    if not pattern:
        return True

    if attribute is None:
        return False

    if pattern.startswith("~"):
        return bool(regex(pattern[1:]).match(attribute))

    return pattern == attribute


def process_attributes_match(process_info, userspec, cgroupspec):
    cgroup_pattern, invert = cgroupspec
    if process_info.cgroup and (match_attribute(process_info.cgroup, cgroup_pattern) is invert):
        return False

    if not match_attribute(process_info.user, userspec):
        return False

    return True


def process_matches(
    command_line: Sequence[str],
    process_pattern: str | None,
    match_groups: Sequence[str | None] | None = None,
) -> bool | re.Match[str]:
    if not process_pattern:
        # Process name not relevant
        return True

    if process_pattern.startswith("~"):
        # Regex for complete process command line
        reg = regex(process_pattern[1:])  # skip "~"
        m = reg.match(" ".join(command_line))
        if not m:
            return False
        if match_groups:
            # Versions prior to 1.5.0p20 discovered a list, so keep tuple conversion!
            return m.groups() == tuple(match_groups)
        return m

    # Exact match on name of executable
    return bool(command_line) and command_line[0] == process_pattern


# produce text or html output intended for the long output field of a check
# from details about a process.  the input is expected to be a list (one
# per process) of lists (one per data field) of key-value tuples where the
# value is again a 2-field tuple, first is the value, second is the unit.
# This function is actually fairly generic so it could be used for other
# data structured the same way
def format_process_list(processes: Iterable[_Process], html_output: bool) -> str:
    def format_value(pvalue: _ProcessValue) -> str:
        value, unit = pvalue
        if unit == "kB":
            return render.bytes(float(value) * 1024)
        if isinstance(value, float):
            return "%.1f%s" % (value, unit)
        unescaped = "%s%s" % (value, unit)
        # Handling of backslash-n vs newline is fundamentally broken when talking to the core.
        # If we're creating HTML anyway, we can circumnavigate that...
        return escape(unescaped).replace("\\", "&bsol;") if html_output else unescaped

    # keys to output and default values:
    headers: Mapping[str, _ProcessValue] = {
        key: ("", "") for process in processes for key, _value in process
    }

    if html_output:
        table_bracket = "<table>%s</table>"
        line_bracket = "<tr>%s</tr>"
        cell_bracket = "<td>%.0s%s</td>"
        cell_seperator = ""
        header_line = "<tr><th>" + "</th><th>".join(headers) + "</th></tr>"

        # make sure each process has all fields from the table
        process_list = [list({**headers, **dict(process)}.items()) for process in processes]

    else:
        table_bracket = "%s"
        line_bracket = "%s\r\n"
        cell_bracket = "%s %s"
        cell_seperator = ", "
        header_line = ""
        process_list = list(processes)

    return table_bracket % (
        header_line
        + "".join(
            [
                line_bracket
                % cell_seperator.join(
                    [
                        cell_bracket % (key, format_value(value))
                        for key, value in process
                        if key in headers
                    ]
                )
                for process in process_list
            ]
        )
    )


def parse_ps_time(text: str) -> int:
    """Parse time as output by ps into seconds

    >>> parse_ps_time("12:17")
    737
    >>> parse_ps_time("55:12:17")
    198737
    >>> parse_ps_time("7-12:34:59")
    650099
    >>> parse_ps_time("650099")
    650099

    """
    if "-" in text:
        tokens = text.split("-")
        days = int(tokens[0] or 0)
        text = tokens[1]
    else:
        days = 0

    day_secs = sum(
        factor * int(v or 0) for factor, v in zip([1, 60, 3600], reversed(text.split(":")))
    )

    return 86400 * days + day_secs


def cpu_rate(value_store, counter, now, lifetime):
    try:
        return get_rate(value_store, counter, now, lifetime)
    except IgnoreResultsError:
        return 0


class ProcessAggregator:
    """Collects information about all instances of monitored processes"""

    def __init__(self, cpu_cores, params) -> None:  # type: ignore[no-untyped-def]
        self.cpu_cores = cpu_cores
        self.params = params
        self.virtual_size = 0
        self.resident_size = 0
        self.handle_count = 0
        self.percent_cpu = 0.0
        self.max_elapsed: Optional[float] = None
        self.min_elapsed: Optional[float] = None
        self.processes: List[_Process] = []
        self.running_on_nodes: set = set()

    def __getitem__(self, item: int) -> _Process:
        return self.processes[item]

    def __iter__(self) -> Iterator[_Process]:
        return iter(self.processes)

    @property
    def count(self) -> int:
        return len(self.processes)

    def append(self, process: _Process) -> None:
        self.processes.append(process)

    def core_weight(self, is_win):
        cpu_rescale_max = self.params.get("cpu_rescale_max")

        if any(
            (
                # Rule not set up, only windows scaled
                cpu_rescale_max == "cpu_rescale_max_unspecified" and not is_win,
                # Current rule is set. Explicitly ask not to divide
                cpu_rescale_max is False,
                # Domino tasks counter
                cpu_rescale_max is None,
            )
        ):
            return 1.0

        # Use default of division
        return 1.0 / self.cpu_cores

    def lifetimes(self, process_info, process: _Process):  # type: ignore[no-untyped-def]
        # process_info.cputime contains the used CPU time and possibly,
        # separated by /, also the total elapsed time since the birth of the
        # process.
        if "/" in process_info.cputime:
            elapsed_text = process_info.cputime.split("/")[1]
        else:
            # uptime is a windows only value, introduced in Werk 4029. For
            # future consistency should be moved to the cputime entry and
            # separated by a /
            if process_info.uptime:
                elapsed_text = process_info.uptime
            else:
                elapsed_text = None

        if elapsed_text:
            elapsed = parse_ps_time(elapsed_text)
            self.min_elapsed = minn(self.min_elapsed or elapsed, elapsed)
            self.max_elapsed = maxx(self.max_elapsed, elapsed)

            now = time.time()
            creation_time_unix = int(now - elapsed)
            if creation_time_unix != 0:
                process.append(
                    (
                        "creation time",
                        (render.datetime(creation_time_unix), ""),
                    )
                )

    def cpu_usage(  # type: ignore[no-untyped-def]
        self, value_store, process_info, process: _Process
    ):
        now = time.time()

        pcpu_text = process_info.cputime.split("/")[0]

        if ":" in pcpu_text:  # In linux is a time
            total_seconds = parse_ps_time(pcpu_text)
            pid = process_info.process_id
            cputime = cpu_rate(value_store, "stat.pcpu.%s" % pid, now, total_seconds)

            pcpu = cputime * 100 * self.core_weight(is_win=False)
            process.append(("pid", (pid, "")))

        # windows cpu times
        elif process_info.usermode_time and process_info.kernelmode_time:
            pid = process_info.process_id

            user_per_sec = cpu_rate(
                value_store, "user.%s" % pid, now, int(process_info.usermode_time)
            )
            kernel_per_sec = cpu_rate(
                value_store, "kernel.%s" % pid, now, int(process_info.kernelmode_time)
            )

            if not all([user_per_sec, kernel_per_sec]):
                user_per_sec = 0
                kernel_per_sec = 0

            core_weight = self.core_weight(is_win=True)
            user_perc = user_per_sec / 100000.0 * core_weight
            kernel_perc = kernel_per_sec / 100000.0 * core_weight
            pcpu = user_perc + kernel_perc
            process.append(("cpu usage (user space)", (user_perc, "%")))
            process.append(("cpu usage (kernel space)", (kernel_perc, "%")))
            process.append(("pid", (pid, "")))

        else:  # Solaris, BSD, aix cpu times
            if pcpu_text == "-":  # Solaris defunct
                pcpu_text = 0.0
            pcpu = float(pcpu_text) * self.core_weight(is_win=False)

        self.percent_cpu += pcpu
        process.append(("cpu usage", (pcpu, "%")))

        if process_info.pagefile:
            process.append(("pagefile usage", (process_info.pagefile, "")))

        if process_info.handles:
            self.handle_count += int(process_info.handles)
            process.append(("handle count", (int(process_info.handles), "")))


def process_capture(
    # process_lines: (Node, PsInfo, cmd_line)
    process_lines: Iterable[Tuple[Optional[str], PsInfo, Sequence[str]]],
    params: Mapping[str, Any],
    cpu_cores: int,
    value_store: MutableMapping[str, Any],
) -> ProcessAggregator:
    ps_aggregator = ProcessAggregator(cpu_cores, params)

    userspec = params.get("user")
    cgroupspec = params.get("cgroup", (None, False))

    for node_name, process_info, command_line in process_lines:
        if not process_attributes_match(process_info, userspec, cgroupspec):
            continue

        if not process_matches(command_line, params.get("process"), params.get("match_groups")):
            continue

        process: _Process = []

        if node_name is not None:
            ps_aggregator.running_on_nodes.add(node_name)

        if command_line:
            process.append(("name", (command_line[0], "")))

        # extended performance data: virtualsize, residentsize, %cpu
        if (
            process_info.user is not None
            and process_info.virtual is not None
            and process_info.physical is not None
        ):
            process.append(("user", (process_info.user, "")))
            process.append(("virtual size", (process_info.virtual, "kB")))
            process.append(("resident size", (process_info.physical, "kB")))

            ps_aggregator.virtual_size += process_info.virtual  # kB
            ps_aggregator.resident_size += process_info.physical  # kB

            ps_aggregator.lifetimes(process_info, process)
            ps_aggregator.cpu_usage(value_store, process_info, process)

        include_args = params.get("process_info_arguments", 0)
        if include_args:
            process.append(("args", (" ".join(command_line[1:])[:include_args], "")))

        ps_aggregator.append(process)

    return ps_aggregator


def discover_ps(
    params: Sequence[Mapping[str, Any]],
    section_ps: Optional[Section],
    section_mem: Optional[memory.SectionMem],
    section_mem_used: Optional[Dict[str, memory.SectionMem]],
    section_cpu: Optional[cpu.Section],
) -> DiscoveryResult:
    if not section_ps:
        return

    inventory_specs = get_discovery_specs(params)

    for process_info, command_line in section_ps[1]:
        for servicedesc, pattern, userspec, cgroupspec, _labels, default_params in inventory_specs:
            if not process_attributes_match(process_info, userspec, cgroupspec):
                continue
            matches = process_matches(command_line, pattern)
            if not matches:
                continue  # skip not matched lines

            # User capturing on rule
            if userspec is False:
                i_userspec: Union[None, str] = process_info.user
            else:
                i_userspec = userspec

            i_servicedesc = servicedesc.replace("%u", i_userspec or "")

            # Process capture
            match_groups = () if isinstance(matches, bool) else matches.groups()

            i_servicedesc = replace_service_description(i_servicedesc, match_groups, pattern)

            # Problem here: We need to instantiate all subexpressions
            # with their actual values of the found process.
            inv_params = {
                "process": pattern,
                "match_groups": match_groups,
                "user": i_userspec,
                "cgroup": cgroupspec,
                **default_params,
            }

            yield Service(
                item=i_servicedesc,
                parameters=inv_params,
            )


@contextlib.contextmanager
def unused_value_remover(
    value_store: MutableMapping[str, Any],
    key: str,
) -> Generator[Dict[str, Tuple[float, float]], None, None]:
    """Remove all values that remain unchanged

    This plugin uses the process IDs in the keys to persist values.
    This would lead to a lot of orphaned values if we used the value store directly.
    Thus we use a single dictionary and only store the values that have been used.
    """
    values = value_store.setdefault(key, {})
    old_values = values.copy()
    try:
        yield values
    finally:
        value_store[key] = {k: v for k, v in values.items() if v != old_values.get(k)}


def check_ps_common(
    *,
    label: str,
    item: str,
    params: Mapping[str, Any],
    process_lines: Iterable[Tuple[Optional[str], PsInfo, Sequence[str]]],
    cpu_cores: int,
    total_ram_map: Mapping[str, float],
) -> CheckResult:
    with unused_value_remover(get_value_store(), "collective") as value_store:
        processes = process_capture(process_lines, params, cpu_cores, value_store)

    yield from count_check(processes, params, label)

    yield from memory_check(processes, params)

    yield from memory_perc_check(processes, params, total_ram_map)

    # CPU
    if processes.count:
        yield from cpu_check(processes.percent_cpu, params)

    yield from individual_process_check(processes, params, total_ram_map)

    # only check handle_count if provided by wmic counters
    if processes.handle_count:
        yield from handle_count_check(processes, params)

    if processes.min_elapsed is not None and processes.max_elapsed is not None:
        yield from uptime_check(processes.min_elapsed, processes.max_elapsed, params)

    if processes.count and params.get("process_info") is not None:
        yield Result(
            state=State.OK,
            notice=format_process_list(processes, params["process_info"] == "html"),
        )


def count_check(
    processes: ProcessAggregator,
    params: Mapping[str, Any],
    info_name: str,
) -> CheckResult:
    warnmin, okmin, okmax, warnmax = params["levels"]
    yield from check_levels(
        processes.count,
        metric_name="count",
        levels_lower=(okmin, warnmin),
        levels_upper=(okmax + 1, warnmax + 1),
        render_func=lambda d: str(int(d)),
        boundaries=(0, None),
        label=info_name,
    )
    if processes.running_on_nodes:
        yield Result(
            state=State.OK,
            summary="Running on nodes %s" % ", ".join(sorted(processes.running_on_nodes)),
        )


def memory_check(
    processes: ProcessAggregator,
    params: Mapping[str, Any],
) -> CheckResult:
    """Check levels for virtual and resident used memory"""
    for size, metric_name, memory_type, metric_id in (
        (processes.virtual_size, "Virtual memory", "virtual", "vsz"),
        (processes.resident_size, "Resident memory", "resident", "rss"),
    ):
        if size == 0:
            continue
        levels = params.get(f"{memory_type}_levels")

        yield from check_averageable_metric(
            metric_id=metric_id,
            avg_metric_id=f"{metric_id}avg",
            metric_name=metric_name,
            metric_value=size * 1024,
            levels=levels,
            average_mins=params.get(f"{memory_type}_average"),
            render_fn=render.bytes,
            produce_avg_metric=True,
        )
        yield Metric(metric_id, size, levels=levels)


def get_total_resident_mem_size(
    processes: ProcessAggregator, total_ram_map: Mapping[str, float]
) -> float:
    """Return the total RAM size or raise KeyError if the size can't be calculated"""
    nodes = processes.running_on_nodes or ("",)
    return sum(total_ram_map[node] for node in nodes)


def memory_perc_check(
    processes: ProcessAggregator,
    params: Mapping[str, Any],
    total_ram_map: Mapping[str, float],
) -> CheckResult:
    """Check levels that are in percent of the total RAM of the host"""
    if not processes.resident_size or (
        "resident_levels_perc" not in params and "resident_perc_average" not in params
    ):
        return

    try:
        total_ram = get_total_resident_mem_size(processes, total_ram_map)
    except KeyError:
        yield Result(
            state=State.UNKNOWN,
            summary="Percentual RAM levels configured, but total RAM is unknown",
        )
        return

    resident_perc = 100.0 * processes.resident_size * 1024.0 / total_ram

    yield from check_averageable_metric(
        metric_id="res_perc",
        avg_metric_id="res_percavg",
        metric_name="Percentage of resident memory",
        metric_value=resident_perc,
        levels=params.get("resident_levels_perc"),
        average_mins=params.get("resident_perc_average"),
        render_fn=render.percent,
        produce_avg_metric=False,
    )


def check_averageable_metric(
    metric_id: str,
    avg_metric_id: str,
    metric_name: str,
    metric_value: Union[float, int],
    levels: tuple[float, float] | None,
    average_mins: int | None,
    render_fn: Callable,
    produce_avg_metric: bool,
) -> CheckResult:
    if average_mins:
        avg_metric_value = get_average(
            get_value_store(), metric_id, time.time(), metric_value, average_mins
        )
        infotext = "%s: %s, %d min average" % (
            metric_name,
            render_fn(metric_value),
            average_mins,
        )
        if produce_avg_metric:
            yield Metric(avg_metric_id, avg_metric_value, levels=levels)
        metric_value = avg_metric_value  # use this for level comparison
    else:
        infotext = metric_name

    yield from check_levels(
        metric_value,
        levels_upper=levels,
        render_func=render_fn,
        label=infotext,
    )


def cpu_check(percent_cpu: float, params: Mapping[str, Any]) -> CheckResult:
    """Check levels for cpu utilization from given process"""

    cpu_levels = params.get("cpulevels", (None, None, None))[:2]
    yield Metric("pcpu", percent_cpu, levels=cpu_levels)

    yield from check_averageable_metric(
        metric_id="cpu",
        avg_metric_id="pcpuavg",
        metric_name="CPU",
        metric_value=percent_cpu,
        levels=cpu_levels,
        average_mins=params.get("cpu_average"),
        render_fn=render.percent,
        produce_avg_metric=True,
    )


def extract_process_data(process: _Process) -> tuple[str | None, str | None, float, float, float]:
    name, pid, cpu_usage, virt_usage, res_usage = None, None, 0.0, 0.0, 0.0
    for the_item, (value, _unit) in process:
        if the_item == "name":
            name = str(value)
        elif the_item == "pid":
            pid = str(value)
        elif the_item == "cpu usage":
            cpu_usage += float(value)  # float conversion fo mypy
        elif the_item == "virtual size":
            virt_usage = float(value) * 1024  # memory is reported in kB
        elif the_item == "resident size":
            res_usage = float(value) * 1024  # memory is reported in kB
    return name, pid, cpu_usage, virt_usage, res_usage


def individual_process_check(
    processes: ProcessAggregator,
    params: Mapping[str, Any],
    total_ram_map: Mapping[str, float],
) -> CheckResult:
    cpu_levels = params.get("single_cpulevels")
    virt_levels = params.get("single_virtual_levels")
    res_levels = params.get("single_resident_levels")
    res_levels_perc = params.get("single_resident_levels_perc")
    if (
        cpu_levels is None
        and virt_levels is None
        and res_levels is None
        and res_levels_perc is None
    ):
        return  # Let's skip calculations if we don't have anything to check

    total_res_memory = None
    if res_levels_perc is not None:
        try:
            total_res_memory = get_total_resident_mem_size(processes, total_ram_map)
        except KeyError:
            yield Result(
                state=State.UNKNOWN,
                summary="Percentual RAM levels configured, but total RAM is unknown",
            )

    for p in processes.processes:
        name, pid, cpu_usage, virt_usage, res_usage = extract_process_data(p)
        res_usage_pct = res_usage * 100 / total_res_memory if total_res_memory is not None else None
        label_prefix = str(name) + (" with PID %s" % pid if pid else "")

        for levels, metric_name, metric_value, render_fn in (
            (cpu_levels, "CPU", cpu_usage, render.percent),
            (virt_levels, "virtual memory", virt_usage, render.bytes),
            (res_levels, "resident memory", res_usage, render.bytes),
            (res_levels_perc, "percentage of resident memory", res_usage_pct, render.percent),
        ):
            if levels is None or metric_value is None:
                continue

            check_result, *_ = check_levels(
                metric_value,
                levels_upper=levels,
                render_func=render_fn,
                label=label_prefix + " " + metric_name,
            )
            # To avoid listing of all processes regardless of level setting we
            # only generate output in case WARN level has been reached.
            # In case a full list of processes is desired, one should enable
            # `process_info`, i.E."Enable per-process details in long-output"
            if check_result.state is not State.OK:
                yield Result(state=check_result.state, notice=check_result.summary)


def uptime_check(
    min_elapsed: float,
    max_elapsed: float,
    params: Mapping[str, Any],
) -> CheckResult:
    """Check how long the process is running"""
    if min_elapsed == max_elapsed:
        yield from check_levels(
            min_elapsed,
            levels_lower=params.get("min_age"),
            levels_upper=params.get("max_age"),
            render_func=render.timespan,
            label="Running for",
        )
    else:
        yield from check_levels(
            min_elapsed,
            metric_name="age_youngest",
            levels_lower=params.get("min_age"),
            render_func=render.timespan,
            label="Youngest running for",
        )
        yield from check_levels(
            max_elapsed,
            metric_name="age_oldest",
            levels_upper=params.get("max_age"),
            render_func=render.timespan,
            label="Oldest running for",
        )


def handle_count_check(
    processes: ProcessAggregator,
    params: Mapping[str, Any],
) -> CheckResult:
    yield from check_levels(
        processes.handle_count,
        metric_name="process_handles",
        levels_upper=params.get("handle_count"),
        render_func=lambda d: str(int(d)),
        label="Process handles",
    )
