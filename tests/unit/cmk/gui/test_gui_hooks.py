#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
import datetime
from collections.abc import Generator

import pytest
from flask import Flask
from pytest_mock import MockerFixture

from tests.unit.cmk.gui.conftest import WebTestAppForCMK

from cmk.utils.exceptions import MKGeneralException

import cmk.gui.hooks as hooks
from cmk.gui.pages import Page, page_registry


@pytest.fixture(autouse=True)
def reset_hooks() -> Generator[None, None, None]:
    old_hooks = hooks.hooks
    try:
        hooks.hooks = {}
        yield
    finally:
        hooks.hooks = old_hooks


def test_flask_request_memoize(flask_app: Flask) -> None:
    @hooks.request_memoize()
    def cached_function():
        return datetime.datetime.now()

    assert len(hooks.hooks) > 0

    with flask_app.test_client(use_cookies=True) as client:
        prev = cached_function()

        # Only Checkmk and REST API Blueprint requests trigger the cache eviction.
        resp = client.get("/")
        assert resp.status_code == 404

        assert prev == cached_function()

        # After another request, the cache is evicted.
        resp = client.get("/NO_SITE/check_mk/login.py")
        assert resp.status_code == 200
        assert prev != cached_function()


def test_request_memoize() -> None:
    @hooks.request_memoize()
    def blah(a=[]):  # pylint: disable=dangerous-default-value
        a.append(1)
        return a

    assert blah() == [1]
    assert blah() == [1]

    hooks.call("request-end")

    assert blah() == [1, 1]


def test_request_memoize_request_integration(
    logged_in_wsgi_app: WebTestAppForCMK, mocker: MockerFixture
) -> None:
    mock = mocker.MagicMock()

    @hooks.request_memoize()
    def memoized():
        return mock()

    @page_registry.register_page("my_page")
    class PageClass(Page):  # pylint: disable=unused-variable
        def page(self) -> None:
            mock.return_value = 1
            assert memoized() == 1

            # Test that it gives the memoized value instead of the new mock value
            mock.return_value = 2
            assert memoized() == 1

    # Try a first request. Memoization within this request is tested in page() above.
    logged_in_wsgi_app.get("/NO_SITE/check_mk/my_page.py", status=200)

    # After the request has ended we get the new value
    mock.return_value = 2
    assert memoized() == 2
    # But there is no reset triggered outside of the request. We do it manually here.
    hooks.call("request-end")

    # And now try a second request
    mock.return_value = 1
    logged_in_wsgi_app.get("/NO_SITE/check_mk/my_page.py", status=200)

    page_registry.unregister("my_page")


def test_request_memoize_unregister() -> None:
    # Make sure request-start hooks are still called, after plugin hooks are
    # unregistered. In previous versions unregister_plugin_hooks also
    # unregistered hooks used by memoize.

    @hooks.request_memoize()
    def blah(a: list[int] = []) -> list[int]:  # pylint: disable=dangerous-default-value
        a.append(1)
        return a

    assert blah() == [1]
    assert blah() == [1]

    hooks.call("request-end")

    assert blah() == [1, 1]

    hooks.unregister_plugin_hooks()
    hooks.call("request-end")

    assert blah() == [1, 1, 1]
    assert blah() == [1, 1, 1]


def test_hook_registration() -> None:
    assert hooks.hooks == {}

    # pre 1.6 API
    hooks.register("bla", lambda: True)
    assert hooks.get("bla")[0].is_builtin is False

    hooks.register_builtin("blub", lambda: True)
    hooks.register_from_plugin("blub", lambda: False)
    assert hooks.get("blub")[0].is_builtin is True
    assert hooks.get("blub")[1].is_builtin is False

    assert hooks.registered("bla") is True
    assert hooks.registered("blub") is True
    assert hooks.registered("bli") is False

    assert len(hooks.get("bla")) == 1
    assert len(hooks.get("blub")) == 2
    assert len(hooks.get("bli")) == 0


def test_call(mocker: MockerFixture) -> None:
    hook1_mock = mocker.Mock()
    hook2_mock = mocker.Mock()
    hooks.register("bla", hook1_mock)
    hooks.register("blub", hook2_mock)

    hooks.call("bla")
    hook1_mock.assert_called_once()
    hook2_mock.assert_not_called()

    hooks.call("blub")
    hook1_mock.assert_called_once()
    hook2_mock.assert_called_once()


@pytest.mark.usefixtures("request_context")
def test_call_exception_handling(mocker: MockerFixture) -> None:
    hooks.register_builtin("bli", lambda: 1.0 / 0.0)
    hook3_mock = mocker.Mock()
    hooks.register("bli", hook3_mock)
    with pytest.raises(MKGeneralException, match="float division by zero"):
        hooks.call("bli")
    hook3_mock.assert_not_called()


def test_builtin_vs_plugin_hooks() -> None:
    hooks.register_builtin("bla", lambda: True)
    assert hooks.registered("bla") is True

    hooks.register_from_plugin("blub", lambda: True)
    assert hooks.registered("blub") is True

    hooks.load_plugins()

    assert hooks.registered("bla") is True
    assert hooks.registered("blub") is False
