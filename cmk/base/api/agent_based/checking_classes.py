#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Classes used by the API for check plugins
"""
import enum
from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple, Optional, overload, Union

from cmk.utils import pnp_cleanup as quote_pnp_string
from cmk.utils.check_utils import unwrap_parameters
from cmk.utils.type_defs import EvalableFloat, MetricTuple, ParsedSectionName, RuleSetName

from cmk.checkers import PluginSuppliedLabel
from cmk.checkers.checking import CheckPluginName
from cmk.checkers.discovery import AutocheckEntry

from cmk.base.api.agent_based.type_defs import ParametersTypeAlias, RuleSetTypeName

# we may have 0/None for min/max for instance.
_OptionalPair = Optional[tuple[Optional[float], Optional[float]]]


class ServiceLabel(PluginSuppliedLabel):
    """Representing a service label in Checkmk

    This class creates a service label that can be passed to a 'Service' object.
    It can be used in the discovery function to create a new label like this:

        >>> my_label = ServiceLabel("my_key", "my_value")

    """


class Service(
    NamedTuple(  # pylint: disable=typing-namedtuple-call
        "_ServiceTuple",
        [
            ("item", Optional[str]),
            ("parameters", ParametersTypeAlias),
            ("labels", Sequence[ServiceLabel]),
        ],
    )
):
    """Class representing services that the discover function yields

    Args:
        item:       The item of the service
        parameters: The determined discovery parameters for this service
        labels:     A list of labels attached to this service

    Example:

        >>> my_drive_service = Service(
        ...    item="disc_name",
        ...    parameters={},
        ... )

    """

    def __new__(
        cls,
        *,
        item: str | None = None,
        parameters: ParametersTypeAlias | None = None,
        labels: Sequence[ServiceLabel] | None = None,
    ) -> "Service":
        return super().__new__(
            cls,
            item=cls._parse_item(item),
            parameters=cls._parse_parameters(parameters),
            labels=cls._parse_labels(labels),
        )

    @staticmethod
    def _parse_item(item: str | None) -> str | None:
        if item is None:
            return None
        if item and isinstance(item, str):
            return item
        raise TypeError(f"'item' must be a non empty string or ommited entirely, got {item!r}")

    @staticmethod
    def _parse_parameters(parameters: ParametersTypeAlias | None) -> ParametersTypeAlias:
        if parameters is None:
            return {}
        if isinstance(parameters, dict) and all(isinstance(k, str) for k in parameters):
            return parameters
        raise TypeError(f"'parameters' must be dict or None, got {parameters!r}")

    @staticmethod
    def _parse_labels(labels: Sequence[ServiceLabel] | None) -> Sequence[ServiceLabel]:
        if not labels:
            return []
        if isinstance(labels, list) and all(isinstance(l, ServiceLabel) for l in labels):
            return labels
        raise TypeError(f"'labels' must be list of ServiceLabels or None, got {labels!r}")

    def __repr__(self) -> str:
        args = ", ".join(
            f"{k}={v!r}"
            for k, v in (
                ("item", self.item),
                ("parameters", self.parameters),
                ("labels", self.labels),
            )
            if v
        )
        return f"{self.__class__.__name__}({args})"

    def as_autocheck_entry(self, name: CheckPluginName) -> AutocheckEntry:
        return AutocheckEntry(
            check_plugin_name=name,
            item=self.item,
            parameters=unwrap_parameters(self.parameters),
            service_labels={label.name: label.value for label in self.labels},
        )


@enum.unique
class State(enum.Enum):
    """States of check results"""

    # Don't use IntEnum to prevent "state.CRIT < state.UNKNOWN" from evaluating to True.
    OK = 0
    WARN = 1
    CRIT = 2
    UNKNOWN = 3

    def __int__(self) -> int:
        return int(self.value)

    @classmethod
    def best(cls, *args: Union["State", int]) -> "State":
        """Returns the best of all passed states

        You can pass an arbitrary number of arguments, and the return value will be
        the "best" of them, where

            `OK -> WARN -> UNKNOWN -> CRIT`

        Args:
            args: Any number of one of State.OK, State.WARN, State.CRIT, State.UNKNOWN

        Returns:
            The best of the input states, one of State.OK, State.WARN, State.CRIT, State.UNKNOWN.

        Examples:
            >>> State.best(State.OK, State.WARN, State.CRIT, State.UNKNOWN)
            <State.OK: 0>
            >>> State.best(0, 1, State.CRIT)
            <State.OK: 0>
        """
        _sorted = {
            cls.OK: 0,
            cls.WARN: 1,
            cls.UNKNOWN: 2,
            cls.CRIT: 3,
        }

        # we are nice and handle ints
        best = min(
            (cls(int(s)) for s in args),
            key=lambda s: _sorted[s],
        )

        return best

    @classmethod
    def worst(cls, *args: Union["State", int]) -> "State":
        """Returns the worst of all passed states.

        You can pass an arbitrary number of arguments, and the return value will be
        the "worst" of them, where

            `OK < WARN < UNKNOWN < CRIT`

        Args:
            args: Any number of one of State.OK, State.WARN, State.CRIT, State.UNKNOWN

        Returns:
            The worst of the input States, one of State.OK, State.WARN, State.CRIT, State.UNKNOWN.

        Examples:
            >>> State.worst(State.OK, State.WARN, State.CRIT, State.UNKNOWN)
            <State.CRIT: 2>
            >>> State.worst(0, 1, State.CRIT)
            <State.CRIT: 2>
        """
        if cls.CRIT in args or 2 in args:
            return cls.CRIT
        return cls(max(int(s) for s in args))


class Metric(
    NamedTuple(  # pylint: disable=typing-namedtuple-call
        "_MetricTuple",
        [
            ("name", str),
            ("value", EvalableFloat),
            ("levels", tuple[Optional[EvalableFloat], Optional[EvalableFloat]]),
            ("boundaries", tuple[Optional[EvalableFloat], Optional[EvalableFloat]]),
        ],
    )
):
    """Create a metric for a service

    Args:
        name:       The name of the metric.
        value:      The measured value.
        levels:     A pair of upper levels, ie. warn and crit. This information is only used
                    for visualization by the graphing system. It does not affect the service state.
        boundaries: Additional information on the value domain for the graphing system.

    If you create a Metric in this way, you may want to consider using :func:`check_levels`.

    Example:

        >>> my_metric = Metric("used_slots_percent", 23.0, levels=(80, 90), boundaries=(0, 100))

    """

    def __new__(
        cls,
        name: str,
        value: float,
        *,
        levels: _OptionalPair = None,
        boundaries: _OptionalPair = None,
    ) -> "Metric":
        cls._validate_name(name)

        if not isinstance(value, (int, float)):
            raise TypeError(f"value for metric must be float or int, got {value!r}")

        return super().__new__(
            cls,
            name=name,
            value=EvalableFloat(value),
            levels=cls._sanitize_optionals("levels", levels),
            boundaries=cls._sanitize_optionals("boundaries", boundaries),
        )

    @staticmethod
    def _validate_name(metric_name: str) -> None:
        if not metric_name:
            raise TypeError("metric name must not be empty")

        # this is not very elegant, but it ensures consistency to cmk.utils.misc.pnp_cleanup
        pnp_name = quote_pnp_string(metric_name)
        if metric_name != pnp_name:
            offenders = "".join(set(metric_name) - set(pnp_name))
            raise TypeError("invalid character(s) in metric name: %r" % offenders)

    @staticmethod
    def _sanitize_single_value(field: str, value: float | None) -> EvalableFloat | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return EvalableFloat(value)
        raise TypeError("%s values for metric must be float, int or None" % field)

    @classmethod
    def _sanitize_optionals(
        cls,
        field: str,
        values: _OptionalPair,
    ) -> tuple[EvalableFloat | None, EvalableFloat | None]:
        if values is None:
            return None, None

        if not isinstance(values, tuple) or len(values) != 2:
            raise TypeError("%r for metric must be a 2-tuple or None" % field)

        return (
            cls._sanitize_single_value(field, values[0]),
            cls._sanitize_single_value(field, values[1]),
        )

    def __repr__(self) -> str:
        levels = "" if self.levels == (None, None) else f", levels={self.levels!r}"
        boundaries = "" if self.boundaries == (None, None) else f", boundaries={self.boundaries!r}"
        return "{}({!r}, {!r}{}{})".format(
            self.__class__.__name__,
            self.name,
            self.value,
            levels,
            boundaries,
        )


class ResultTuple(NamedTuple):
    state: State
    summary: str
    details: str


class Result(ResultTuple):
    """A result to be yielded by check functions

    This is the class responsible for creating service output and setting the state of a service.

    Args:
        state:   The resulting state of the service.
        summary: The text to be displayed in the services *summary* view.
        notice:  A text that will only be shown in the *summary* if `state` is not OK.
        details: The alternative text that will be displayed in the details view. Defaults to the
                 value of `summary` or `notice`.

    Note:
        You must specify *exactly* one of the arguments ``summary`` and ``notice``!

    When yielding more than one result, Checkmk will not only aggregate the texts, but also
    compute the worst state for the service and highlight the individual non-OK states in
    the output.
    You should always match the state to the output, and yield subresults:

        >>> def my_check_function() -> None:
        ...     # the back end will comput the worst overall state:
        ...     yield Result(state=State.CRIT, summary="All the foos are broken")
        ...     yield Result(state=State.OK, summary="All the bars are fine")
        >>>
        >>> # run function to make sure we have a working example
        >>> _ = list(my_check_function())

    The ``notice`` keyword has the special property that it will only be displayed if the
    corresponding state is not OK. Otherwise we assume it is sufficient if the information
    is available in the details view:

        >>> def my_check_function() -> None:
        ...     count = 23
        ...     yield Result(
        ...         state=State.WARN if count <= 42 else State.OK,
        ...         notice=f"Things: {count}",  # only appear in summary if count drops below 43
        ...         details=f"We currently have this many things: {count}",
        ...     )
        >>>
        >>> # run function to make sure we have a working example
        >>> _ = list(my_check_function())

    If you find yourself computing the state by comparing a metric to some thresholds, you
    probably should be using :func:`check_levels`!

    """

    @overload
    def __new__(
        cls,
        *,
        state: State,
        summary: str,
        details: str | None = None,
    ) -> "Result":
        pass

    @overload
    def __new__(
        cls,
        *,
        state: State,
        notice: str,
        details: str | None = None,
    ) -> "Result":
        pass

    def __new__(  # type: ignore[no-untyped-def]
        cls,
        **kwargs,
    ) -> "Result":
        state, summary, details = _create_result_fields(**kwargs)
        return super().__new__(
            cls,
            state=state,
            summary=summary,
            details=details,
        )

    def __repr__(self) -> str:
        if not self.summary:
            text_args = f"notice={self.details!r}"
        elif self.summary != self.details:
            text_args = f"summary={self.summary!r}, details={self.details!r}"
        else:
            text_args = f"summary={self.summary!r}"
        return f"{self.__class__.__name__}(state={self.state!r}, {text_args})"


def _create_result_fields(
    *,
    state: State,
    summary: str | None = None,
    notice: str | None = None,
    details: str | None = None,
) -> tuple[State, str, str]:
    if not isinstance(state, State):
        raise TypeError(f"'state' must be a checkmk State constant, got {state}")

    for var, name in (
        (summary, "summary"),
        (notice, "notice"),
        (details, "details"),
    ):
        if var is None:
            continue
        if not isinstance(var, str):
            raise TypeError(f"'{name}' must be non-empty str or None, got {var}")
        if not var:
            raise ValueError(f"'{name}' must be non-empty str or None, got {var}")

    if summary:
        if notice:
            raise TypeError("'summary' and 'notice' are mutually exclusive arguments")
        if "\n" in summary:
            raise ValueError("'\\n' not allowed in 'summary'")
        return state, summary, details or summary

    if notice:
        summary = notice.replace("\n", ", ") if state != State.OK else ""
        return state, summary, details or notice

    raise TypeError("at least 'summary' or 'notice' is required")


class IgnoreResultsError(RuntimeError):
    """Raising an `IgnoreResultsError` from within a check function makes the service go stale.

    Example:

        >>> def check_db_table(item, section):
        ...     if item not in section:
        ...         # avoid a lot of UNKNOWN services:
        ...         raise IgnoreResultsError("Login to database failed")
        ...     # do your work here
        >>>

    """


class IgnoreResults:
    """A result to make the service go stale, but carry on with the check function

    Yielding a result of type `IgnoreResults` will have a similar effect as raising
    an :class:`.IgnoreResultsError`, with the difference that the execution of the
    check funtion will not be interrupted.

    .. code-block:: python

        yield IgnoreResults("Good luck next time!")
        return

    is equivalent to

    .. code-block:: python

        raise IgnoreResultsError("Good luck next time!")

    This is useful for instance if you want to initialize all counters, before
    returning.
    """

    def __init__(self, value: str = "currently no results") -> None:
        self._value = value

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._value!r})"

    def __str__(self) -> str:
        return self._value if isinstance(self._value, str) else repr(self._value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IgnoreResults) and self._value == other._value


CheckResult = Iterable[Union[IgnoreResults, Metric, Result]]
CheckFunction = Callable[..., CheckResult]
DiscoveryResult = Iterable[Service]
DiscoveryFunction = Callable[..., DiscoveryResult]


def consume_check_results(
    # TODO(ml):  We should limit the type to `CheckResult` but that leads to
    # layering violations.  We could also go with dependency inversion or some
    # other slightly higher abstraction.  The code here is really concrete.
    # Investigate and find a solution later.
    subresults: Iterable[object],
) -> tuple[Sequence[MetricTuple], Sequence[Result]]:
    """Impedance matching between the Check API and the Check Engine."""
    ignore_results: list[IgnoreResults] = []
    results: list[Result] = []
    perfdata: list[MetricTuple] = []
    for subr in subresults:
        if isinstance(subr, IgnoreResults):
            ignore_results.append(subr)
        elif isinstance(subr, Metric):
            perfdata.append((subr.name, subr.value) + subr.levels + subr.boundaries)
        elif isinstance(subr, Result):
            results.append(subr)
        else:
            raise TypeError(subr)

    # Consume *all* check results, and *then* raise, if we encountered
    # an IgnoreResults instance.
    if ignore_results:
        raise IgnoreResultsError(str(ignore_results[-1]))

    return perfdata, results


class CheckPlugin(NamedTuple):
    name: CheckPluginName
    sections: list[ParsedSectionName]
    service_name: str
    discovery_function: DiscoveryFunction
    discovery_default_parameters: ParametersTypeAlias | None
    discovery_ruleset_name: RuleSetName | None
    discovery_ruleset_type: RuleSetTypeName
    check_function: CheckFunction
    check_default_parameters: ParametersTypeAlias | None
    check_ruleset_name: RuleSetName | None
    cluster_check_function: CheckFunction | None
    module: str | None  # not available for auto migrated plugins.
