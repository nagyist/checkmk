#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import contextlib
import os
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from logging import Logger
from pathlib import Path
from typing import Any, assert_never, Literal

from cmk.utils.log import VERBOSE
from cmk.utils.render import date_and_time

from .config import Config
from .event import Event
from .query import OperatorName, QueryGET
from .settings import Settings

# TODO: As one can see clearly below, we should really have a class hierarchy here...

HistoryWhat = Literal[
    "ORPHANED",
    "NOCOUNT",
    "DELAYOVER",
    "EXPIRED",
    "COUNTREACHED",
    "COUNTFAILED",
    "UPDATE",
    "NEW",
    "DELETE",
    "EMAIL",
    "SCRIPT",
    "CANCELLED",
    "ARCHIVED",
    "AUTODELETE",
    "CHANGESTATE",
]

Columns = Sequence[tuple[str, float | int | str | list]]


class History:
    def __init__(
        self,
        settings: Settings,
        config: Config,
        logger: Logger,
        event_columns: Columns,
        history_columns: Columns,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._config = config
        self._logger = logger
        self._event_columns = event_columns
        self._history_columns = history_columns
        self._lock = threading.Lock()
        self._mongodb = MongoDB()
        self._active_history_period = ActiveHistoryPeriod()
        self.reload_configuration(config)

    def reload_configuration(self, config: Config) -> None:
        self._config = config
        if self._config["archive_mode"] == "mongodb":
            _reload_configuration_mongodb(self)
        else:
            _reload_configuration_files(self)

    def flush(self) -> None:
        if self._config["archive_mode"] == "mongodb":
            _flush_mongodb(self)
        else:
            _flush_files(self)

    def add(self, event: Event, what: HistoryWhat, who: str = "", addinfo: str = "") -> None:
        if self._config["archive_mode"] == "mongodb":
            _add_mongodb(self, event, what, who, addinfo)
        else:
            _add_files(self, event, what, who, addinfo)

    def get(self, query: QueryGET) -> Iterable[Any]:
        if self._config["archive_mode"] == "mongodb":
            return _get_mongodb(self, query)
        return _get_files(self, self._logger, query)

    def housekeeping(self) -> None:
        if self._config["archive_mode"] == "mongodb":
            _housekeeping_mongodb(self)
        else:
            _housekeeping_files(self)


# .
#   .--MongoDB-------------------------------------------------------------.
#   |             __  __                         ____  ____                |
#   |            |  \/  | ___  _ __   __ _  ___ |  _ \| __ )               |
#   |            | |\/| |/ _ \| '_ \ / _` |/ _ \| | | |  _ \               |
#   |            | |  | | (_) | | | | (_| | (_) | |_| | |_) |              |
#   |            |_|  |_|\___/|_| |_|\__, |\___/|____/|____/               |
#   |                                |___/                                 |
#   +----------------------------------------------------------------------+
#   | The Event Log Archive can be stored in a MongoDB instead of files,   |
#   | this section contains MongoDB related code.                          |
#   '----------------------------------------------------------------------'

try:
    import datetime

    from pymongo import DESCENDING
    from pymongo.connection import Connection  # type: ignore[import]
    from pymongo.errors import OperationFailure
except ImportError:
    Connection = None


class MongoDB:
    def __init__(self) -> None:
        super().__init__()
        self.connection: Connection = None
        self.db: Any = None


def _reload_configuration_mongodb(history: History) -> None:
    # Configure the auto deleting indexes in the DB
    _update_mongodb_indexes(history._settings, history._mongodb)
    _update_mongodb_history_lifetime(history._settings, history._config, history._mongodb)


def _housekeeping_mongodb(history: History) -> None:
    pass


def _connect_mongodb(settings: Settings, mongodb: MongoDB) -> None:
    if Connection is None:
        raise Exception("Could not initialize MongoDB (Python-Modules are missing)")
    mongodb.connection = Connection(*_mongodb_local_connection_opts(settings))
    mongodb.db = mongodb.connection[os.environ["OMD_SITE"]]


def _mongodb_local_connection_opts(settings: Settings) -> tuple[str | None, int | None]:
    ip, port = None, None
    with settings.paths.mongodb_config_file.value.open(encoding="utf-8") as f:
        for entry in f:
            if entry.startswith("bind_ip"):
                ip = entry.split("=")[1].strip()
            elif entry.startswith("port"):
                port = int(entry.split("=")[1].strip())
    return ip, port


def _flush_mongodb(history: History) -> None:
    history._mongodb.db.ec_archive.drop()


def _get_mongodb_max_history_age(mongodb: MongoDB) -> int:
    result = mongodb.db.ec_archive.index_information()
    if "dt_-1" not in result or "expireAfterSeconds" not in result["dt_-1"]:
        return -1
    return result["dt_-1"]["expireAfterSeconds"]


def _update_mongodb_indexes(settings: Settings, mongodb: MongoDB) -> None:
    if not mongodb.connection:
        _connect_mongodb(settings, mongodb)
    result = mongodb.db.ec_archive.index_information()

    if "time_-1" not in result:
        mongodb.db.ec_archive.ensure_index([("time", DESCENDING)])


def _update_mongodb_history_lifetime(settings: Settings, config: Config, mongodb: MongoDB) -> None:
    if not mongodb.connection:
        _connect_mongodb(settings, mongodb)

    if _get_mongodb_max_history_age(mongodb) == config["history_lifetime"] * 86400:
        return  # do not update already correct index

    with contextlib.suppress(OperationFailure):  # Ignore not existing index
        mongodb.db.ec_archive.drop_index("dt_-1")

    # Delete messages after x days
    mongodb.db.ec_archive.ensure_index(
        [("dt", DESCENDING)], expireAfterSeconds=config["history_lifetime"] * 86400, unique=False
    )


def _mongodb_next_id(mongodb: MongoDB, name: str, first_id: int = 0) -> int:
    ret = mongodb.db.counters.find_and_modify(
        query={"_id": name}, update={"$inc": {"seq": 1}}, new=True
    )

    if not ret:
        # Initialize the index!
        mongodb.db.counters.insert({"_id": name, "seq": first_id})
        return first_id
    return ret["seq"]


def _add_mongodb(history: History, event: Event, what: HistoryWhat, who: str, addinfo: str) -> None:
    _log_event(history._config, history._logger, event, what, who, addinfo)
    if not history._mongodb.connection:
        _connect_mongodb(history._settings, history._mongodb)
    # We converted _id to be an auto incrementing integer. This makes the unique
    # index compatible to history_line of the file (which is handled as integer)
    # within mkeventd. It might be better to use the ObjectId() of MongoDB, but
    # for the first step, we use the integer index for simplicity
    now = time.time()
    history._mongodb.db.ec_archive.insert(
        {
            "_id": _mongodb_next_id(history._mongodb, "ec_archive_id"),
            "dt": datetime.datetime.fromtimestamp(now),
            "time": now,
            "event": event,
            "what": what,
            "who": who,
            "addinfo": addinfo,
        }
    )


def _log_event(
    config: Config, logger: Logger, event: Event, what: HistoryWhat, who: str, addinfo: str
) -> None:
    if config["debug_rules"]:
        logger.info("Event %d: %s/%s/%s - %s", event["id"], what, who, addinfo, event["text"])


def _get_mongodb(  # pylint: disable=too-many-branches
    history: History,
    query: QueryGET,
) -> Iterable[Any]:
    filters, limit = query.filters, query.limit

    history_entries = []

    if not history._mongodb.connection:
        _connect_mongodb(history._settings, history._mongodb)

    # Construct the mongodb filtering specification. We could fetch all information
    # and do filtering on this data, but this would be way too inefficient.
    mongo_query = {}
    for column_name, operator_name, _predicate, argument in filters:
        if operator_name == "=":
            mongo_filter: str | dict[str, str] = argument
        elif operator_name == ">":
            mongo_filter = {"$gt": argument}
        elif operator_name == "<":
            mongo_filter = {"$lt": argument}
        elif operator_name == ">=":
            mongo_filter = {"$gte": argument}
        elif operator_name == "<=":
            mongo_filter = {"$lte": argument}
        elif operator_name == "~":  # case sensitive regex, find pattern in string
            mongo_filter = {"$regex": argument, "$options": ""}
        elif operator_name == "=~":  # case insensitive, match whole string
            mongo_filter = {"$regex": argument, "$options": "mi"}
        elif operator_name == "~~":  # case insensitive regex, find pattern in string
            mongo_filter = {"$regex": argument, "$options": "i"}
        elif operator_name == "in":
            mongo_filter = {"$in": argument}
        else:
            assert_never(operator_name)

        if column_name[:6] == "event_":
            mongo_query["event." + column_name[6:]] = mongo_filter
        elif column_name[:8] == "history_":
            key = column_name[8:]
            if key == "line":
                key = "_id"
            mongo_query[key] = mongo_filter
        else:
            raise Exception(f"Filter {column_name} not implemented for MongoDB")

    result = history._mongodb.db.ec_archive.find(mongo_query).sort("time", -1)

    # Might be used for debugging / profiling
    # open(cmk.utils.paths.omd_root + '/var/log/check_mk/ec_history_debug.log', 'a').write(
    #    pprint.pformat(filters) + '\n' + pprint.pformat(result.explain()) + '\n')

    if limit:
        result = result.limit(limit + 1)

    # now convert the MongoDB data structure to the eventd internal one
    for entry in result:
        item = [
            entry["_id"],
            entry["time"],
            entry["what"],
            entry["who"],
            entry["addinfo"],
        ]
        for colname, defval in history._event_columns:
            key = colname[6:]  # drop "event_"
            item.append(entry["event"].get(key, defval))
        history_entries.append(item)

    return history_entries


# .
#   .--History-------------------------------------------------------------.
#   |                   _   _ _     _                                      |
#   |                  | | | (_)___| |_ ___  _ __ _   _                    |
#   |                  | |_| | / __| __/ _ \| '__| | | |                   |
#   |                  |  _  | \__ \ || (_) | |  | |_| |                   |
#   |                  |_| |_|_|___/\__\___/|_|   \__, |                   |
#   |                                             |___/                    |
#   +----------------------------------------------------------------------+
#   | Functions for logging the history of events                          |
#   '----------------------------------------------------------------------'


def _reload_configuration_files(history: History) -> None:
    pass


def _flush_files(history: History) -> None:
    _expire_logfiles(history._settings, history._config, history._logger, history._lock, True)


def _housekeeping_files(history: History) -> None:
    _expire_logfiles(history._settings, history._config, history._logger, history._lock, False)


def _add_files(history: History, event: Event, what: HistoryWhat, who: str, addinfo: str) -> None:
    """Make a new entry in the event history.

    Each entry is tab-separated line with the following columns:
    0: time of log entry
    1: type of entry (keyword)
    2: user who initiated the action (for GUI actions)
    3: additional information about the action
    4-oo: StatusTableEvents.columns
    """
    _log_event(history._config, history._logger, event, what, who, addinfo)
    with history._lock:
        columns = [
            quote_tab(str(time.time())),
            quote_tab(scrub_string(what)),
            quote_tab(scrub_string(who)),
            quote_tab(scrub_string(addinfo)),
        ]
        columns += [
            quote_tab(event.get(colname[6:], defval))  # drop "event_"
            for colname, defval in history._event_columns
        ]

        with get_logfile(
            history._config,
            history._settings.paths.history_dir.value,
            history._active_history_period,
        ).open(mode="ab") as f:
            f.write(b"\t".join(columns) + b"\n")


def quote_tab(col: Any) -> bytes:
    ty = type(col)
    if ty in [float, int]:
        return str(col).encode("utf-8")
    if ty is bool:
        return b"1" if col else b"0"
    if ty in [tuple, list]:
        col = b"\1" + b"\1".join([quote_tab(e) for e in col])
    elif col is None:
        col = b"\2"
    elif ty is str:
        col = col.encode("utf-8")

    return col.replace(b"\t", b" ")


class ActiveHistoryPeriod:
    def __init__(self) -> None:
        super().__init__()
        self.value: int | None = None


def get_logfile(config: Config, log_dir: Path, active_history_period: ActiveHistoryPeriod) -> Path:
    """Get file object to current log file, handle also history and lifetime limit."""
    log_dir.mkdir(parents=True, exist_ok=True)
    # Log into file starting at current history period,
    # but: if a newer logfile exists, use that one. This
    # can happen if you switch the period from daily to
    # weekly.
    timestamp = _current_history_period(config)

    # Log period has changed or we have not computed a filename yet ->
    # compute currently active period
    if active_history_period.value is None or timestamp > active_history_period.value:
        # Look if newer files exist
        timestamps = sorted(int(str(path.name)[:-4]) for path in log_dir.glob("*.log"))
        if len(timestamps) > 0:
            timestamp = max(timestamps[-1], timestamp)

        active_history_period.value = timestamp

    return log_dir / f"{timestamp}.log"


def _current_history_period(config: Config) -> int:
    """Return timestamp of the beginning of the current history period."""
    lt = time.localtime()
    ts = time.mktime(
        time.struct_time(
            (
                lt.tm_year,
                lt.tm_mon,
                lt.tm_mday,
                0,  # tm_hour
                0,  # tm_min
                0,  # tm_sec
                lt.tm_wday,
                lt.tm_yday,
                lt.tm_isdst,
                lt.tm_zone,
                lt.tm_gmtoff,
            )
        )
    )
    offset = lt.tm_wday * 86400 if config["history_rotation"] == "weekly" else 0
    return int(ts) - offset


def _expire_logfiles(
    settings: Settings, config: Config, logger: Logger, lock_history: threading.Lock, flush: bool
) -> None:
    """Delete old log files."""
    with lock_history:
        try:
            days = config["history_lifetime"]
            min_mtime = time.time() - days * 86400
            logger.log(
                VERBOSE,
                "Expiring logfiles (Horizon: %d days -> %s)",
                days,
                date_and_time(min_mtime),
            )
            for path in settings.paths.history_dir.value.glob("*.log"):
                if flush or path.stat().st_mtime < min_mtime:
                    logger.info(
                        "Deleting log file %s (age %s)", path, date_and_time(path.stat().st_mtime)
                    )
                    path.unlink()
        except Exception as e:
            if settings.options.debug:
                raise
            logger.warning(f"Error expiring log files: {e}")


# Please note: Keep this in sync with livestatus/src/TableEventConsole.cc.
_GREPABLE_COLUMNS = {
    "event_id",
    "event_text",
    "event_comment",
    "event_host",
    "event_contact",
    "event_application",
    "event_rule_id",
    "event_owner",
    "event_ipaddress",
    "event_core_host",
}


def _grep_pipeline(
    filters: list[tuple[str, OperatorName, Callable[[Any], bool], Any]]
) -> list[str]:
    """
    Optimization: use grep in order to reduce amount of read lines based on some frequently used
    filters. It's OK if the filters don't match 100% accurately on the right lines. If in doubt, you
    can output more lines than necessary. This is only a kind of prefiltering.
    """
    return [
        command
        for column_name, operator_name, _predicate, argument in filters
        if column_name in _GREPABLE_COLUMNS
        for command in [_grep_command(operator_name, argument)]
        if command is not None
    ]


def _grep_command(operator_name: OperatorName, argument: Any) -> str | None:
    if operator_name == "=":
        return f"grep -F {_grep_pattern(argument)}"
    if operator_name == "=~":
        return f"grep -F -i {_grep_pattern(argument)}"
    if operator_name == "~":
        return f"grep -E {_grep_pattern(argument)}"
    if operator_name == "~~":
        return f"grep -E -i {_grep_pattern(argument)}"
    return None


def _grep_pattern(argument: Any) -> str:
    return f"-e {shlex.quote(str(argument))}"


def _get_files(history: History, logger: Logger, query: QueryGET) -> Iterable[Any]:
    if not history._settings.paths.history_dir.value.exists():
        return []

    filters = query.filters
    logger.debug("Filters: %r", filters)
    limit = query.limit
    logger.debug("Limit: %r", limit)

    grep_pipeline = _grep_pipeline(filters)

    time_filters = [
        (operator_name, argument)
        for column_name, operator_name, _predicate, argument in filters
        if column_name.split("_")[-1] == "time"
    ]
    time_range = (
        _greatest_lower_bound_for_filters(time_filters),
        _least_upper_bound_for_filters(time_filters),
    )
    logger.debug("time range: %r", time_range)

    # We do not want to open all files. So our strategy is:
    # look for "time" filters and first apply the filter to
    # the first entry and modification time of the file. Only
    # if at least one of both timestamps is accepted then we
    # take that file into account.
    # Use the later logfiles first, to get the newer log entries
    # first. When a limit is reached, the newer entries should
    # be processed in most cases. We assume that now.
    # To keep a consistent order of log entries, we should care
    # about sorting the log lines in reverse, but that seems to
    # already be done by the GUI, so we don't do that twice. Skipping
    # this # will lead into some lines of a single file to be limited in
    # wrong order. But this should be better than before.
    history_entries: list[Any] = []
    for path in sorted(history._settings.paths.history_dir.value.glob("*.log"), reverse=True):
        if limit is not None and limit <= 0:
            logger.debug("query limit reached")
            break
        if not _intersects(time_range, _get_logfile_timespan(path)):
            logger.debug("skipping history file %s because of time filters", path)
            continue
        tac = f"nl -b a {shlex.quote(str(path))} | tac"  # Process younger lines first
        cmd = " | ".join([tac] + grep_pipeline)
        logger.debug("preprocessing history file with command [%s]", cmd)
        new_entries = parse_history_file(
            history._history_columns, path, query.filter_row, cmd, limit, logger
        )
        history_entries += new_entries
        if limit is not None:
            limit -= len(new_entries)
    return history_entries


def _greatest_lower_bound_for_filters(
    filters: Iterable[tuple[OperatorName, float]]
) -> float | None:
    result: float | None = None
    for operator, value in filters:
        glb = _greatest_lower_bound_for_filter(operator, value)
        if glb is not None:
            result = glb if result is None else max(result, glb)
    return result


def _greatest_lower_bound_for_filter(operator: OperatorName, value: float) -> float | None:
    if operator == "=":
        return value
    if operator == ">=":
        return value
    if operator == ">":
        return value + 1
    return None


def _least_upper_bound_for_filters(filters: Iterable[tuple[OperatorName, float]]) -> float | None:
    result: float | None = None
    for operator, value in filters:
        lub = _least_upper_bound_for_filter(operator, value)
        if lub is not None:
            result = lub if result is None else min(result, lub)
    return result


def _least_upper_bound_for_filter(operator: OperatorName, value: float) -> float | None:
    if operator == "=":
        return value
    if operator == "<=":
        return value
    if operator == "<":
        return value - 1
    return None


def _intersects(
    interval1: tuple[float | None, float | None],
    interval2: tuple[float | None, float | None],
) -> bool:
    lo1, hi1 = interval1
    lo2, hi2 = interval2
    return (lo2 is None or hi1 is None or lo2 <= hi1) and (lo1 is None or hi2 is None or lo1 <= hi2)


def parse_history_file(
    history_columns: Sequence[tuple[str, Any]],
    path: Path,
    filter_row: Callable[[Sequence[Any]], bool],
    cmd: str,
    limit: int | None,
    logger: Logger,
) -> list[Any]:
    entries: list[Any] = []
    with subprocess.Popen(
        cmd,
        shell=True,  # nosec
        close_fds=True,
        stdout=subprocess.PIPE,
    ) as grep:
        if grep.stdout is None:
            raise Exception("Huh? stdout vanished...")

        for line in grep.stdout:
            if limit is not None and len(entries) > limit:
                break
            try:
                parts: list[Any] = line.decode("utf-8").rstrip("\n").split("\t")
                convert_history_line(history_columns, parts)
                if filter_row(parts):
                    entries.append(parts)
            except Exception:
                logger.exception(f"Invalid line '{line!r}' in history file {path}")

    return entries


def convert_history_line(history_columns: Sequence[tuple[str, Any]], values: list[Any]) -> None:
    """
    Speed-critical function for converting string representation
    of log line back to Python values.
    """
    values[0] = int(values[0])  # history_line
    values[1] = float(values[1])  # history_time
    values[5] = int(values[5])  # event_id
    values[6] = int(values[6])  # event_count
    values[8] = float(values[8])  # event_first
    values[9] = float(values[9])  # event_last
    values[11] = int(values[11])  # event_sl
    values[15] = int(values[15])  # event_pid
    values[16] = int(values[16])  # event_priority
    values[17] = int(values[17])  # event_facility
    values[19] = int(values[19])  # event_state
    values[22] = _unsplit(values[22])  # event_match_groups
    num_values = len(values)
    if num_values <= 23:  # event_contact_groups
        values.append(None)
    else:
        values[23] = _unsplit(values[23])
    if num_values <= 24:  # event_ipaddress
        values.append(history_columns[25][1])
    if num_values <= 25:  # event_orig_host
        values.append(history_columns[26][1])
    if num_values <= 26:  # event_contact_groups_precedence
        values.append(history_columns[27][1])
    if num_values <= 27:  # event_core_host
        values.append(history_columns[28][1])
    if num_values <= 28:  # event_host_in_downtime
        values.append(history_columns[29][1])
    else:
        values[28] = values[28] == "1"
    if num_values <= 29:  # event_match_groups_syslog_application
        values.append(history_columns[30][1])
    else:
        values[29] = _unsplit(values[29])


def _unsplit(s: Any) -> Any:
    if not isinstance(s, str):
        return s
    if s.startswith("\2"):
        return None  # \2 is the designator for None
    if s.startswith("\1"):
        if len(s) == 1:
            return ()
        return tuple(s[1:].split("\1"))
    return s


def _get_logfile_timespan(path: Path) -> tuple[float | None, float | None]:
    try:
        with path.open(encoding="utf-8") as f:
            first_entry: float | None = float(f.readline().split("\t", 1)[0])
    except Exception:
        first_entry = None
    try:
        last_entry: float | None = path.stat().st_mtime
    except Exception:
        last_entry = None
    return first_entry, last_entry


def scrub_string(s: str) -> str:
    """Rip out/replace any characters which have a special meaning in the UTF-8
    encoded history files, see e.g. quote_tab. In theory this shouldn't be
    necessary, because there are a bunch of bytes which are not contained in any
    valid UTF-8 string, but following Murphy's Law, those are not used in
    Checkmk. To keep backwards compatibility with old history files, we have no
    choice and continue to do it wrong... :-/"""

    return s.translate(_scrub_string_unicode_table)


_scrub_string_unicode_table = {0: None, 1: None, 2: None, ord("\n"): None, ord("\t"): ord(" ")}
