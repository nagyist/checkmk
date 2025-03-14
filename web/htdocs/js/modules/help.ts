// Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
// This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
// conditions defined in the file COPYING, which is part of this source code package.

import * as utils from "utils";
import * as ajax from "ajax";

//#   .-Help Toggle--------------------------------------------------------.
//#   |          _   _      _         _____                 _              |
//#   |         | | | | ___| |_ __   |_   _|__   __ _  __ _| | ___         |
//#   |         | |_| |/ _ \ | '_ \    | |/ _ \ / _` |/ _` | |/ _ \        |
//#   |         |  _  |  __/ | |_) |   | | (_) | (_| | (_| | |  __/        |
//#   |         |_| |_|\___|_| .__/    |_|\___/ \__, |\__, |_|\___|        |
//#   |                      |_|                |___/ |___/                |
//#   '--------------------------------------------------------------------'

function is_help_active() {
    const helpdivs = document.getElementsByClassName(
        "help"
    ) as HTMLCollectionOf<HTMLElement>;
    return helpdivs.length !== 0 && helpdivs[0].style.display === "flex";
}

export function toggle() {
    if (is_help_active()) {
        switch_help(false);
    } else {
        switch_help(true);
    }
    toggle_help_page_menu_icon();
}

function switch_help(how: boolean) {
    // recursive scan for all div class=help elements
    const helpdivs = document.getElementsByClassName(
        "help"
    ) as HTMLCollectionOf<HTMLElement>;
    let i;
    for (i = 0; i < helpdivs.length; i++) {
        helpdivs[i].style.display = how ? "flex" : "none";
    }

    // small hack for wato ruleset lists, toggle the "float" and "nofloat"
    // classes on those objects to make the layout possible
    const rulesetdivs = utils.querySelectorAllByClassName("ruleset");
    for (i = 0; i < rulesetdivs.length; i++) {
        if (how) {
            if (utils.has_class(rulesetdivs[i], "float")) {
                utils.remove_class(rulesetdivs[i], "float");
                utils.add_class(rulesetdivs[i], "nofloat");
            }
        } else {
            if (utils.has_class(rulesetdivs[i], "nofloat")) {
                utils.remove_class(rulesetdivs[i], "nofloat");
                utils.add_class(rulesetdivs[i], "float");
            }
        }
    }

    ajax.call_ajax("ajax_switch_help.py?enabled=" + (how ? "yes" : ""));
}

function toggle_help_page_menu_icon() {
    const icon = document
        .getElementById("menu_entry_inline_help")!
        .getElementsByTagName("img")[0];
    icon.src = icon.src.includes("toggle_on")
        ? icon.src.replace("toggle_on", "toggle_off")
        : icon.src.replace("toggle_off", "toggle_on");
}
