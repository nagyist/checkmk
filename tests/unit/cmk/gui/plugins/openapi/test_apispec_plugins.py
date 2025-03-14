#!/usr/bin/env python3
# Copyright (C) 2020 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
# fmt: off

import pytest
from apispec import APISpec
from marshmallow import post_load, Schema, ValidationError
from marshmallow.base import SchemaABC

from cmk.gui.fields.base import ValueTypedDictSchema
from cmk.gui.fields.openapi import CheckmkMarshmallowPlugin

from cmk import fields


class Movie:
    def __init__(self, **kw) -> None: # type: ignore[no-untyped-def]
        for key, value in kw.items():
            setattr(self, key, value)
        self.kw = kw
        self.value = tuple(sorted(kw.items()))

    def __repr__(self) -> str:
        return f"<Movie {self.kw!r}>"

    def __lt__(self, other):
        return self.kw["year"] > other.kw["year"]

    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.value == other.value


MOVIES = {
    'Solyaris': {
        'title': 'Solyaris',
        'director': 'Andrei Tarkovsky',
        'year': 1972
    },
    'Stalker': {
        'title': 'Stalker',
        'director': 'Andrei Tarkovsky',
        'year': 1979
    },
}

BROKEN_MOVIE = {
    'Plan 9 from Outer Space': {
        'title': None,
        'director': None,
        'year': 1957,
    },
}

EXPECTED_MOVIES = {
    'Solyaris': Movie(**{
        'title': 'Solyaris',
        'director': 'Andrei Tarkovsky',
        'year': 1972
    }),
    'Stalker': Movie(**{
        'title': 'Stalker',
        'director': 'Andrei Tarkovsky',
        'year': 1979
    }),
}


class MovieSchema(Schema):
    title = fields.String(required=True)
    director = fields.String(required=True)
    year = fields.Integer(required=True)

    @post_load
    def make_movie(self, data, **kwargs):
        return Movie(**data)


class MovieDictSchema(ValueTypedDictSchema):
    value_type = MovieSchema


class CustomTagDictSchema(ValueTypedDictSchema):
    value_type = ValueTypedDictSchema.field(fields.String(
        description="Tag value here",
        pattern="foo|bar",
        required=True,
    ))


class IntegerDictSchema(ValueTypedDictSchema):
    value_type = ValueTypedDictSchema.field(fields.Integer())


class EmailSchema(ValueTypedDictSchema):
    value_type = ValueTypedDictSchema.field(fields.Email())


@pytest.fixture(name="spec", scope='function')
def spec_fixture():
    return APISpec(title='Sensationalist Witty Title',
                   version='1.0.0',
                   openapi_version='3.0.0',
                   plugins=[
                       CheckmkMarshmallowPlugin(),
                   ])


def test_apispec_plugin_string_to_schema_dict(spec) -> None: # type: ignore[no-untyped-def]
    # Schema suffix of schemas gets stripped by library
    spec.components.schema('MovieDict', schema=MovieDictSchema)

    schemas = spec.to_dict()['components']['schemas']
    assert schemas['MovieDict'] == {
        'type': 'object',
        'additionalProperties': {
            '$ref': '#/components/schemas/Movie'
        }
    }


def test_apispec_plugin_string_to_string_dict(spec) -> None: # type: ignore[no-untyped-def]
    # Schema suffix of schemas gets stripped by library
    spec.components.schema('CustomTagDict', schema=CustomTagDictSchema)
    schemas = spec.to_dict()['components']['schemas']
    assert schemas['CustomTagDict'] == {
        'type': 'object',
        'additionalProperties': {
            'type': 'string',
            'description': 'Tag value here',
            'pattern': 'foo|bar',
            'required': True,
        }
    }


def test_apispec_plugin_parameters(spec) -> None: # type: ignore[no-untyped-def]
    # Different code paths are executed here. We need to make sure our plugin handles this.
    spec.components.parameter('var', 'path', {'description': "Some path variable"})


@pytest.mark.parametrize(
    ['schema_class', 'in_data', 'expected_result'],
    [
        (MovieDictSchema, MOVIES, EXPECTED_MOVIES),
        (IntegerDictSchema, {'foo': 1}, {'foo': 1}),
        (EmailSchema, {'bob': 'bob@example.com'}, {'bob': 'bob@example.com'}),
    ],
)
def test_typed_dictionary_success(schema_class: type[SchemaABC], in_data, expected_result) -> None: # type: ignore[no-untyped-def]
    schema = schema_class()
    result = schema.load(in_data)
    assert result == expected_result
    assert schema.dump(result) == in_data


@pytest.mark.parametrize(['schema_class', 'in_data'], [
    (MovieDictSchema, BROKEN_MOVIE),
    (IntegerDictSchema, {'bar': 'eins'}),
    (EmailSchema, {'hans': 'foo'}),
])
def test_typed_dictionary_failed_validation(schema_class, in_data) -> None: # type: ignore[no-untyped-def]
    schema = schema_class()
    with pytest.raises(ValidationError):
        schema.load(in_data)
