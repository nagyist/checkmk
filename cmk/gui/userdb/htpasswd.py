#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from pathlib import Path

import cmk.utils.paths
from cmk.utils.crypto import password_hashing
from cmk.utils.crypto.password import Password, PasswordHash
from cmk.utils.crypto.secrets import AutomationUserSecret
from cmk.utils.store.htpasswd import Htpasswd
from cmk.utils.type_defs import UserId

from cmk.gui.exceptions import MKUserError
from cmk.gui.i18n import _
from cmk.gui.plugins.userdb.utils import (
    CheckCredentialsResult,
    ConnectorType,
    user_connector_registry,
    UserConnector,
)
from cmk.gui.type_defs import UserSpec


# Checkmk supports different authentication frontends for verifying the
# local credentials:
#
# a) basic authentication
# b) GUI form + cookie based authentication
#
# The default is b). This option is toggled with the "omd config" option
# MULTISITE_COOKIE_AUTH. In case the basic authentication is chosen it
# is only possible use hashing algorithms that are supported by the
# web server which performs the authentication.
#
# See:
# - https://httpd.apache.org/docs/2.4/misc/password_encryptions.html
# - https://passlib.readthedocs.io/en/stable/lib/passlib.apache.html
#
def hash_password(password: Password) -> PasswordHash:
    """Hash a password

    Invalid inputs raise MKUserError.
    """
    try:
        return password_hashing.hash_password(password)

    except password_hashing.PasswordTooLongError:
        raise MKUserError(
            None, "Passwords over 72 bytes would be truncated and are therefore not allowed!"
        )
    except ValueError:
        raise MKUserError(None, "Password could not be hashed.")


@user_connector_registry.register
class HtpasswdUserConnector(UserConnector):
    @classmethod
    def type(cls) -> str:
        return ConnectorType.HTPASSWD

    @property
    def id(self) -> str:
        return "htpasswd"

    @classmethod
    def title(cls) -> str:
        return _("Apache Local Password File (htpasswd)")

    @classmethod
    def short_title(cls) -> str:
        return _("htpasswd")

    def __init__(self, cfg) -> None:  # type: ignore[no-untyped-def]
        super().__init__(cfg)
        self._htpasswd = Htpasswd(Path(cmk.utils.paths.htpasswd_file))

    #
    # USERDB API METHODS
    #

    def is_enabled(self) -> bool:
        return True

    def check_credentials(self, user_id: UserId, password: Password) -> CheckCredentialsResult:
        if not (pw_hash := self._htpasswd.get_hash(user_id)):
            return None  # not user in htpasswd, skip so other connectors can try

        if self._is_automation_user(user_id):
            raise MKUserError(None, _("Automation user rejected"))

        if pw_hash.startswith("!"):
            raise MKUserError(None, _("User is locked"))

        try:
            password_hashing.verify(password, pw_hash)
        except (password_hashing.PasswordInvalidError, ValueError):
            return False
        return user_id

    def _is_automation_user(self, user_id: UserId) -> bool:
        return AutomationUserSecret(user_id).exists()

    def save_users(self, users: dict[UserId, UserSpec]) -> None:
        # Apache htpasswd. We only store passwords here. During
        # loading we created entries for all admin users we know. Other
        # users from htpasswd are lost. If you start managing users with
        # Setup, you should continue to do so or stop doing to for ever...
        # Locked accounts get a '!' before their password. This disable it.
        entries = {}

        for uid, user in users.items():
            # only process users which are handled by htpasswd connector
            if user.get("connector", "htpasswd") != "htpasswd":
                continue

            if user.get("password"):
                entries[uid] = PasswordHash(
                    "{}{}".format("!" if user.get("locked", False) else "", user["password"])
                )

        self._htpasswd.save_all(entries)
