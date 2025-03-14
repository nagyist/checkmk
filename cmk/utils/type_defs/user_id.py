#!/usr/bin/env python3
# Copyright (C) 2022 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from __future__ import annotations

import re

__all__ = ["UserId"]


class UserId(str):
    """
    A Checkmk user ID

    UserIds must comply to a restricted set of allowed characters (see UserId.validate).

    UserIds must either be compatible with all protocols and components we interface with, or we
    must ensure proper encoding whenever UserIds leave Checkmk.
    The following *not exhaustive* list provides a starting point for the interfaces we need to
    keep in mind:

        * HTML rendering in numerous instances in the GUI
        * URLs and links
            * usually these go through `makeuri_contextless()` or `urlencode()` at the call site
        * GUI messages (user to user) (`cmk.gui.message`)
        * the value of the auth cookie
        * the RestAPI
            * responses
            * bearer token (not base64 encoded!)
        * various error messages (displayed in the GUI)

        * file paths like `var/check_mk/web/<UserId>`
            * sometimes those check for `/` and `..` but currently there's no mechanism make sure
        * Visuals like Graphs and Reports that serialize UserIds into .mk files
        * persisted UserSpecs and Sessions (.mk files)
        * cmk.gui.userdb and htpasswd (LDAP is only ingress of users)
        * various logs, including Audit Log

        * Microcore
        * Nagios core
        * livestatus queries and commands
            * livestatus.py (the validation regex is currently duplicated here!)
            * cmk.gui.livestatus_utils (acknowledgements, comments, downtimes, ...)
        * event console for contacts, notifications, possibly more
        * agent registration and background jobs

        * ntop connector
        * Grafana connector
        * X509 certificates and the `Key` object (`cmk.gui.key_mgmt`)
    """

    # Note: livestatus.py duplicates the regex to validate incoming UserIds!
    USER_ID_REGEX = re.compile(r"^[\w$][-@.\w$]*$", re.UNICODE)

    @classmethod
    def validate(cls, text: str) -> None:
        """Check if it is a valid UserId

        UserIds are used in a variety of contexts, including HTML, file paths and other external
        systems. Currently we have no means of ensuring proper output encoding wherever UserIds
        are used. Thus, we strictly limit the characters that can be used in a UserId.

        See class docstring for an incomplete list of places where UserIds are processed.

        Examples:

            The empty UserId is allowed for historical reasons (see `UserId.builtin`).

                >>> UserId.validate("")

            ASCII letters, digits, and selected special characters are allowed.

                >>> UserId.validate("cmkadmin")
                >>> UserId.validate("$cmk_@dmün.1")

            Anything considered a letter in Unicode and the dollar sign is allowed.

                >>> UserId.validate("cmkadmin")
                >>> UserId.validate("$cmkädmin")
                >>> UserId.validate("ↄ𝒽ѥ𝕔𖹬-艋く")

            Special characters other than '$_-@.' are not allowed (see `USER_ID_REGEX`).

                >>> UserId.validate("foo/../")
                Traceback (most recent call last):
                ...
                ValueError: Invalid username: 'foo/../'

                >>> UserId.validate("%2F")
                Traceback (most recent call last):
                ...
                ValueError: Invalid username: '%2F'

            Some special characters are not allowed at the start.

                >>> UserId.validate(".")
                Traceback (most recent call last):
                ...
                ValueError: Invalid username: '.'

                >>> UserId.validate("@example.com")
                Traceback (most recent call last):
                ...
                ValueError: Invalid username: '@example.com'

        """
        if text == "":
            # see UserId.builtin
            return
        if not cls.USER_ID_REGEX.match(text):
            raise ValueError(f"Invalid username: {text!r}")

    @classmethod
    def builtin(cls) -> UserId:
        """A special UserId signifying something is owned or created not by a real user but shipped
        as a built in functionality.
        This is mostly used in cmk.gui.visuals.
        Note that, unfortunately, the UserId "" will sometimes also be constructed via regular
        initialization, so this method is not the only source for them.
        Moreover, be aware that it is very possible that some parts of the code use the UserId ""
        with a different meaning.
        """
        return UserId("")

    def __new__(cls, text: str) -> UserId:
        """Construct a new UserId object

        Raises:
            - ValueError: whenever the given text contains special characters. See `validate`.
        """
        cls.validate(text)
        return super().__new__(cls, text)
