#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Check_MK's library for code used by different components of Check_MK.

This library is currently handled as internal module of Check_MK and
does not offer stable APIs. The code may change at any time."""

from __future__ import annotations

__version__ = "2.3.0b1"

import enum
import functools
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, NamedTuple, Self

import cmk.utils.paths
from cmk.utils.i18n import _


class _EditionValue(NamedTuple):
    short: str
    long: str
    title: str


class Edition(_EditionValue, enum.Enum):
    CRE = _EditionValue("cre", "raw", "Checkmk Raw Edition")
    CEE = _EditionValue("cee", "enterprise", "Checkmk Enterprise Edition")
    CCE = _EditionValue("cce", "cloud", "Checkmk Cloud Edition")
    CME = _EditionValue("cme", "managed", "Checkmk Managed Services Edition")


@lru_cache
def omd_version() -> str:
    version_link = cmk.utils.paths.omd_root / "version"
    return version_link.resolve().name


@lru_cache
def edition() -> Edition:
    try:
        return Edition[omd_version().split(".")[-1].upper()]
    except KeyError:
        # Without this fallback CI jobs may fail.
        # The last job known to fail was we the building of the sphinx documentation
        return Edition.CRE


def is_enterprise_edition() -> bool:
    return edition() is Edition.CEE


def is_cloud_edition() -> bool:
    return edition() is Edition.CCE


def is_raw_edition() -> bool:
    return edition() is Edition.CRE


def is_managed_edition() -> bool:
    return edition() is Edition.CME


def is_cma() -> bool:
    return os.path.exists("/etc/cma/cma.conf")


def mark_edition_only(feature_to_mark: str, exclusive_to: Edition) -> str:
    """
    >>> mark_edition_only("Feature", Edition.CRE)
    'Feature (Raw Edition)'
    >>> mark_edition_only("Feature", Edition.CEE)
    'Feature (Enterprise Edition)'
    >>> mark_edition_only("Feature", Edition.CCE)
    'Feature (Cloud Edition)'
    >>> mark_edition_only("Feature", Edition.CME)
    'Feature (Managed Services Edition)'
    """
    return f"{feature_to_mark} ({exclusive_to.title.removeprefix('Checkmk ')})"


# Version string: <major>.<minor>.<sub><vtype><patch>-<year>.<month>.<day>
#                 [------ base -------][-- build ---]-[------ date ------]
# A version string may consist of the three parts "base", "build" and "date".
# A version string must contain at least one of the parts "base" and "date", while the following
# combinations are possible:
#
# _StableVersion:           <base>              e.g. 1.2.3
# _BetaVersion,             <base><build>       e.g. 1.2.3b4
# _InnovationVersion.       <base><build>       e.g. 1.2.3i4
# _PatchVersion:            <base><build>       e.g. 1.2.3p4
# _MasterDailyVersion:      <date>              e.g. 2021.12.24
# _StableDailyVersion:      <base>-<date>       e.g. 1.2.3-2021.12.24


class RType(enum.IntEnum):
    i = 0
    b = 1
    na = 2
    p = 3
    daily = 4

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"


@dataclass(order=True)
class _BuildDate:
    year: int
    month: int
    day: int

    def __str__(self) -> str:
        return f"{self.year:04}.{self.month:02}.{self.day:02}"


@dataclass(order=True)
class _Release:
    r_type: RType
    value: int | _BuildDate

    def suffix(self) -> str:
        if self.r_type is RType.na:
            return ""
        if self.r_type is RType.daily:
            return f"-{self.value}"
        return f"{self.r_type.name}{self.value}"

    @classmethod
    def unspecified(cls) -> Self:
        return cls(RType.na, 0)


@dataclass(order=True)
class _BaseVersion:
    major: int
    minor: int
    sub: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.sub}"


@functools.total_ordering
class Version:
    # Regular expression patterns
    _PAT_BASE = r"([1-9]?\d)\.([1-9]?\d)\.([1-9]?\d)"  # e.g. "2.1.0"
    _PAT_DATE = r"([1-9]\d{3})\.([0-1]\d)\.([0-3]\d)"  # e.g. "2021.12.24"
    _PAT_BUILD = r"([bip])(\d+)"  # b=beta, i=innov, p=patch; e.g. "b4"
    _RGX_STABLE = re.compile(rf"{_PAT_BASE}(?:{_PAT_BUILD})?")  # e.g. "2.1.0p17"
    # e.g. daily of version branch: "2.1.0-2021.12.24",
    # daily of master branch: "2021.12.24"
    # daily of master sandbox branch: "2022.06.02-sandbox-lm-2.2-thing"
    # daily of version sandbox branch: "2.1.0-2022.06.02-sandbox-lm-2.2-thing"
    _RGX_DAILY = re.compile(rf"(?:{_PAT_BASE}-)?{_PAT_DATE}(?:-sandbox.+)?")

    @classmethod
    def from_str(cls, raw: str) -> Self:
        try:
            return cls._parse_release_version(raw)
        except ValueError:
            return cls._parse_daily_version(raw)

    @classmethod
    def _parse_release_version(cls, vstring: str) -> Self:
        if not (match := cls._RGX_STABLE.fullmatch(vstring)):
            raise ValueError('Invalid version string "%s"' % vstring)

        match match.group(1, 2, 3, 4, 5):
            case major, minor, sub, None, None:
                return cls(_BaseVersion(int(major), int(minor), int(sub)), _Release.unspecified())
            case major, minor, sub, r_type, patch:
                return cls(
                    _BaseVersion(int(major), int(minor), int(sub)),
                    _Release(RType[r_type], int(patch)),
                )

        raise ValueError(f'Cannot parse version string "{vstring}".')

    @classmethod
    def _parse_daily_version(cls, vstring: str) -> Self:
        if not (match := cls._RGX_DAILY.fullmatch(vstring)):
            raise ValueError('Invalid version string "%s"' % vstring)

        (major, minor, sub, year, month, day) = match.group(1, 2, 3, 4, 5, 6)

        return cls(
            None
            if all(x is None for x in (major, minor, sub))
            else _BaseVersion(int(major), int(minor), int(sub)),
            _Release(RType.daily, _BuildDate(int(year), int(month), int(day))),
        )

    def __init__(self, base: _BaseVersion | None, release: _Release) -> None:
        self.base: Final = base
        self.release: Final = release

    @property
    def version_base(self) -> str:
        return "" if self.base is None else str(self.base)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.base!r}, {self.release!r})"

    def __str__(self) -> str:
        return f"{'' if self.base is None else self.base}{self.release.suffix()}".lstrip("-")

    def __lt__(self, other: Version) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self.base == other.base:
            return self.release < other.release
        if self.base is None:
            return False
        if other.base is None:
            return True
        return self.base < other.base

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.base == other.base and self.release == other.release


VERSION_PATTERN = re.compile(r"^([.\-a-z]+)?(\d+)")


# Parses versions of Checkmk and converts them into comparable integers.
def parse_check_mk_version(v: str) -> int:
    """Figure out how to compare versions semantically.

    Parses versions of Checkmk and converts them into comparable integers.

    >>> p = parse_check_mk_version

    Watch out! This function can do more than just parse Checkmk versions :-(
    Changing this is incompatible, as it might render some rules for
    "Checkmk Agent installation auditing" invalid.
    >>> p("1.2")
    1020050000

    No idea why this should be allowed:
    >>> p("1.2.3i12p4b43")
    1020315504

    All dailies are built equal.

    >>> p("1.5.0-2019.10.10")
    1050090000

    >>> p("1.6.0-2019.10.10")
    1060090000

    >>> p("1.5.0-2019.10.24") == p("1.5.0-2018.05.05")
    True

    >>> p('1.2.4p1')
    1020450001

    >>> p('1.2.4')
    1020450000

    >>> p('1.2.4b1')
    1020420100

    >>> p('1.2.3i1p1')
    1020310101

    >>> p('1.2.3i1')
    1020310100

    >>> p('1.2.4p10')
    1020450010

    >>> p("1.5.0") > p("1.5.0p22")
    False

    >>> p("1.5.0-2019.10.10") > p("1.5.0p22")
    True

    >>> p("1.5.0p13") == p("1.5.0p13")
    True

    >>> p("1.5.0p13") > p("1.5.0p12")
    True

    >>> p("2022.06.23-sandbox-az-sles-15sp3")
    2022062390000

    """
    parts = v.split(".", 2)

    while len(parts) < 3:
        parts.append("0")

    var_map = {
        # identifier: (base-val, multiplier)
        "s": (0, 1),  # sub
        "i": (10000, 100),  # innovation
        "b": (20000, 100),  # beta
        "p": (50000, 1),  # patch-level
        "-": (90000, 0),  # daily
        ".": (90000, 0),  # daily
    }

    def _extract_rest(_rest: str) -> tuple[str, int, str]:
        for match in VERSION_PATTERN.finditer(_rest):
            _var_type = match.group(1) or "s"
            _num = match.group(2)
            return _var_type, int(_num), _rest[match.end() :]
        # Default fallback.
        return "p", 0, ""

    major, minor, rest = parts
    _, sub, rest = _extract_rest(rest)

    if rest.startswith("-sandbox"):
        rest = ""

    if len(major) == 4:  # daily!
        var_type, num, rest = "-", 0, ""
    else:
        # Only add the base once, else we could do it in the loop.
        var_type, num, rest = _extract_rest(rest)

    base, multiply = var_map[var_type]
    val = base
    val += num * multiply

    while rest:
        var_type, num, rest = _extract_rest(rest)
        _, multiply = var_map[var_type]
        val += num * multiply

    return int("%02d%02d%02d%05d" % (int(major), int(minor), sub, val))


def base_version_parts(version: str) -> tuple[int, int, int]:
    match = re.match(r"(\d+).(\d+).(\d+)", version)
    if not match or len(match.groups()) != 3:
        raise ValueError(_("Unable to parse version: %r") % version)
    groups = match.groups()
    return int(groups[0]), int(groups[1]), int(groups[2])


def is_daily_build_of_master(version: str) -> bool:
    """
    >>> f = is_daily_build_of_master
    >>> f("2021.04.12")
    True
    >>> f("2023.04.12")
    True
    >>> f("2.1.0")
    False
    >>> f("2.1.0-2022.06.23")
    False

    Is not directly built from master, but a sandbox branch which is based on the master branch.
    Treat it same as a master branch.

    >>> f("2022.06.23-sandbox-lm-2.2-omd-apache")
    True
    """
    return re.match(r"\d{4}.\d{2}.\d{2}(?:-sandbox.+)?$", version) is not None


def is_same_major_version(this_version: str, other_version: str) -> bool:
    """
    Nightly branch builds e.g. 2.0.0-2022.01.01 are treated as 2.0.0.

    >>> c = is_same_major_version
    >>> c("2.0.0-2022.01.01", "2.0.0p3")
    True
    >>> c("2022.01.01", "2.0.0p3")
    True
    >>> c("2022.01.01", "1.6.0p3")
    True
    >>> c("1.6.0", "1.6.0p2")
    True
    >>> c("1.7.0", "1.6.0")
    False
    >>> c("1.6.0", "1.7.0")
    False
    >>> c("2.1.0i1", "2.1.0p2")
    True
    >>> c("2.1.0", "2.1.0")
    True
    """
    # We can not decide which is the current base version of the master daily builds. For this
    # reason we always treat them to be compatbile.
    if is_daily_build_of_master(this_version) or is_daily_build_of_master(other_version):
        return True

    return base_version_parts(this_version)[:-1] == base_version_parts(other_version)[:-1]


class VersionsCompatible:
    ...


class VersionsIncompatible:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def __str__(self) -> str:
        return self._reason


def versions_compatible(
    from_v: Version, to_v: Version, /
) -> VersionsCompatible | VersionsIncompatible:
    """Whether or not two versions are compatible (e.g. for omd update or remote automation calls)

    >>> c = versions_compatible

    Nightly build of master branch is always compatible as we don't know which major version it
    belongs to. It's also not that important to validate this case.

    >>> isinstance(c(Version.from_str("2.0.0i1"), Version.from_str("2021.12.13")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2021.12.13"), Version.from_str("2.0.0i1")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2021.12.13"), Version.from_str("2022.01.01")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2022.01.01"), Version.from_str("2021.12.13")), VersionsCompatible)
    True

    Nightly branch builds e.g. 2.0.0-2022.01.01 are treated as 2.0.0.

    >>> isinstance(c(Version.from_str("2.0.0-2022.01.01"), Version.from_str("2.0.0p3")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2.0.0p3"), Version.from_str("2.0.0-2022.01.01")), VersionsCompatible)
    True

    Same major is allowed

    >>> isinstance(c(Version.from_str("2.0.0i1"), Version.from_str("2.0.0p3")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2.0.0p3"), Version.from_str("2.0.0i1")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2.0.0p3"), Version.from_str("2.0.0p3")), VersionsCompatible)
    True

    Prev major to new is allowed #1

    >>> isinstance(c(Version.from_str("1.6.0i1"), Version.from_str("2.0.0")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("1.6.0p23"), Version.from_str("2.0.0")), VersionsCompatible)
    True
    >>> isinstance(c(Version.from_str("2.0.0p12"), Version.from_str("2.1.0i1")), VersionsCompatible)
    True

    Prepre major to new not allowed

    >>> str(c(Version.from_str("1.6.0p1"), Version.from_str("2.1.0p3")))
    'Target version too new (one major version jump at maximum).'
    >>> str(c(Version.from_str("1.6.0p1"), Version.from_str("2.1.0b1")))
    'Target version too new (one major version jump at maximum).'
    >>> str(c(Version.from_str("1.5.0i1"), Version.from_str("2.0.0")))
    'Target version too new (one major version jump at maximum).'
    >>> str(c(Version.from_str("1.4.0"), Version.from_str("2.0.0")))
    'Target version too new (one major version jump at maximum).'

    New major to old not allowed

    >>> str(c(Version.from_str("2.0.0"), Version.from_str("1.6.0p1")))
    'Target version too old (older major version is not supported).'
    >>> str(c(Version.from_str("2.1.0"), Version.from_str("2.0.0b1")))
    'Target version too old (older major version is not supported).'

    Specific patch release requirements

    >>> isinstance(c(Version.from_str("2.2.0p1"), Version.from_str("2.3.0i1")), VersionsCompatible)
    True
    >>> str(c(Version.from_str("2.2.0b1000"), Version.from_str("2.3.0i1")))
    'This target version requires at least 2.2.0p1'
    """

    # Daily builds of the master branch (format: YYYY.MM.DD) are always treated to be compatbile
    if any(
        (
            is_daily_build_of_master(str(from_v)),
            is_daily_build_of_master(str(to_v)),
        )
    ):
        return VersionsCompatible()

    from_v_parts = base_version_parts(str(from_v))
    to_v_parts = base_version_parts(str(to_v))

    # Same major version is allowed
    if from_v_parts == to_v_parts:
        return VersionsCompatible()

    # Newer major to older is not allowed
    if from_v_parts > to_v_parts:
        return VersionsIncompatible(
            "Target version too old (older major version is not supported)."
        )

    # Now we need to detect the previous and pre-previous major version.
    # How can we do it without explicitly listing all version numbers?
    #
    # What version changes did we have?
    #
    # - Long ago we increased only the 3rd number which is not done anymore
    # - Until 1.6.0 we only increased the 2nd number
    # - With 2.0.0 we once increased the 1st number
    # - With 2.1.0 we will again only increase the 2nd number
    # - Increasing of the 1st number may happen again
    #
    # Seems we need to handle these cases for:
    #
    # - Steps in 1st number with reset of 2nd number can happen
    # - Steps in 2nd number can happen
    # - 3rd number and suffixes can completely be ignored for now
    #
    # We could implement a simple logic like this:
    #
    # - 1st number +1, newer 2nd is 0 -> it is uncertain which was the
    #                                    last release. We need an explicit
    #                                    lookup table for this situation.
    # - 1st number +2                      -> preprev major
    # - Equal 1st number and 2nd number +1 -> prev major
    # - Equal 1st number and 2nd number +2 -> preprev major
    #
    # Seems to be sufficient for now.
    #
    # Obviously, this only works as long as we keep the current version scheme.

    target_too_new = VersionsIncompatible(
        "Target version too new (one major version jump at maximum)."
    )

    if to_v_parts[0] - from_v_parts[0] > 1:
        return target_too_new  # preprev 1st number

    last_major_releases = {
        1: (1, 6, 0),
    }

    if to_v_parts[0] - from_v_parts[0] == 1 and to_v_parts[1] == 0:
        # prev major (e.g. last 1.x.0 before 2.0.0)
        if last_major_releases[from_v_parts[0]] == from_v_parts:
            return _check_minimum_patch_release(from_v, to_v)
        return target_too_new  # preprev 1st number

    if to_v_parts[0] == from_v_parts[0]:
        if to_v_parts[1] - from_v_parts[1] > 1:
            return target_too_new  # preprev in 2nd number
        if to_v_parts[1] - from_v_parts[1] == 1:
            return _check_minimum_patch_release(from_v, to_v)  # prev in 2nd number, ignoring 3rd

    # Everything else is incompatible
    return target_too_new


_REQUIRED_PATCH_RELEASES_MAP: Final = {
    # max can be evaluated in place, obviously, but we keep a list for documentation.
    (2, 3, 0): max(
        (Version.from_str("2.2.0p1"),),  # at least the last major version, by default.
    ),
}


def _check_minimum_patch_release(
    from_v: Version, to_v: Version, /
) -> VersionsCompatible | VersionsIncompatible:
    if not (
        required_patch_release := _REQUIRED_PATCH_RELEASES_MAP.get(base_version_parts(str(to_v)))
    ):
        return VersionsCompatible()
    if from_v >= required_patch_release:
        return VersionsCompatible()
    return VersionsIncompatible(f"This target version requires at least {required_patch_release}")


#   .--general infos-------------------------------------------------------.
#   |                                      _   _        __                 |
#   |       __ _  ___ _ __   ___ _ __ __ _| | (_)_ __  / _| ___  ___       |
#   |      / _` |/ _ \ '_ \ / _ \ '__/ _` | | | | '_ \| |_ / _ \/ __|      |
#   |     | (_| |  __/ | | |  __/ | | (_| | | | | | | |  _| (_) \__ \      |
#   |      \__, |\___|_| |_|\___|_|  \__,_|_| |_|_| |_|_|  \___/|___/      |
#   |      |___/                                                           |
#   '----------------------------------------------------------------------'

# Collect general infos about CheckMk and OS which are used by crash reports
# and diagnostics.


def get_general_version_infos() -> dict[str, Any]:
    return {
        "time": time.time(),
        "os": _get_os_info(),
        "version": __version__,
        "edition": edition().short,
        "core": _current_monitoring_core(),
        "python_version": sys.version,
        "python_paths": sys.path,
    }


def _get_os_info() -> str:
    for path_release_file in (
        Path("/etc/redhat-release"),
        Path("/etc/SuSE-release"),
    ):
        if path_release_file.exists():
            with path_release_file.open() as release_file:
                return release_file.readline().strip()

    info = {}
    for path in [Path("/etc/os-release"), Path("/etc/lsb-release")]:
        if path.exists():
            with path.open() as release_file:
                for line in release_file.readlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        info[k.strip()] = v.strip().strip('"')
            break

    if "PRETTY_NAME" in info:
        return info["PRETTY_NAME"]

    if info:
        return "%s" % info

    if os.environ.get("OMD_ROOT"):
        disto_info = os.environ["OMD_ROOT"] + "/share/omd/distro.info"
        if os.path.exists(disto_info):
            return open(disto_info).readline().split("=", 1)[1].strip()

    return "UNKNOWN"


def _current_monitoring_core() -> str:
    try:
        completed_process = subprocess.run(
            ["omd", "config", "show", "CORE"],
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        # Allow running unit tests on systems without omd installed (e.g. on travis)
        return "UNKNOWN"
    return completed_process.stdout.rstrip()
