#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from collections.abc import Iterable, Sequence

from cmk.gui.type_defs import ColumnSpec, PainterName, PainterParameters, SorterName, SorterSpec

from .painter.v0.base import painter_exists, painter_registry
from .sorter import ParameterizedSorter, sorter_registry


def compute_sort_url_parameter(
    painter_name: PainterName,
    painter_parameters: PainterParameters | None,
    join_key: str | None,
    group_painters: Sequence[ColumnSpec],
    config_sorters: Sequence[SorterSpec],
    user_sorters: Sequence[SorterSpec],
) -> str:
    """Computes the `sort` URL parameter value for a column header

    The following sorters need to be handled in this order:

    1. group by sorter (needed in grouped views)
    2. user defined sorters (url sorter)
    3. configured view sorters
    """
    sorters = []

    group_sort, user_sort, view_sort = _get_separated_sorters(
        group_painters, config_sorters, list(user_sorters)
    )

    sorters = group_sort + user_sort + view_sort

    # Now apply the sorter of the current column:
    # - Negate/Disable when at first position
    # - Move to the first position when already in sorters
    # - Add in the front of the user sorters when not set
    sorter_name = _get_sorter_name_of_painter(painter_name)
    if sorter_name is None:
        # Do not change anything in case there is no sorter for the current column
        return _encode_sorter_url(sorters)

    if sorter_name not in sorter_registry:
        return _encode_sorter_url(sorters)

    sorter: SorterName | tuple[SorterName, PainterParameters]
    if issubclass(sorter_registry[sorter_name], ParameterizedSorter):
        assert painter_parameters is not None
        sorter = (painter_name, painter_parameters)
    else:
        sorter = sorter_name

    this_asc_sorter = SorterSpec(sorter=sorter, negate=False, join_key=join_key)
    this_desc_sorter = SorterSpec(sorter=sorter, negate=True, join_key=join_key)

    if user_sort and this_asc_sorter == user_sort[0]:
        # Second click: Change from asc to desc order
        sorters[sorters.index(this_asc_sorter)] = this_desc_sorter

    elif user_sort and this_desc_sorter == user_sort[0]:
        # Third click: Remove this sorter
        sorters.remove(this_desc_sorter)

    else:
        # First click: add this sorter as primary user sorter
        # Maybe the sorter is already in the user sorters or view sorters, remove it
        for s in [user_sort, view_sort]:
            if this_asc_sorter in s:
                s.remove(this_asc_sorter)
            if this_desc_sorter in s:
                s.remove(this_desc_sorter)
        # Now add the sorter as primary user sorter
        sorters = group_sort + [this_asc_sorter] + user_sort + view_sort

    return _encode_sorter_url(sorters)


def _get_separated_sorters(
    group_painters: Sequence[ColumnSpec],
    config_sorters: Sequence[SorterSpec],
    user_sorters: list[SorterSpec],
) -> tuple[list[SorterSpec], list[SorterSpec], list[SorterSpec]]:
    group_sort = _get_group_sorters(group_painters)
    view_sort = [s for s in config_sorters if not any(s.sorter == gs.sorter for gs in group_sort)]
    user_sort = user_sorters

    _substract_sorters(user_sort, group_sort)
    _substract_sorters(view_sort, user_sort)

    return group_sort, user_sort, view_sort


def _get_group_sorters(group_painters: Sequence[ColumnSpec]) -> list[SorterSpec]:
    group_sort: list[SorterSpec] = []
    for p in group_painters:
        if not painter_exists(p):
            continue
        sorter_name = _get_sorter_name_of_painter(p)
        if sorter_name is None:
            continue

        group_sort.append(SorterSpec(sorter_name, negate=False, join_key=None))
    return group_sort


def _get_sorter_name_of_painter(
    painter_name_or_spec: PainterName | ColumnSpec,
) -> SorterName | None:
    painter_name = (
        painter_name_or_spec.name
        if isinstance(painter_name_or_spec, ColumnSpec)
        else painter_name_or_spec
    )
    painter = painter_registry[painter_name]()
    if painter.sorter:
        return painter.sorter

    if painter_name in sorter_registry:
        return painter_name

    return None


def _substract_sorters(base: list[SorterSpec], remove: list[SorterSpec]) -> None:
    for s in remove:
        negated_sorter = SorterSpec(sorter=s.sorter, negate=not s.negate, join_key=None)

        if s in base:
            base.remove(s)
        elif negated_sorter in base:
            base.remove(negated_sorter)


def _encode_sorter_url(sorters: Iterable[SorterSpec]) -> str:
    p: list[str] = []
    for s in sorters:
        sorter_name = s.sorter
        if isinstance(sorter_name, tuple):
            sorter_name, params = sorter_name
            ident = params.get("ident", params.get("uuid", ""))
            if not ident:
                raise ValueError(f"Parameterized sorter without ident: {s!r}")
            sorter_name = f"{sorter_name}:{ident}"
        url = ("-" if s.negate else "") + sorter_name
        if s.join_key:
            url += "~" + s.join_key
        p.append(url)

    return ",".join(p)
