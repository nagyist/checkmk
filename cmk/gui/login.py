#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
from __future__ import annotations

import contextlib
import http.client
from collections.abc import Iterator
from datetime import datetime

import cmk.utils.paths
import cmk.utils.version as cmk_version
from cmk.utils.crypto.password import Password
from cmk.utils.licensing.handler import LicenseStateError, RemainingTrialTime
from cmk.utils.licensing.registry import get_remaining_trial_time
from cmk.utils.site import omd_site, url_prefix
from cmk.utils.type_defs import UserId

import cmk.gui.mobile
import cmk.gui.userdb as userdb
from cmk.gui.auth import is_site_login
from cmk.gui.breadcrumb import Breadcrumb
from cmk.gui.config import active_config
from cmk.gui.exceptions import FinalizeRequest, HTTPRedirect, MKAuthException, MKUserError
from cmk.gui.htmllib.generator import HTMLWriter
from cmk.gui.htmllib.header import make_header
from cmk.gui.htmllib.html import html
from cmk.gui.http import request, response
from cmk.gui.i18n import _
from cmk.gui.logged_in import LoggedInNobody, LoggedInUser, user
from cmk.gui.main import get_page_heading
from cmk.gui.pages import Page, page_registry
from cmk.gui.plugins.userdb.utils import active_connections_by_type
from cmk.gui.session import session, UserContext
from cmk.gui.userdb.session import auth_cookie_name
from cmk.gui.utils.escaping import escape_to_html
from cmk.gui.utils.html import HTML
from cmk.gui.utils.login import show_saml2_login, show_user_errors
from cmk.gui.utils.mobile import is_mobile
from cmk.gui.utils.theme import theme
from cmk.gui.utils.transaction_manager import transactions
from cmk.gui.utils.urls import makeuri, requested_file_name, urlencode
from cmk.gui.utils.user_errors import user_errors


@contextlib.contextmanager
def authenticate() -> Iterator[bool]:
    """Perform the user authentication

    This is called by index.py to ensure user
    authentication and initialization of the user related data structures.

    Initialize the user session with the mod_python provided request object.
    The user may have configured (basic) authentication by the web server. In
    case a user is provided, we trust that user and assume it as authenticated.

    Otherwise, we check / ask for the cookie authentication or eventually the
    automation secret authentication."""
    if isinstance(session.user, LoggedInNobody):
        yield False
    else:
        assert session.session_info.auth_type
        with TransactionIdContext(session.user.ident):
            yield True


@contextlib.contextmanager
def TransactionIdContext(user_id: UserId) -> Iterator[None]:
    """Managing context of authenticated user session with cleanup before logout.

    Args:
        user_id:
            The username.

    """
    with UserContext(user_id):
        # Auth with automation secret succeeded before - mark transid as
        # unneeded in this case
        if session.session_info.auth_type == "automation":
            transactions.ignore()
        try:
            yield
        finally:
            transactions.unignore()
            transactions.store_new()


def del_auth_cookie() -> None:
    cookie_name = auth_cookie_name()
    if not request.has_cookie(cookie_name):
        return

    response.unset_http_cookie(cookie_name)


# TODO: Needs to be cleaned up. When using HTTP header auth or web server auth it is not
# ensured that a user exists after letting the user in. This is a problem for the following
# code! We need to define a point where the following code can rely on an existing user
# object. userdb.check_credentials() is doing some similar stuff
# - It also checks the type() of the user_id (Not in the same way :-/)
# - It also calls userdb.is_customer_user_allowed_to_login()
# - It calls userdb.create_non_existing_user() but we don't
# - It calls connection.is_locked() but we don't


@page_registry.register_page("login")
class LoginPage(Page):
    def __init__(self) -> None:
        super().__init__()
        self._no_html_output = False

    def set_no_html_output(self, no_html_output: bool) -> None:
        self._no_html_output = no_html_output

    def page(self) -> None:
        # Initialize the cmk.gui.i18n for the login dialog. This might be
        # overridden later after user login
        cmk.gui.i18n.localize(request.var("lang", active_config.default_language))

        self._do_login()

        if self._no_html_output:
            raise MKAuthException(_("Invalid login credentials."))

        if is_mobile(request, response):
            cmk.gui.mobile.page_login()
            return

        self._show_login_page()

    def _do_login(self) -> None:
        """handle the login form"""
        if not request.var("_login"):
            return

        try:
            if not active_config.user_login and not is_site_login():
                raise MKUserError(None, _("Login is not allowed on this site."))

            # Login via the GET method is allowed only after manually
            # enabling the property "Enable login via GET" in the
            # Global Settings. Please refer to the Werk 14261 for
            # more details.
            if request.request_method != "POST" and not active_config.enable_login_via_get:
                raise MKUserError(None, _("Method not allowed"))

            username_var = request.get_str_input("_username", "")
            assert username_var is not None
            username = UserId(username_var.rstrip())
            if not username:
                raise MKUserError("_username", _("Missing username"))

            password = request.get_validated_type_input_mandatory(Password, "_password")

            default_origtarget = url_prefix() + "check_mk/"
            origtarget = request.get_url_input("_origtarget", default_origtarget)

            # Disallow redirections to:
            #  - logout.py: Happens after login
            #  - side.py: Happens when invalid login is detected during sidebar refresh
            if "logout.py" in origtarget or "side.py" in origtarget:
                origtarget = default_origtarget

            now = datetime.now()
            if result := userdb.check_credentials(username, password, now):
                # use the username provided by the successful login function, this function
                # might have transformed the username provided by the user. e.g. switched
                # from mixed case to lower case.
                username = result

                # The login succeeded! Now:
                # a) Set the auth cookie
                # b) Unset the login vars in further processing
                # c) Redirect to really requested page
                session.login(LoggedInUser(username))

                # This must happen before the enforced password change is
                # checked in order to have the redirects correct...
                if userdb.is_two_factor_login_enabled(username):
                    raise HTTPRedirect(
                        "user_login_two_factor.py?_origtarget=%s" % urlencode(makeuri(request, []))
                    )

                # Never use inplace redirect handling anymore as used in the past. This results
                # in some unexpected situations. We simpy use 302 redirects now. So we have a
                # clear situation.
                # userdb.need_to_change_pw returns either False or the reason description why the
                # password needs to be changed
                if change_reason := userdb.need_to_change_pw(username, now):
                    raise HTTPRedirect(
                        f"user_change_pw.py?_origtarget={urlencode(origtarget)}&reason={change_reason}"
                    )

                raise HTTPRedirect(origtarget)

            userdb.on_failed_login(username, now)
            raise MKUserError(None, _("Invalid login"))
        except MKUserError as e:
            user_errors.add(e)

    def _show_login_page(self) -> None:
        html.render_headfoot = False
        html.add_body_css_class("login")
        make_header(html, get_page_heading(), Breadcrumb(), javascripts=[])

        default_origtarget = (
            "index.py"
            if requested_file_name(request) in ["login", "logout"]
            else makeuri(request, [])
        )
        origtarget = request.get_url_input("_origtarget", default_origtarget)

        # Never allow the login page to be opened in the iframe. Redirect top page to login page.
        # This will result in a full screen login page.
        html.javascript(
            """if(top != self) {
    window.top.location.href = location;
}"""
        )

        # When someone calls the login page directly and is already authed redirect to main page
        if requested_file_name(request) == "login" and session.user.id:
            raise HTTPRedirect(origtarget)

        html.open_div(id_="login")

        html.open_div(id_="login_window")

        html.open_a(href="https://checkmk.com", class_="login_window_logo_link")
        html.img(
            src=theme.detect_icon_path(
                icon_name="login_logo" if theme.has_custom_logo("login_logo") else "checkmk_logo",
                prefix="",
            ),
            id_="logo",
        )
        html.close_a()

        try:
            _show_remaining_trial_time(get_remaining_trial_time())
        except LicenseStateError:
            pass

        html.begin_form("login", method="POST", add_transid=False, action="login.py")
        html.hidden_field("_login", "1")
        html.hidden_field("_origtarget", origtarget)

        saml2_user_error: str | None = None
        if saml_connections := [
            c for c in active_connections_by_type("saml2") if c["owned_by_site"] == omd_site()
        ]:
            saml2_user_error = show_saml2_login(saml_connections, saml2_user_error, origtarget)

        html.open_table()
        html.open_tr()
        html.td(
            html.render_label(
                "%s:" % _("Username"), id_="label_user", class_=["legend"], for_="_username"
            ),
            class_="login_label",
        )
        html.open_td(class_="login_input")
        html.text_input("_username", id_="input_user")
        html.close_td()
        html.close_tr()
        html.open_tr()
        html.td(
            html.render_label(
                "%s:" % _("Password"), id_="label_pass", class_=["legend"], for_="_password"
            ),
            class_="login_label",
        )
        html.open_td(class_="login_input")
        html.password_input("_password", id_="input_pass", size=None)
        html.close_td()
        html.close_tr()
        html.close_table()

        html.open_div(id_="button_text")
        html.button("_login", _("Login"), cssclass=None if saml_connections else "hot")
        html.close_div()

        if user_errors and not saml2_user_error:
            show_user_errors("login_error")

        html.close_div()

        html.open_div(id_="foot")

        if active_config.login_screen.get("login_message"):
            html.open_div(id_="login_message")
            html.show_message(active_config.login_screen["login_message"])
            html.close_div()

        footer: list[HTML] = []
        for title, url, target in active_config.login_screen.get("footer_links", []):
            footer.append(HTMLWriter.render_a(title, href=url, target=target))

        if "hide_version" not in active_config.login_screen:
            footer.append(escape_to_html("Version: %s" % cmk_version.__version__))

        footer.append(
            HTML(
                "&copy; %s"
                % HTMLWriter.render_a("Checkmk GmbH", href="https://tribe29.com", target="_blank")
            )
        )

        html.write_html(HTML(" - ").join(footer))

        html.close_div()

        html.set_focus("_username")
        html.hidden_fields()
        html.end_form()
        html.close_div()

        html.footer()


def _show_remaining_trial_time(remaining_trial_time: RemainingTrialTime) -> None:
    remaining_days: int = remaining_trial_time.days
    remaining_percentage: float = remaining_trial_time.perc

    html.open_div(class_="trial_expiration_info" + (" warning" if remaining_days < 8 else ""))
    html.span(_("%d days") % remaining_days, class_="remaining_days")
    html.span(_(" left in your free trial"))

    html.open_div(class_="time_bar")
    html.div(
        "",
        class_="passed",
        style="width: %d%%;" % (100 - remaining_percentage),
    )
    html.div(
        "",
        class_="remaining",
        style="width: %d%%;" % remaining_percentage,
    )
    html.close_div()

    html.close_div()


@page_registry.register_page("logout")
class LogoutPage(Page):
    def page(self) -> None:
        assert user.id is not None

        session.invalidate()
        session.persist()

        if session.session_info.auth_type == "cookie":
            raise HTTPRedirect(url_prefix() + "check_mk/login.py")

        # Implement HTTP logout with cookie hack
        if not request.has_cookie("logout"):
            response.headers["WWW-Authenticate"] = (
                'Basic realm="OMD Monitoring Site %s"' % omd_site()
            )
            response.set_http_cookie("logout", "1", secure=request.is_secure)
            raise FinalizeRequest(http.client.UNAUTHORIZED)

        response.delete_cookie("logout")
        raise HTTPRedirect(url_prefix() + "check_mk/")
