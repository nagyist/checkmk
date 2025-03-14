#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Checkmk wide type definitions"""

__all__ = [
    "ValidatedString",
    "ActiveCheckPluginName",
    "AgentRawData",
    "AgentTargetVersion",
    "CheckPluginNameStr",
    "ClusterMode",
    "Contact",
    "ContactgroupName",
    "ContactName",
    "Deserializer",
    "DisabledNotificationsOptions",
    "DiscoveryResult",
    "ECEventContext",
    "EvalableFloat",
    "EventContext",
    "EventRule",
    "EVERYTHING",
    "ExitSpec",
    "ensure_mrpe_configs",
    "Gateways",
    "HandlerName",
    "HandlerParameters",
    "HostAddress",
    "HostAgentConnectionMode",
    "HostgroupName",
    "HostName",
    "HostOrServiceConditionRegex",
    "HostOrServiceConditions",
    "HostOrServiceConditionsNegated",
    "HostOrServiceConditionsSimple",
    "HostState",
    "HostsToUpdate",
    "HTTPMethod",
    "InfluxDBConnectionSpec",
    "Item",
    "LegacyCheckParameters",
    "MrpeConfig",
    "MrpeConfigDeprecated",
    "MetricName",
    "MetricTuple",
    "NotificationContext",
    "NotificationType",
    "NotifyAnalysisInfo",
    "NotifyBulk",
    "NotifyBulkParameters",
    "NotifyBulks",
    "NotifyPluginInfo",
    "NotifyPluginName",
    "NotifyPluginParams",
    "NotifyPluginParamsDict",
    "NotifyPluginParamsList",
    "NotifyRuleInfo",
    "ParametersTypeAlias",
    "ParsedSectionName",
    "PhaseOneResult",
    "PluginNotificationContext",
    "RuleSetName",
    "Seconds",
    "SectionName",
    "Serializer",
    "ServiceAdditionalDetails",
    "ServiceDetails",
    "ServicegroupName",
    "ServiceName",
    "ServiceState",
    "SNMPDetectBaseType",
    "state_markers",
    "TimeperiodName",
    "TimeperiodSpec",
    "timeperiod_spec_alias",
    "TimeperiodSpecs",
    "TimeRange",
    "Timestamp",
    "UpdateDNSCacheResult",
    "UserId",
    "UUIDs",
]


from ._misc import (  # TODO(ML): We should clean this up some day.
    ActiveCheckPluginName,
    AgentRawData,
    AgentTargetVersion,
    CheckPluginNameStr,
    ClusterMode,
    ContactgroupName,
    DiscoveryResult,
    EvalableFloat,
    EVERYTHING,
    ExitSpec,
    HostOrServiceConditionRegex,
    HostOrServiceConditions,
    HostOrServiceConditionsNegated,
    HostOrServiceConditionsSimple,
    HTTPMethod,
    InfluxDBConnectionSpec,
    Item,
    LegacyCheckParameters,
    MetricName,
    MetricTuple,
    ParametersTypeAlias,
    Seconds,
    ServiceAdditionalDetails,
    ServiceDetails,
    ServicegroupName,
    ServiceName,
    ServiceState,
    SNMPDetectBaseType,
    state_markers,
    timeperiod_spec_alias,
    TimeperiodName,
    TimeperiodSpec,
    TimeperiodSpecs,
    TimeRange,
    Timestamp,
)
from .automations import PhaseOneResult
from .core_config import HostsToUpdate
from .host import HostAddress, HostAgentConnectionMode, HostgroupName, HostName, HostState
from .ip_lookup import UpdateDNSCacheResult
from .mrpe_config import ensure_mrpe_configs, MrpeConfig, MrpeConfigDeprecated
from .notify import (
    Contact,
    ContactName,
    DisabledNotificationsOptions,
    ECEventContext,
    EventContext,
    EventRule,
    HandlerName,
    HandlerParameters,
    NotificationContext,
    NotificationType,
    NotifyAnalysisInfo,
    NotifyBulk,
    NotifyBulkParameters,
    NotifyBulks,
    NotifyPluginInfo,
    NotifyPluginName,
    NotifyPluginParams,
    NotifyPluginParamsDict,
    NotifyPluginParamsList,
    NotifyRuleInfo,
    PluginNotificationContext,
    UUIDs,
)
from .parent_scan import Gateways
from .pluginname import ParsedSectionName, RuleSetName, SectionName, ValidatedString
from .protocol import Deserializer, Serializer
from .user_id import UserId
