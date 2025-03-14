#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
"""Classes used by the API for check plugins
"""
import string
from collections.abc import Callable, Iterable, Mapping
from typing import get_args, NamedTuple, NoReturn, Union

from cmk.utils.structured_data import StructuredDataNode
from cmk.utils.type_defs import ParsedSectionName, RuleSetName

from cmk.checkers.inventory import InventoryPluginName

from cmk.base.api.agent_based.type_defs import ParametersTypeAlias

_ATTR_DICT_KEY_TYPE = str

AttrDict = Mapping[_ATTR_DICT_KEY_TYPE, Union[None, int, float, str]]

# get allowed value types back as a tuple to guarantee consistency
_ATTR_DICT_VAL_TYPES = get_args(get_args(AttrDict)[1])

_VALID_CHARACTERS = set(string.ascii_letters + string.digits + "_-")


def _parse_valid_path(path: list[str]) -> list[str]:
    if not (path and isinstance(path, list) and all(isinstance(s, str) for s in path)):
        raise TypeError("'path' arg expected a non empty List[str], got %r" % path)
    invalid_chars = set("".join(path)) - _VALID_CHARACTERS
    if invalid_chars:
        raise ValueError("invalid characters in path: %r" % "".join(invalid_chars))
    return path


def _raise_invalid_attr_dict(kwarg_name: str, dict_: AttrDict) -> NoReturn:
    value_types = ", ".join(t.__name__ for t in _ATTR_DICT_VAL_TYPES)
    raise TypeError(
        f"{kwarg_name} must be a dict with keys of type {_ATTR_DICT_KEY_TYPE.__name__}"
        f" and values of type {value_types}. Got {dict_!r}"
    )


def _parse_valid_dict(kwarg_name: str, dict_: AttrDict | None) -> AttrDict:
    if dict_ is None:
        return {}
    if not isinstance(dict_, dict):
        _raise_invalid_attr_dict(kwarg_name, dict_)
    if not all(
        isinstance(k, _ATTR_DICT_KEY_TYPE) and isinstance(v, _ATTR_DICT_VAL_TYPES)
        for k, v in dict_.items()
    ):
        _raise_invalid_attr_dict(kwarg_name, dict_)
    return dict_


class Attributes(
    NamedTuple(  # pylint: disable=typing-namedtuple-call
        "_AttributesTuple",
        [
            ("path", list[str]),
            ("inventory_attributes", AttrDict),
            ("status_attributes", AttrDict),
        ],
    )
):
    """Attributes to be written at a node in the HW/SW inventory"""

    def __new__(
        cls,
        *,
        path: list[str],
        inventory_attributes: AttrDict | None = None,
        status_attributes: AttrDict | None = None,
    ) -> "Attributes":
        """

        Example:

            >>> _ = Attributes(
            ...     path = ["os", "vendor"],
            ...     inventory_attributes = {
            ...         "name" : "Micki$osft",
            ...         "date" : "1920",
            ...     },
            ...     status_attributes = {
            ...         "uptime" : 0,
            ...     },
            ... )

        """
        inventory_attributes = _parse_valid_dict("inventory_attributes", inventory_attributes)
        status_attributes = _parse_valid_dict("status_attributes", status_attributes)
        common_keys = set(inventory_attributes) & set(status_attributes)
        if common_keys:
            raise ValueError(
                "keys must be either of type 'status' or 'inventory': %s" % ", ".join(common_keys)
            )

        return super().__new__(
            cls,
            path=_parse_valid_path(path),
            inventory_attributes=inventory_attributes,
            status_attributes=status_attributes,
        )

    def populate_inventory_tree(self, tree: StructuredDataNode) -> None:
        if not self.inventory_attributes:
            return

        node = tree.setdefault_node(tuple(self.path))
        node.attributes.add_pairs(self.inventory_attributes)

    def populate_status_data_tree(self, tree: StructuredDataNode) -> None:
        if not self.status_attributes:
            return

        node = tree.setdefault_node(tuple(self.path))
        node.attributes.add_pairs(self.status_attributes)


class TableRow(
    NamedTuple(  # pylint: disable=typing-namedtuple-call
        "_TableRowTuple",
        [
            ("path", list[str]),
            ("key_columns", AttrDict),
            ("inventory_columns", AttrDict),
            ("status_columns", AttrDict),
        ],
    )
):
    """TableRow to be written into a Table at a node in the HW/SW inventory"""

    def __new__(
        cls,
        *,
        path: list[str],
        key_columns: AttrDict,
        inventory_columns: AttrDict | None = None,
        status_columns: AttrDict | None = None,
    ) -> "TableRow":
        """

        Example:

            >>> _ = TableRow(
            ...     path = ["software", "applications", "oracle", "instance"],
            ...     key_columns = {
            ...         "sid" : "DECAF-FOOBAR",
            ...     },
            ...     inventory_columns = {
            ...         "version": "23.42",
            ...         "logmode": "debug",
            ...     },
            ...     status_columns = {
            ...         "db_uptime": 123456,
            ...     },
            ... )

        """
        if not (isinstance(key_columns, dict) and key_columns):
            raise TypeError(
                f"TableRows 'key_columns' expected non empty Dict[str, Any], got {key_columns!r}"
            )

        key_columns = _parse_valid_dict("key_columns", key_columns)
        inventory_columns = _parse_valid_dict("inventory_columns", inventory_columns)
        status_columns = _parse_valid_dict("status_columns", status_columns)

        for key in set(inventory_columns) | set(status_columns):
            if ((key in key_columns) + (key in inventory_columns) + (key in status_columns)) > 1:
                raise ValueError("conflicting key: %s" % key)

        return super().__new__(
            cls,
            path=_parse_valid_path(path),
            key_columns=key_columns,
            inventory_columns=inventory_columns,
            status_columns=status_columns,
        )

    def populate_inventory_tree(self, tree: StructuredDataNode) -> None:
        # No guard: always set key columns.
        node = tree.setdefault_node(tuple(self.path))
        node.table.add_key_columns(sorted(self.key_columns))
        node.table.add_rows([{**self.key_columns, **self.inventory_columns}])

    def populate_status_data_tree(self, tree: StructuredDataNode) -> None:
        if not self.status_columns:
            return

        node = tree.setdefault_node(tuple(self.path))
        node.table.add_key_columns(sorted(self.key_columns))
        node.table.add_rows([{**self.key_columns, **self.status_columns}])


InventoryResult = Iterable[Union[Attributes, TableRow]]
InventoryFunction = Callable[..., InventoryResult]


class InventoryPlugin(NamedTuple):
    name: InventoryPluginName
    sections: list[ParsedSectionName]
    inventory_function: InventoryFunction
    inventory_default_parameters: ParametersTypeAlias
    inventory_ruleset_name: RuleSetName | None
    module: str
