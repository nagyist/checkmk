#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict, Union

from pydantic import BaseModel

from livestatus import SiteId

from cmk.utils.cpu_tracking import Snapshot
from cmk.utils.crypto import HashAlgorithm
from cmk.utils.crypto.certificate import (
    Certificate,
    CertificatePEM,
    CertificateWithPrivateKey,
    EncryptedPrivateKeyPEM,
    RsaPrivateKey,
)
from cmk.utils.crypto.password import Password, PasswordHash
from cmk.utils.labels import Labels
from cmk.utils.structured_data import SDPath
from cmk.utils.type_defs import (
    ContactgroupName,
    DisabledNotificationsOptions,
    EventRule,
    HostName,
    MetricName,
    ServiceName,
    UserId,
)

from cmk.gui.exceptions import FinalizeRequest
from cmk.gui.utils.speaklater import LazyString

SizePT = float
SizeMM = float
HTTPVariables = list[tuple[str, int | str | None]]
LivestatusQuery = str
PermissionName = str
RoleName = str
CSSSpec = list[str]
ChoiceText = str
ChoiceId = str | None
Choice = tuple[ChoiceId, ChoiceText]
Choices = list[Choice]  # TODO: Change to Sequence, perhaps DropdownChoiceEntries[str]


@dataclass
class UserRole:
    name: str
    alias: str
    builtin: bool = False
    permissions: dict[str, bool] = field(default_factory=dict)
    basedon: str | None = None

    def to_dict(self) -> dict:
        userrole_dict = {
            "alias": self.alias,
            "permissions": self.permissions,
            "builtin": self.builtin,
        }

        if not self.builtin:
            userrole_dict["basedon"] = self.basedon

        return userrole_dict


class ChoiceGroup(NamedTuple):
    title: str
    choices: Choices


GroupedChoices = list[ChoiceGroup]


class WebAuthnCredential(TypedDict):
    credential_id: str
    registered_at: int
    alias: str
    credential_data: bytes


class TwoFactorCredentials(TypedDict):
    webauthn_credentials: dict[str, WebAuthnCredential]
    backup_codes: list[PasswordHash]


SessionId = str
AuthType = Literal["automation", "cookie", "web_server", "http_header", "bearer", "basic_auth"]


@dataclass
class SessionInfo:
    session_id: SessionId
    started_at: int
    last_activity: int
    csrf_token: str = field(default_factory=lambda: str(uuid.uuid4()))
    flashes: list[str] = field(default_factory=list)
    # In case it is enabled: Was it already authenticated?
    two_factor_completed: bool = False
    # We don't care about the specific object, because it's internal to the fido2 library
    webauthn_action_state: object = None

    logged_out: bool = field(default=False)
    auth_type: AuthType | None = None

    def to_json(self) -> dict:
        # We assume here that asdict() does the right thing for the
        # webauthn_action_state field. This can be the case, but it's not very
        # obvious.
        return asdict(self)

    def invalidate(self) -> None:
        """Called when a user logged out"""
        self.auth_type = None
        self.logged_out = True

    def refresh(self, now: datetime | None = None) -> None:
        """Called on any user activity.

        >>> now = datetime(2022, 1, 1, 0, 0, 0)
        >>> info = SessionInfo(session_id="", started_at=0, last_activity=0)
        >>> info.refresh(now)
        >>> assert info.last_activity == int(now.timestamp())

        Args:
            now:

        Returns:

        """
        if now is None:
            now = datetime.now()
        self.last_activity = int(now.timestamp())


class UserSpec(TypedDict, total=False):
    """This is not complete, but they don't yet...  Also we have a
    user_attribute_registry (cmk/gui/plugins/userdb/utils.py)

    I ignored two mypy findings in cmk/gui/userdb.py grep for ignore[misc]
    """

    alias: str
    authorized_sites: Any  # TODO: Improve this
    automation_secret: str
    connector: str | None
    contactgroups: list[ContactgroupName]
    customer: str | None
    disable_notifications: DisabledNotificationsOptions
    email: str  # TODO: Why do we have "email" *and* "mail"?
    enforce_pw_change: bool | None
    fallback_contact: bool | None
    force_authuser: bool
    host_notification_options: str
    idle_timeout: Any  # TODO: Improve this
    language: str
    last_pw_change: int
    locked: bool | None
    mail: str  # TODO: Why do we have "email" *and* "mail"?
    notification_method: Any  # TODO: Improve this
    notification_period: str
    notification_rules: list[EventRule]  # yes, we actually modify this! :-/
    notifications_enabled: bool | None
    num_failed_logins: int
    pager: str
    password: PasswordHash
    roles: list[str]
    serial: int
    service_notification_options: str
    session_info: dict[SessionId, SessionInfo]
    show_mode: str | None
    start_url: str | None
    two_factor_credentials: TwoFactorCredentials
    ui_sidebar_position: Any  # TODO: Improve this
    ui_theme: Any  # TODO: Improve this
    user_id: UserId
    user_scheme_serial: int
    nav_hide_icons_title: Literal["hide"] | None
    icons_per_item: Literal["entry"] | None
    temperature_unit: str | None


class UserObjectValue(TypedDict):
    attributes: UserSpec
    is_new_user: bool


UserObject = dict[UserId, UserObjectValue]

Users = dict[UserId, UserSpec]  # TODO: Improve this type

# Visual specific
FilterName = str
FilterHTTPVariables = Mapping[str, str]
VisualName = str
VisualTypeName = Literal["dashboards", "views", "reports"]
VisualContext = Mapping[FilterName, FilterHTTPVariables]
InfoName = str
SingleInfos = Sequence[InfoName]


class LinkFromSpec(TypedDict, total=False):
    single_infos: SingleInfos
    host_labels: Labels
    has_inventory_tree: Sequence[SDPath]
    has_inventory_tree_history: Sequence[SDPath]


class Visual(TypedDict):
    owner: UserId
    name: str
    context: VisualContext
    single_infos: SingleInfos
    add_context_to_title: bool
    title: str | LazyString
    description: str | LazyString
    topic: str
    sort_index: int
    is_show_more: bool
    icon: Icon | None
    hidden: bool
    hidebutton: bool
    public: bool | tuple[Literal["contact_groups"], Sequence[str]]
    packaged: bool
    link_from: LinkFromSpec


class VisualLinkSpec(NamedTuple):
    type_name: VisualTypeName
    name: VisualName

    @classmethod
    def from_raw(cls, value: VisualName | tuple[VisualTypeName, VisualName]) -> VisualLinkSpec:
        # With Checkmk 2.0 we introduced the option to link to dashboards. Now the link_view is not
        # only a string (view_name) anymore, but a tuple of two elemets: ('<visual_type_name>',
        # '<visual_name>'). Transform the old value to the new format.
        if isinstance(value, tuple):
            return cls(value[0], value[1])

        return cls("views", value)

    def to_raw(self) -> tuple[VisualTypeName, VisualName]:
        return self.type_name, self.name


# View specific
Row = dict[str, Any]  # TODO: Improve this type
Rows = list[Row]
PainterName = str
SorterName = str
ViewName = str
ColumnName = str


class PainterParameters(TypedDict, total=False):
    # TODO Improve:
    # First step was: make painter's param a typed dict with all obvious keys
    # but some possible keys are still missing
    aggregation: tuple[str, str]
    color_choices: list[str]
    column_title: str
    ident: str
    max_len: int
    metric: str
    render_states: list[int | str]
    use_short: bool
    uuid: str
    # From join inv painter params
    path_to_table: SDPath
    column_to_display: str
    columns_to_match: list[tuple[str, str]]


def _make_default_painter_parameters() -> PainterParameters:
    return PainterParameters()


ColumnTypes = Literal["column", "join_column", "join_inv_column"]


class _RawCommonColumnSpec(TypedDict):
    name: PainterName
    parameters: PainterParameters | None
    link_spec: tuple[VisualTypeName, VisualName] | None
    tooltip: ColumnName | None
    column_title: str | None
    column_type: ColumnTypes | None


class _RawLegacyColumnSpec(_RawCommonColumnSpec):
    join_index: ColumnName | None


class _RawColumnSpec(_RawCommonColumnSpec):
    join_value: ColumnName | None


@dataclass(frozen=True)
class ColumnSpec:
    name: PainterName
    parameters: PainterParameters = field(default_factory=_make_default_painter_parameters)
    link_spec: VisualLinkSpec | None = None
    tooltip: ColumnName | None = None
    join_value: ColumnName | None = None
    column_title: str | None = None
    _column_type: ColumnTypes | None = None

    @property
    def column_type(self) -> ColumnTypes:
        if self._column_type in ["column", "join_column", "join_inv_column"]:
            return self._column_type

        # First note:
        #   The "column_type" is used for differentiating ColumnSpecs in the view editor
        #   dialog. ie. "Column", "Joined Column", "Joined inventory column".
        # We have two entry points for ColumnSpec initialization:
        # 1. In "group_painters" or "painters" in views/dashboards/..., eg. views/builtin_views.py
        #    Here the "_column_type" is not set but calculated from "join_value".
        #    This only applies to "column" and "join_column" but not to "join_inv_column"
        #    because there are no such pre-defined ColumnSpecs
        # 2. during loading of visuals
        #    Here the "column_type" is part of the raw ColumnSpec which is add below in "from_raw"
        # Thus we don't need to handle "join_inv_column" here as long as there are no pre-defined
        # ColumnSpecs.
        return self._get_column_type_from_join_value(self.join_value)

    @classmethod
    def from_raw(cls, value: _RawColumnSpec | _RawLegacyColumnSpec | tuple) -> ColumnSpec:
        # TODO
        # 1: The params-None case can be removed with Checkmk 2.3
        # 2: The tuple-case can be removed with Checkmk 2.4.
        # 3: The join_index case can be removed with Checkmk 2.3
        # => The transformation is done via update_config/plugins/actions/cre_visuals.py

        if isinstance(value, dict):

            def _get_join_value(value: _RawColumnSpec | _RawLegacyColumnSpec) -> ColumnName | None:
                if isinstance(join_value := value.get("join_value"), str):
                    return join_value
                if isinstance(join_value := value.get("join_index"), str):
                    return join_value
                return None

            return cls(
                name=value["name"],
                parameters=value["parameters"] or PainterParameters(),
                link_spec=(
                    None
                    if (link_spec := value["link_spec"]) is None
                    else VisualLinkSpec.from_raw(link_spec)
                ),
                tooltip=value["tooltip"] or None,
                join_value=_get_join_value(value),
                column_title=value["column_title"],
                _column_type=value.get("column_type"),
            )

        # Some legacy views have optional fields like "tooltip" set to "" instead of None
        # in their definitions. Consolidate this case to None.
        value = (value[0],) + tuple(p or None for p in value[1:]) + (None,) * (5 - len(value))

        if isinstance(value[0], tuple):
            name, parameters = value[0]
        else:
            name = value[0]
            parameters = PainterParameters()

        join_value = value[3]
        return cls(
            name=name,
            parameters=parameters,
            link_spec=None if value[1] is None else VisualLinkSpec.from_raw(value[1]),
            tooltip=value[2],
            join_value=join_value,
            column_title=value[4],
            _column_type=cls._get_column_type_from_join_value(join_value),
        )

    @staticmethod
    def _get_column_type_from_join_value(
        join_value: ColumnName | None,
    ) -> Literal["column", "join_column"]:
        return "column" if join_value is None else "join_column"

    def to_raw(self) -> _RawColumnSpec:
        return {
            "name": self.name,
            "parameters": self.parameters,
            "link_spec": None if self.link_spec is None else self.link_spec.to_raw(),
            "tooltip": self.tooltip,
            "join_value": self.join_value,
            "column_title": self.column_title,
            "column_type": self.column_type,
        }

    def __repr__(self) -> str:
        """
        Used to serialize user-defined visuals
        """
        return str(self.to_raw())


@dataclass(frozen=True)
class SorterSpec:
    # The sorter parameters should be moved to a separate attribute instead
    sorter: SorterName | tuple[SorterName, PainterParameters]
    negate: bool
    join_key: str | None = None

    def to_raw(
        self,
    ) -> tuple[SorterName | tuple[SorterName, PainterParameters], bool, str | None]:
        return (
            self.sorter,
            self.negate,
            self.join_key,
        )

    def __repr__(self) -> str:
        """
        Used to serialize user-defined visuals
        """
        return str(self.to_raw())


class _InventoryJoinMacrosSpec(TypedDict):
    macros: list[tuple[str, str]]


class ViewSpec(Visual):
    datasource: str
    layout: str  # TODO: Replace with literal? See layout_registry.get_choices()
    group_painters: Sequence[ColumnSpec]
    painters: Sequence[ColumnSpec]
    browser_reload: int
    num_columns: int
    column_headers: Literal["off", "pergroup", "repeat"]
    sorters: Sequence[SorterSpec]
    add_headers: NotRequired[str]
    # View editor only adds them in case they are truish. In our builtin specs these flags are also
    # partially set in case they are falsy
    mobile: NotRequired[bool]
    mustsearch: NotRequired[bool]
    force_checkboxes: NotRequired[bool]
    user_sortable: NotRequired[bool]
    play_sounds: NotRequired[bool]
    inventory_join_macros: NotRequired[_InventoryJoinMacrosSpec]


AllViewSpecs = dict[tuple[UserId, ViewName], ViewSpec]
PermittedViewSpecs = dict[ViewName, ViewSpec]

SorterFunction = Callable[[ColumnName, Row, Row], int]
FilterHeader = str


class GroupSpec(TypedDict):
    title: str
    pattern: str
    min_items: int


class SetOnceDict(dict):
    """A subclass of `dict` on which every key can only ever be set once.

    Apart from preventing keys to be set again, and the fact that keys can't be removed it works
    just like a regular dict.

    Examples:

        >>> d = SetOnceDict()
        >>> d['foo'] = 'bar'
        >>> d['foo'] = 'bar'
        Traceback (most recent call last):
        ...
        ValueError: key 'foo' already set

    """

    def __setitem__(self, key, value):
        if key in self:
            raise ValueError(f"key {key!r} already set")
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        raise NotImplementedError("Deleting items are not supported.")


class ABCMegaMenuSearch(ABC):
    """Abstract base class for search fields in mega menus"""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def onopen(self) -> str:
        return 'cmk.popup_menu.focus_search_field("mk_side_search_field_%s");' % self.name

    @abstractmethod
    def show_search_field(self) -> None:
        ...


class _Icon(TypedDict):
    icon: str
    emblem: str | None


Icon = str | _Icon


class TopicMenuItem(NamedTuple):
    name: str
    title: str
    sort_index: int
    url: str
    target: str = "main"
    is_show_more: bool = False
    icon: Icon | None = None
    button_title: str | None = None
    additional_matches_setup_search: Sequence[str] = ()


class TopicMenuTopic(NamedTuple):
    name: str
    title: str
    items: list[TopicMenuItem]
    max_entries: int = 10
    icon: Icon | None = None
    hide: bool = False


class MegaMenu(NamedTuple):
    name: str
    title: str | LazyString
    icon: Icon
    sort_index: int
    topics: Callable[[], list[TopicMenuTopic]]
    search: ABCMegaMenuSearch | None = None
    info_line: Callable[[], str] | None = None
    hide: Callable[[], bool] = lambda: False


SearchQuery = str


@dataclass
class SearchResult:
    """Representation of a single result"""

    title: str
    url: str
    context: str = ""


SearchResultsByTopic = Iterable[tuple[str, Iterable[SearchResult]]]

# Metric & graph specific

UnitRenderFunc = Callable[[Any], str]


GraphTitleFormat = Literal["plain", "add_host_name", "add_host_alias", "add_service_description"]
GraphUnitRenderFunc = Callable[[list[float]], tuple[str, list[str]]]


class UnitInfo(TypedDict):
    title: str
    symbol: str
    render: UnitRenderFunc
    js_render: str
    id: NotRequired[str]
    stepping: NotRequired[str]
    color: NotRequired[str]
    graph_unit: NotRequired[GraphUnitRenderFunc]
    description: NotRequired[str]
    valuespec: NotRequired[Any]  # TODO: better typing


class _TranslatedMetricRequired(TypedDict):
    scale: list[float]


class TranslatedMetric(_TranslatedMetricRequired, total=False):
    # All keys seem to be optional. At least in one situation there is a translation object
    # constructed which only has the scale member (see
    # CustomGraphPage._show_metric_type_combined_summary)
    orig_name: list[str]
    value: float
    scalar: dict[str, float]
    auto_graph: bool
    title: str
    unit: UnitInfo
    color: str


GraphPresentation = str  # TODO: Improve Literal["lines", "stacked", "sum", "average", "min", "max"]
GraphConsoldiationFunction = Literal["max", "min", "average"]

RenderingExpression = tuple[Any, ...]
TranslatedMetrics = dict[str, TranslatedMetric]
MetricExpression = str
LineType = str  # TODO: Literal["line", "area", "stack", "-line", "-area", "-stack"]
# We still need "Union" because of https://github.com/python/mypy/issues/11098
MetricDefinition = Union[
    tuple[MetricExpression, LineType],
    tuple[MetricExpression, LineType, str | LazyString],
]
PerfometerSpec = dict[str, Any]
PerfdataTuple = tuple[str, float, str, float | None, float | None, float | None, float | None]
Perfdata = list[PerfdataTuple]
RGBColor = tuple[float, float, float]  # (1.5, 0.0, 0.5)


class RowShading(TypedDict):
    enabled: bool
    odd: RGBColor
    even: RGBColor
    heading: RGBColor


RPNExpression = tuple  # TODO: Improve this type

HorizontalRule = tuple[float, str, str, str | LazyString]


class GraphMetric(TypedDict):
    title: str
    line_type: LineType
    expression: RPNExpression
    unit: NotRequired[str]
    color: NotRequired[str]
    visible: NotRequired[bool]


class GraphSpec(TypedDict):
    pass


class TemplateGraphSpec(GraphSpec):
    site: SiteId | None
    host_name: HostName
    service_description: ServiceName
    graph_index: NotRequired[int | None]
    graph_id: NotRequired[str | None]


class ExplicitGraphSpec(GraphSpec):
    title: str
    unit: str
    consolidation_function: GraphConsoldiationFunction | None
    explicit_vertical_range: tuple[float | None, float | None]
    omit_zero_metrics: bool
    horizontal_rules: Sequence[HorizontalRule]
    context: VisualContext
    add_context_to_title: bool
    metrics: Sequence[GraphMetric]


class CombinedGraphSpec(GraphSpec):
    datasource: str
    single_infos: SingleInfos
    presentation: GraphPresentation
    context: VisualContext
    selected_metric: NotRequired[MetricDefinition]
    consolidation_function: NotRequired[GraphConsoldiationFunction]
    graph_template: NotRequired[str]


class _SingleTimeseriesGraphSpecMandatory(GraphSpec):
    site: str
    metric: MetricName


class SingleTimeseriesGraphSpec(_SingleTimeseriesGraphSpecMandatory, total=False):
    host: HostName
    service: ServiceName
    service_description: ServiceName
    color: str | None


TemplateGraphIdentifier = tuple[Literal["template"], TemplateGraphSpec]
CombinedGraphIdentifier = tuple[Literal["combined"], CombinedGraphSpec]
CustomGraphIdentifier = tuple[Literal["custom"], str]
ExplicitGraphIdentifier = tuple[Literal["explicit"], ExplicitGraphSpec]
SingleTimeseriesGraphIdentifier = tuple[Literal["single_timeseries"], SingleTimeseriesGraphSpec]
ForecastGraphIdentifier = tuple[Literal["forecast"], str]

# We still need "Union" because of https://github.com/python/mypy/issues/11098
GraphIdentifier = Union[
    CustomGraphIdentifier,
    ForecastGraphIdentifier,
    TemplateGraphIdentifier,
    CombinedGraphIdentifier,
    ExplicitGraphIdentifier,
    SingleTimeseriesGraphIdentifier,
]


class RenderableRecipe(NamedTuple):
    title: str
    expression: RenderingExpression
    color: str
    line_type: str
    visible: bool


ActionResult = FinalizeRequest | None


@dataclass
class ViewProcessTracking:
    amount_unfiltered_rows: int = 0
    amount_filtered_rows: int = 0
    amount_rows_after_limit: int = 0
    duration_fetch_rows: Snapshot = Snapshot.null()
    duration_filter_rows: Snapshot = Snapshot.null()
    duration_view_render: Snapshot = Snapshot.null()


class CustomAttr(TypedDict):
    title: str
    help: str
    name: str
    topic: str
    type: str
    add_custom_macro: bool
    show_in_table: bool


class Key(BaseModel):
    certificate: str
    private_key: str
    alias: str
    owner: UserId
    date: float
    # Before 2.2 this field was only used for Setup backup keys. Now we add it to all key, because it
    # won't hurt for other types of keys (e.g. the bakery signing keys). We set a default of False
    # to initialize it for all existing keys assuming it was already downloaded. It is still only
    # used in the context of the backup keys.
    not_downloaded: bool = False

    def to_certificate_with_private_key(self, passphrase: Password) -> CertificateWithPrivateKey:
        return CertificateWithPrivateKey(
            certificate=Certificate.load_pem(CertificatePEM(self.certificate)),
            private_key=RsaPrivateKey.load_pem(
                EncryptedPrivateKeyPEM(self.private_key), passphrase
            ),
        )

    def fingerprint(self, algorithm: HashAlgorithm) -> str:
        """return the fingerprint aka hash of the certificate as a hey string"""
        return (
            Certificate.load_pem(CertificatePEM(self.certificate))
            .fingerprint(algorithm)
            .hex(":")
            .upper()
        )


GlobalSettings = Mapping[str, Any]
