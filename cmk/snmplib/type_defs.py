#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import abc
import copy
import enum
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, cast, Literal, NamedTuple, TypeVar, Union

from cmk.utils.type_defs import AgentRawData as _AgentRawData
from cmk.utils.type_defs import HostAddress as _HostAddress
from cmk.utils.type_defs import HostName as _HostName
from cmk.utils.type_defs import SectionName as _SectionName
from cmk.utils.type_defs import SNMPDetectBaseType as _SNMPDetectBaseType

SNMPContextName = str
SNMPDecodedString = str
SNMPDecodedBinary = Sequence[int]
SNMPDecodedValues = SNMPDecodedString | SNMPDecodedBinary
SNMPValueEncoding = Literal["string", "binary"]
SNMPTable = Sequence[SNMPDecodedValues]
SNMPContext = str | None
SNMPRawDataSection = SNMPTable | Sequence[SNMPTable]
# The SNMPRawData type is not useful.  See comments to `AgentRawDataSection`.
#
#     **WE DO NOT WANT `NewType` HERE** because this prevents us to
#     type some classes correctly.  The type should be *REMOVED* instead!
#
SNMPRawData = Mapping[_SectionName, Sequence[SNMPRawDataSection]]
OID = str
OIDFunction = Callable[
    [OID, SNMPDecodedString | None, _SectionName | None], SNMPDecodedString | None
]
OIDRange = tuple[int, int]
# We still need "Union" because of https://github.com/python/mypy/issues/11098
RangeLimit = Union[
    tuple[Literal["first", "last"], int],
    tuple[Literal["mid"], OIDRange],
]

SNMPScanFunction = Callable[[OIDFunction], bool]
SNMPRawValue = bytes
SNMPRowInfo = list[tuple[OID, SNMPRawValue]]

# TODO: Be more specific about the possible tuples
# if the credentials are a string, we use that as community,
# if it is a four-tuple, we use it as V3 auth parameters:
# (1) security level (-l)
# (2) auth protocol (-a, e.g. 'md5')
# (3) security name (-u)
# (4) auth password (-A)
# And if it is a six-tuple, it has the following additional arguments:
# (5) privacy protocol (DES|AES) (-x)
# (6) privacy protocol pass phrase (-X)
SNMPCommunity = str
# TODO: This does not work as intended
# SNMPv3NoAuthNoPriv = tuple[str, str]
# SNMPv3AuthNoPriv = tuple[str, str, str, str]
# SNMPv3AuthPriv = tuple[str, str, str, str, str, str]
# SNMPCredentials = SNMPCommunity | SNMPv3NoAuthNoPriv | SNMPv3AuthNoPriv | SNMPv3AuthPriv
SNMPCredentials = SNMPCommunity | tuple[str, ...]
# TODO: Cleanup to named tuple
SNMPTiming = dict

SNMPDetectAtom = tuple[str, str, bool]  # (oid, regex_pattern, expected_match)

# TODO(ml): This type does not really belong here but there currently
#           is not better place.
AbstractRawData = _AgentRawData | SNMPRawData
TRawData = TypeVar("TRawData", bound=AbstractRawData)


class SNMPBackendEnum(enum.Enum):
    INLINE = "Inline"
    CLASSIC = "Classic"
    STORED_WALK = "StoredWalk"

    def serialize(self) -> str:
        return self.name

    @classmethod
    def deserialize(cls, name: str) -> "SNMPBackendEnum":
        return cls[name]


class SNMPDetectSpec(_SNMPDetectBaseType):
    """A specification for SNMP device detection"""

    @classmethod
    def from_json(cls, serialized: Mapping[str, Any]) -> "SNMPDetectSpec":
        try:
            # The cast is necessary as mypy does not infer types in a list comprehension.
            # See https://github.com/python/mypy/issues/5068
            return cls(
                [
                    [cast(SNMPDetectAtom, tuple(inner)) for inner in outer]
                    for outer in serialized["snmp_detect_spec"]
                ]
            )
        except (LookupError, TypeError, ValueError) as exc:
            raise ValueError(serialized) from exc

    def to_json(self) -> Mapping[str, Any]:
        return {"snmp_detect_spec": self}


# Wraps the configuration of a host into a single object for the SNMP code
class SNMPHostConfig(NamedTuple):
    is_ipv6_primary: bool
    hostname: _HostName
    ipaddress: _HostAddress
    credentials: SNMPCredentials
    port: int
    is_bulkwalk_host: bool
    is_snmpv2or3_without_bulkwalk_host: bool
    bulk_walk_size_of: int
    timing: SNMPTiming
    oid_range_limits: Mapping[_SectionName, Sequence[RangeLimit]]
    snmpv3_contexts: list
    character_encoding: str | None
    snmp_backend: SNMPBackendEnum

    @property
    def is_snmpv3_host(self) -> bool:
        return isinstance(self.credentials, tuple)

    def snmpv3_contexts_of(
        self,
        section_name: _SectionName | None,
    ) -> Sequence[SNMPContext]:
        if not section_name or not self.is_snmpv3_host:
            return [None]
        section_name_str = str(section_name)
        for ty, rules in self.snmpv3_contexts:
            if ty is None or ty == section_name_str:
                return rules
        return [None]

    def ensure_str(self, value: str | bytes) -> str:
        if isinstance(value, str):
            return value
        if self.character_encoding:
            return value.decode(self.character_encoding)
        try:
            return value.decode()
        except UnicodeDecodeError:
            return value.decode("latin1")

    def serialize(self):
        serialized = self._asdict()
        serialized["snmp_backend"] = serialized["snmp_backend"].serialize()
        serialized["oid_range_limits"] = {
            str(sn): rl for sn, rl in serialized["oid_range_limits"].items()
        }
        return serialized

    @classmethod
    def deserialize(cls, serialized: Mapping[str, Any]) -> "SNMPHostConfig":
        serialized_ = copy.deepcopy(dict(serialized))
        serialized_["snmp_backend"] = SNMPBackendEnum.deserialize(serialized_["snmp_backend"])
        serialized_["oid_range_limits"] = {
            _SectionName(sn): rl for sn, rl in serialized_["oid_range_limits"].items()
        }
        return cls(**serialized_)


class SNMPBackend(abc.ABC):
    def __init__(self, snmp_config: SNMPHostConfig, logger: logging.Logger) -> None:
        super().__init__()
        self._logger = logger
        self.config = snmp_config

    @property
    def hostname(self) -> _HostName:
        return self.config.hostname

    @property
    def address(self) -> _HostAddress | None:
        return self.config.ipaddress

    @property
    def port(self) -> int:
        return self.config.port

    @port.setter
    def port(self, new_port: int) -> None:
        self.config = self.config._replace(port=new_port)

    @abc.abstractmethod
    def get(self, oid: OID, context_name: SNMPContextName | None = None) -> SNMPRawValue | None:
        """Fetch a single OID from the given host in the given SNMP context
        The OID may end with .* to perform a GETNEXT request. Otherwise a GET
        request is sent to the given host.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def walk(
        self,
        oid: OID,
        section_name: _SectionName | None = None,
        table_base_oid: OID | None = None,
        context_name: SNMPContextName | None = None,
    ) -> SNMPRowInfo:
        return []


class SpecialColumn(enum.IntEnum):
    # Until we remove all but the first, its worth having an enum
    END = 0  # Suffix-part of OID that was not specified
    STRING = -1  # Complete OID as string ".1.3.6.1.4.1.343...."
    BIN = -2  # Complete OID as binary string "\x01\x03\x06\x01..."
    END_BIN = -3  # Same, but just the end part
    END_OCTET_STRING = -4  # yet same, but omit first byte (assuming that is the length byte)


class BackendOIDSpec(NamedTuple):
    column: str | SpecialColumn
    encoding: SNMPValueEncoding
    save_to_cache: bool

    def _serialize(self) -> tuple[str, str, bool] | tuple[int, str, bool]:
        if isinstance(self.column, SpecialColumn):
            return (int(self.column), self.encoding, self.save_to_cache)
        return (self.column, self.encoding, self.save_to_cache)

    @classmethod
    def deserialize(
        cls,
        column: str | int,
        encoding: SNMPValueEncoding,
        save_to_cache: bool,
    ) -> "BackendOIDSpec":
        return cls(
            SpecialColumn(column) if isinstance(column, int) else column, encoding, save_to_cache
        )


class BackendSNMPTree(NamedTuple):
    """The 'working class' pendant to the check APIs 'SNMPTree'

    It mainly features (de)serialization. Validation is done during
    section registration, so we can assume sane values here.
    """

    base: str
    oids: Sequence[BackendOIDSpec]

    @classmethod
    def from_frontend(
        cls,
        *,
        base: str,
        oids: Iterable[tuple[str | int, SNMPValueEncoding, bool]],
    ) -> "BackendSNMPTree":
        return cls(
            base=base,
            oids=[BackendOIDSpec.deserialize(*oid) for oid in oids],
        )

    def to_json(self) -> Mapping[str, Any]:
        return {
            "base": self.base,
            "oids": [oid._serialize() for oid in self.oids],
        }

    @classmethod
    def from_json(cls, serialized: Mapping[str, Any]) -> "BackendSNMPTree":
        return cls(
            base=serialized["base"],
            oids=[BackendOIDSpec.deserialize(*oid) for oid in serialized["oids"]],
        )
