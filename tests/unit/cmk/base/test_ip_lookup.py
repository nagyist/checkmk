#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import socket
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias

import pytest
from pytest import MonkeyPatch

from tests.testlib.base import Scenario

from cmk.utils.tags import TagGroupID, TagID
from cmk.utils.type_defs import HostAddress, HostName

import cmk.base.config as config
import cmk.base.ip_lookup as ip_lookup

_PersistedCache: TypeAlias = Mapping[
    tuple[HostName | HostAddress, socket.AddressFamily], HostAddress | None
]


@pytest.fixture(autouse=True)
def no_io_ip_lookup_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ip_lookup.IPLookupCache, "PATH", tmp_path / "cache")


_PatchMapping = Mapping[ip_lookup.IPLookupCacheId, str | None]


def patch_config_cache(monkeypatch: MonkeyPatch, cache: _PatchMapping) -> None:
    monkeypatch.setattr(ip_lookup._config_cache, "get", lambda _x: cache)


def patch_persisted_cache(monkeypatch: MonkeyPatch, cache: _PatchMapping) -> None:
    monkeypatch.setattr(ip_lookup, "_get_ip_lookup_cache", lambda: cache)


def patch_actual_lookup(monkeypatch: MonkeyPatch, mapping: _PatchMapping) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda hn, _a, fm: [[0, 0, 0, 0, [mapping[(hn, fm)]]]]
    )


def _empty() -> dict[ip_lookup.IPLookupCacheId, str | None]:  # just centralize type hint...
    return {}


def test_cached_dns_lookup_is_config_cached_ok(monkeypatch: MonkeyPatch) -> None:
    patch_config_cache(monkeypatch, {(HostName("config_cached_host"), socket.AF_INET): "1.2.3.4"})
    patch_persisted_cache(
        monkeypatch, {(HostName("config_cached_host"), socket.AF_INET): "6.6.6.6"}
    )
    patch_actual_lookup(monkeypatch, {(HostName("config_cached_host"), socket.AF_INET): "7.7.7.7"})

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("config_cached_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=False,
        )
        == "1.2.3.4"
    )
    assert (
        ip_lookup.cached_dns_lookup(
            HostName("config_cached_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=True,
        )
        == "1.2.3.4"
    )


def test_cached_dns_lookup_is_config_cached_none(monkeypatch: MonkeyPatch) -> None:
    patch_config_cache(monkeypatch, {(HostName("the_host_that_raised"), socket.AF_INET6): None})
    patch_persisted_cache(monkeypatch, _empty())
    patch_actual_lookup(monkeypatch, _empty())

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("the_host_that_raised"),
            family=socket.AF_INET6,
            force_file_cache_renewal=False,
        )
        is None
    )
    assert (
        ip_lookup.cached_dns_lookup(
            HostName("the_host_that_raised"),
            family=socket.AF_INET6,
            force_file_cache_renewal=True,
        )
        is None
    )


def test_cached_dns_lookup_is_persisted_cached_ok(monkeypatch: MonkeyPatch) -> None:
    config_ipcache = _empty()
    persisted_cache: _PersistedCache = {
        (HostName("persisted_cached_host"), socket.AF_INET): "1.2.3.4"
    }

    patch_config_cache(monkeypatch, config_ipcache)
    patch_persisted_cache(monkeypatch, persisted_cache)
    patch_actual_lookup(
        monkeypatch, {(HostName("persisted_cached_host"), socket.AF_INET): "6.6.6.6"}
    )

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("persisted_cached_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=False,
        )
        == "1.2.3.4"
    )
    assert config_ipcache.pop((HostName("persisted_cached_host"), socket.AF_INET)) == "1.2.3.4"
    assert persisted_cache[(HostName("persisted_cached_host"), socket.AF_INET)] == "1.2.3.4"

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("persisted_cached_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=True,
        )
        == "6.6.6.6"
    )
    assert config_ipcache[(HostName("persisted_cached_host"), socket.AF_INET)] == "6.6.6.6"
    assert persisted_cache[(HostName("persisted_cached_host"), socket.AF_INET)] == "6.6.6.6"


def test_cached_dns_lookup_is_persisted_cached_ok_unchanged(monkeypatch: MonkeyPatch) -> None:
    config_ipcache = _empty()
    persisted_cache: _PersistedCache = {
        (HostName("persisted_cached_host"), socket.AF_INET): "1.2.3.4"
    }

    patch_config_cache(monkeypatch, config_ipcache)
    patch_persisted_cache(monkeypatch, persisted_cache)
    patch_actual_lookup(
        monkeypatch, {(HostName("persisted_cached_host"), socket.AF_INET): "1.2.3.4"}
    )

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("persisted_cached_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=False,
        )
        == "1.2.3.4"
    )
    assert config_ipcache.pop((HostName("persisted_cached_host"), socket.AF_INET)) == "1.2.3.4"
    assert persisted_cache[(HostName("persisted_cached_host"), socket.AF_INET)] == "1.2.3.4"

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("persisted_cached_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=True,
        )
        == "1.2.3.4"
    )
    assert config_ipcache.pop((HostName("persisted_cached_host"), socket.AF_INET)) == "1.2.3.4"
    assert persisted_cache[(HostName("persisted_cached_host"), socket.AF_INET)] == "1.2.3.4"


def test_cached_dns_lookup_uncached(monkeypatch: MonkeyPatch) -> None:
    config_ipcache = _empty()
    persisted_cache = _empty()

    patch_config_cache(monkeypatch, config_ipcache)
    patch_persisted_cache(monkeypatch, persisted_cache)
    patch_actual_lookup(monkeypatch, {(HostName("test_host"), socket.AF_INET): "3.1.4.1"})

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("test_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=False,
        )
        == "3.1.4.1"
    )
    assert config_ipcache.pop((HostName("test_host"), socket.AF_INET)) == "3.1.4.1"
    assert persisted_cache.pop((HostName("test_host"), socket.AF_INET)) == "3.1.4.1"

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("test_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=True,
        )
        == "3.1.4.1"
    )
    assert config_ipcache[(HostName("test_host"), socket.AF_INET)] == "3.1.4.1"
    assert persisted_cache[(HostName("test_host"), socket.AF_INET)] == "3.1.4.1"


def test_cached_dns_lookup_raises_once(monkeypatch: MonkeyPatch) -> None:
    config_ipcache = _empty()
    persisted_cache = _empty()

    patch_config_cache(monkeypatch, config_ipcache)
    patch_persisted_cache(monkeypatch, persisted_cache)
    patch_actual_lookup(monkeypatch, {})

    with pytest.raises(ip_lookup.MKIPAddressLookupError):
        _ = ip_lookup.cached_dns_lookup(
            HostName("test_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=False,
        )

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("test_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=False,
        )
        is None
    )
    assert (
        ip_lookup.cached_dns_lookup(
            HostName("test_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=True,
        )
        is None
    )


def test_filecache_beats_failing_lookup(monkeypatch: MonkeyPatch) -> None:
    config_ipcache = _empty()
    persisted_cache: _PersistedCache = {(HostName("test_host"), socket.AF_INET): "3.1.4.1"}

    patch_config_cache(monkeypatch, config_ipcache)
    patch_persisted_cache(monkeypatch, persisted_cache)
    patch_actual_lookup(monkeypatch, {})

    assert (
        ip_lookup.cached_dns_lookup(
            HostName("test_host"),
            family=socket.AF_INET,
            force_file_cache_renewal=True,
        )
        == "3.1.4.1"
    )
    assert persisted_cache[(HostName("test_host"), socket.AF_INET)]


# TODO: Can be removed when this is not executed through a symlink anymore.
# tests/unit/cmk/base/conftest.py::clear_config_caches() then cares about this.
@pytest.fixture(autouse=True, scope="function")
def clear_config_caches_ip_lookup(monkeypatch: MonkeyPatch) -> None:
    # pylint: disable=import-outside-toplevel

    from cmk.utils.caching import config_cache as _config_cache
    from cmk.utils.caching import runtime_cache as _runtime_cache

    # pylint:enable=import-outside-toplevel
    _config_cache.clear()
    _runtime_cache.clear()


class TestIPLookupCacheSerialzer:
    def test_simple_cache(self) -> None:
        s = ip_lookup.IPLookupCacheSerializer()
        cache_data: Mapping[tuple[HostName | HostAddress, socket.AddressFamily], HostAddress] = {
            (HostName("host1"), socket.AF_INET): "1"
        }
        assert s.deserialize(s.serialize(cache_data)) == cache_data


class TestIPLookupCache:
    def test_repr(self) -> None:
        assert isinstance(repr(ip_lookup.IPLookupCache({})), str)

    def test_load_invalid_syntax(self, tmp_path: Path) -> None:
        with ip_lookup.IPLookupCache.PATH.open(mode="w", encoding="utf-8") as f:
            f.write("{...")

        cache = ip_lookup.IPLookupCache({})
        with pytest.raises(SyntaxError):
            cache.load_persisted()
        assert not cache

    def test_update_empty_file(self, tmp_path: Path) -> None:
        cache_id = HostName("host1"), socket.AF_INET
        ip_lookup_cache = ip_lookup.IPLookupCache({})
        ip_lookup_cache[cache_id] = "127.0.0.1"

        new_cache_instance = ip_lookup.IPLookupCache({})
        new_cache_instance.load_persisted()
        assert new_cache_instance[cache_id] == "127.0.0.1"

    def test_update_existing_file(self, tmp_path: Path) -> None:
        cache_id1 = HostName("host1"), socket.AF_INET
        cache_id2 = HostName("host2"), socket.AF_INET

        ip_lookup_cache = ip_lookup.IPLookupCache({})
        ip_lookup_cache[cache_id1] = "127.0.0.1"
        ip_lookup_cache[cache_id2] = "127.0.0.2"

        new_cache_instance = ip_lookup.IPLookupCache({})
        new_cache_instance.load_persisted()
        assert new_cache_instance[cache_id1] == "127.0.0.1"
        assert new_cache_instance[cache_id2] == "127.0.0.2"

    def test_update_existing_entry(self, tmp_path: Path) -> None:
        cache_id1 = HostName("host1"), socket.AF_INET
        cache_id2 = HostName("host2"), socket.AF_INET

        ip_lookup_cache = ip_lookup.IPLookupCache(
            {
                cache_id1: "1",
                cache_id2: "2",
            }
        )
        ip_lookup_cache.save_persisted()

        ip_lookup_cache[cache_id1] = "127.0.0.1"

        new_cache_instance = ip_lookup.IPLookupCache({})
        new_cache_instance.load_persisted()
        assert new_cache_instance[cache_id1] == "127.0.0.1"
        assert new_cache_instance[cache_id2] == "2"

    def test_update_without_persistence(self, tmp_path: Path) -> None:
        cache_id1 = HostName("host1"), socket.AF_INET

        ip_lookup_cache = ip_lookup.IPLookupCache({})
        ip_lookup_cache[cache_id1] = "0.0.0.0"

        with ip_lookup_cache.persisting_disabled():
            ip_lookup_cache[cache_id1] = "127.0.0.1"

        assert ip_lookup_cache[cache_id1] == "127.0.0.1"

        new_cache_instance = ip_lookup.IPLookupCache({})
        new_cache_instance.load_persisted()
        assert new_cache_instance[cache_id1] == "0.0.0.0"

    def test_load_legacy(self, tmp_path: Path) -> None:
        cache_id1 = HostName("host1"), socket.AF_INET
        cache_id2 = HostName("host2"), socket.AF_INET

        with ip_lookup.IPLookupCache.PATH.open("w", encoding="utf-8") as f:
            f.write(repr({"host1": "127.0.0.1", "host2": "127.0.0.2"}))

        cache = ip_lookup.IPLookupCache({})
        cache.load_persisted()
        assert cache[cache_id1] == "127.0.0.1"
        assert cache[cache_id2] == "127.0.0.2"

    def test_clear(self, tmp_path: Path) -> None:
        ip_lookup.IPLookupCache({(HostName("host1"), socket.AF_INET): "127.0.0.1"}).save_persisted()

        ip_lookup_cache = ip_lookup.IPLookupCache({})
        ip_lookup_cache.load_persisted()
        assert ip_lookup_cache[(HostName("host1"), socket.AF_INET)] == "127.0.0.1"

        ip_lookup_cache.clear()

        assert not ip_lookup_cache

        ip_lookup_cache = ip_lookup.IPLookupCache({})
        ip_lookup_cache.load_persisted()
        assert not ip_lookup_cache


def test_update_dns_cache(monkeypatch: MonkeyPatch) -> None:
    def ip_lookup_cache() -> ip_lookup.IPLookupCache:
        cache = ip_lookup.IPLookupCache({})
        cache.load_persisted()
        return cache

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family=None, socktype=None, proto=None, flags=None: {
            ("blub", socket.AF_INET): [(family, None, None, None, ("127.0.0.13", 1337))],
            ("bla", socket.AF_INET): [(family, None, None, None, ("127.0.0.37", 1337))],
            ("dual", socket.AF_INET): [(family, None, None, None, ("127.0.0.42", 1337))],
        }[(host, family)],
    )

    ts = Scenario()
    ts.add_host(HostName("blub"), tags={TagGroupID("criticality"): TagID("offline")})
    ts.add_host(HostName("bla"))
    ts.add_host(HostName("dual"), tags={TagGroupID("address_family"): TagID("ip-v4v6")})
    ts.apply(monkeypatch)

    config_cache = config.get_config_cache()
    assert ip_lookup_cache() == {}

    result = ip_lookup.update_dns_cache(
        ip_lookup_configs=(
            config_cache.ip_lookup_config(hn) for hn in config_cache.all_active_hosts()
        ),
        configured_ipv4_addresses={},
        configured_ipv6_addresses={},
        simulation_mode=False,
        override_dns=None,
    )
    assert ip_lookup_cache() == {
        ("blub", socket.AF_INET): "127.0.0.13",
        ("bla", socket.AF_INET): "127.0.0.37",
        ("dual", socket.AF_INET): "127.0.0.42",
    }
    # Actual failure is:
    # MKIPAddressLookupError("Failed to lookup IPv6 address of dual via DNS: ('dual', <AddressFamily.AF_INET6: 10>)")
    assert result == (3, ["dual"])

    # Check persisted data
    cache = ip_lookup_cache()
    assert cache.get((HostName("blub"), socket.AF_INET)) == "127.0.0.13"
    assert cache.get((HostName("blub"), socket.AF_INET6)) is None
    assert cache.get((HostName("bla"), socket.AF_INET)) == "127.0.0.37"
    assert cache.get((HostName("bla"), socket.AF_INET6)) is None
    assert cache.get((HostName("dual"), socket.AF_INET)) == "127.0.0.42"
    assert cache.get((HostName("dual"), socket.AF_INET6)) is None


@pytest.mark.parametrize(
    "hostname_str, tags, result_address",
    [
        # default IPv4 host
        ("localhost", {}, "127.0.0.1"),
        ("127.0.0.1", {}, "127.0.0.1"),
        # explicit IPv4 host
        (
            "localhost",
            {
                TagGroupID("address_family"): TagID("ip-v4-only"),
            },
            "127.0.0.1",
        ),
        (
            "127.0.0.1",
            {
                TagGroupID("address_family"): TagID("ip-v4-only"),
            },
            "127.0.0.1",
        ),
    ],
)
def test_lookup_mgmt_board_ip_address_ipv4_host(
    monkeypatch: MonkeyPatch, hostname_str: str, tags: dict[TagGroupID, TagID], result_address: str
) -> None:
    hostname = HostName(hostname_str)
    ts = Scenario()
    ts.add_host(hostname, tags=tags)

    config_cache = ts.apply(monkeypatch)
    assert config.lookup_mgmt_board_ip_address(config_cache, hostname) == result_address


@pytest.mark.parametrize(
    "hostname_str, result_address",
    [
        ("localhost", "::1"),
        ("::1", "::1"),
    ],
)
def test_lookup_mgmt_board_ip_address_ipv6_host(
    monkeypatch: MonkeyPatch, hostname_str: str, result_address: str
) -> None:
    hostname = HostName(hostname_str)
    ts = Scenario()
    ts.add_host(hostname, tags={TagGroupID("address_family"): TagID("ip-v6-only")})
    config_cache = ts.apply(monkeypatch)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, family=None, socktype=None, proto=None, flags=None: {
            # That looks like a tautological tests.  It's most likely useless.
            ("localhost", socket.AF_INET6): [(family, None, None, None, (result_address, 0))],
            ("::1", socket.AF_INET6): [(family, None, None, None, (result_address, 0))],
        }[(host, family)],
    )

    assert config.lookup_mgmt_board_ip_address(config_cache, hostname) == result_address


@pytest.mark.parametrize(
    "hostname_str, result_address",
    [
        ("localhost", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_lookup_mgmt_board_ip_address_dual_host(
    monkeypatch: MonkeyPatch, hostname_str: str, result_address: str
) -> None:
    hostname = HostName(hostname_str)
    ts = Scenario()
    ts.add_host(
        hostname,
        tags={
            TagGroupID("address_family"): TagID("ip-v4v6"),
        },
    )

    config_cache = ts.apply(monkeypatch)
    assert config.lookup_mgmt_board_ip_address(config_cache, hostname) == result_address


@pytest.mark.parametrize(
    "tags, family",
    [
        ({}, socket.AF_INET),
        (
            {
                TagGroupID("address_family"): TagID("ip-v4-only"),
            },
            socket.AF_INET,
        ),
        (
            {
                TagGroupID("address_family"): TagID("ip-v6-only"),
            },
            socket.AF_INET6,
        ),
        (
            {
                TagGroupID("address_family"): TagID("ip-v4v6"),
            },
            socket.AF_INET,
        ),
    ],
)
def test_lookup_mgmt_board_ip_address_unresolveable(
    monkeypatch: MonkeyPatch, tags: dict[TagGroupID, TagID], family: socket.AddressFamily
) -> None:
    hostname = HostName("unresolveable-hostname")
    ts = Scenario()
    ts.add_host(hostname, tags=tags)

    config_cache = ts.apply(monkeypatch)
    assert config.lookup_mgmt_board_ip_address(config_cache, hostname) is None
