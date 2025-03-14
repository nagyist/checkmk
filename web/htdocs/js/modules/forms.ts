// Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
// This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
// conditions defined in the file COPYING, which is part of this source code package.

import $ from "jquery";
import "select2";
import Tagify, {EditTagsRuntimeSettings} from "@yaireo/tagify";
import "element-closest-polyfill";
import Swal from "sweetalert2";

import * as utils from "utils";
import * as ajax from "ajax";
import {initialize_autocompleters, toggle_label_row_opacity} from "valuespecs";
import {CMKAjaxReponse} from "types";

interface TagifyState {
    inputText: string;
    editing: boolean;
    composing: boolean;
    actions: any;
    mixMode: any;
    dropdown: any;
    flaggedTags: any;
    blockChangeEvent: boolean;
    lastOriginalValueReported: string;
    mainEvents: boolean;
}

declare global {
    class Tagify {
        state: TagifyState;
    }
}

interface ConfirmLinkCustomArgs {
    title: string;
    html: string;
    confirmButtonText: string;
    cancelButtonText: string;
    icon: string;
    custom_class_options: Record<string, string>;
}

interface CheckMKTagifyArgs extends Tagify.TagifySettings<CheckMKTagifyData> {
    pattern: RegExp;
    dropdown: {
        enabled: number;
        caseSensitive: boolean;
    };
    editTags: EditTagsRuntimeSettings;
}

interface CheckMKTagifyData extends Tagify.BaseTagData {
    state: TagifyState;
}

export function enable_dynamic_form_elements(
    container: HTMLElement | null = null
) {
    enable_select2_dropdowns(container);
    enable_label_input_fields(container);
}

let g_previous_timeout_id: number | null = null;
let g_ajax_obj: XMLHttpRequest | null;

export function enable_select2_dropdowns(
    container: JQuery<Document> | HTMLElement | HTMLDocument | null
) {
    if (!container) container = $(document);

    const elements = $(container)
        .find(".select2-enable")
        .not(".vlof_prototype .select2-enable");
    elements.select2({
        dropdownAutoWidth: true,
        minimumResultsForSearch: 5,
    });
    initialize_autocompleters(container);

    // workaround for select2-input not being in focus
    $(document).on("select2:open", e => {
        (
            document.querySelector(
                ".select2-search__field"
            ) as HTMLSelectElement
        )?.focus();
        if (
            e.target.id.match("labels.*vs") &&
            e.target instanceof HTMLSelectElement
        )
            toggle_label_row_opacity(e.target, true);
    });
    $(document).on("select2:close", e => {
        if (
            e.target.id.match("labels.*vs") &&
            e.target instanceof HTMLSelectElement
        )
            toggle_label_row_opacity(e.target, false);
    });
}

function enable_label_input_fields(
    container: HTMLElement | HTMLDocument | null
) {
    if (!container) container = document;

    const elements = container.querySelectorAll(
        "input.labels"
    ) as NodeListOf<HTMLInputElement>;
    elements.forEach(element => {
        // Do not tagify objects that are part of a ListOf valuespec template
        if (element.closest(".vlof_prototype") !== null) {
            return;
        }

        const data_max_labels = element.getAttribute("data-max-labels");
        const max_labels = data_max_labels ? parseFloat(data_max_labels) : null;
        const world = element.getAttribute("data-world");

        const tagify_args: CheckMKTagifyArgs = {
            pattern: /^[^:]+:[^:]+$/,
            dropdown: {
                enabled: 1, // show dropdown on first character
                caseSensitive: false,
            },
            editTags: {
                clicks: 1, // single click to edit a tag
                keepInvalid: false, // if after editing, tag is invalid, auto-revert
            },
        };

        if (max_labels !== null) {
            tagify_args["maxTags"] = max_labels;
        }

        const tagify = new Tagify<CheckMKTagifyData>(element, tagify_args);

        // Add custom validation function that ensures that a single label key is only used once
        tagify.settings.validate = (t => {
            return add_label => {
                const label_key = add_label.value.split(":", 1)[0];
                const key_error_msg =
                    "Only one value per KEY can be used at a time.";
                if (tagify.settings.maxTags == 1) {
                    const label_type = element.getAttribute("class")!;
                    const existing_tags = document.querySelectorAll(
                        `.tagify.${label_type.replace(
                            " ",
                            "."
                        )} .tagify__tag-text`
                    );
                    const existing_keys_array = Array.prototype.map.call(
                        existing_tags,
                        function (x) {
                            return x.textContent.split(":")[0];
                        }
                    );

                    if (
                        existing_keys_array.includes(label_key) &&
                        !t.state.editing
                    ) {
                        return key_error_msg;
                    }
                } else {
                    for (const existing_label of t.value) {
                        // Do not check the current edited value. KEY would be
                        // always present leading to invalid value
                        if (t.state.editing) {
                            continue;
                        }
                        const existing_key = existing_label.value.split(
                            ":",
                            1
                        )[0];

                        if (label_key == existing_key) {
                            return key_error_msg;
                        }
                    }
                }
                return true;
            };
        })(tagify);

        tagify.on("invalid", function (e) {
            let message;
            if (
                e.type == "invalid" &&
                e.detail.message == "number of tags exceeded"
            ) {
                message = "Only one tag allowed";
            } else if (
                (e.type == "invalid" &&
                    e.detail.message
                        .toString()
                        .includes("Only one value per KEY")) ||
                e.detail.message == "already exists"
            ) {
                message =
                    "Only one value per KEY can be used at a time." +
                    e.detail.data.value +
                    " is already used.";
            } else {
                message =
                    "Labels need to be in the format <tt>[KEY]:[VALUE]</tt>. For example <tt>os:windows</tt>.</div>";
            }

            $("div.label_error").remove(); // Remove all previous errors

            // Print a validation error message
            const msg = document.createElement("div");
            msg.classList.add("message", "error", "label_error");

            msg.innerHTML = message;
            element.parentNode!.insertBefore(msg, element.nextSibling);
        });

        tagify.on("add", function () {
            $("div.label_error").remove(); // Remove all previous errors
        });

        // Realize the auto completion dropdown field by using an ajax call
        tagify.on("input", function (e) {
            $("div.label_error").remove(); // Remove all previous errors

            const value = e.detail.value;
            tagify.settings.whitelist.length = 0; // reset the whitelist

            // show loading animation and hide the suggestions dropdown
            tagify.loading(true).dropdown.hide.call(tagify);

            const post_data =
                "request=" +
                encodeURIComponent(
                    JSON.stringify({
                        ident: "label",
                        value: value,
                        params: {world: world},
                    })
                );

            if (g_previous_timeout_id !== null) {
                clearTimeout(g_previous_timeout_id);
            }
            g_previous_timeout_id = window.setTimeout(function () {
                kill_previous_autocomplete_call();
                ajax_call_autocomplete_labels(
                    post_data,
                    tagify,
                    value,
                    element
                );
            }, 300);
        });
    });
}

function kill_previous_autocomplete_call() {
    if (g_ajax_obj) {
        g_ajax_obj.abort();
        g_ajax_obj = null;
    }
}

interface AjaxVsAutocomplete {
    choices: [string | null, string][];
}

function ajax_call_autocomplete_labels(
    post_data: string,
    tagify: Tagify<CheckMKTagifyData>,
    value: string,
    element: HTMLInputElement
) {
    g_ajax_obj = ajax.call_ajax("ajax_vs_autocomplete.py", {
        method: "POST",
        post_data: post_data,
        response_handler: function (
            handler_data: {value: string; tagify: Tagify<CheckMKTagifyData>},
            ajax_response: string
        ) {
            const response: CMKAjaxReponse<AjaxVsAutocomplete> =
                JSON.parse(ajax_response);
            if (response.result_code != 0) {
                console.log(
                    "Error [" + response.result_code + "]: " + response.result
                ); // eslint-disable-line
                return;
            }

            const result_objects: {value: string}[] = [];
            response.result.choices.forEach(entry => {
                result_objects.push({value: entry[1]});
            });

            handler_data.tagify.settings.whitelist.splice(
                10,
                //@ts-ignore // result is just a dict with choices filed so length is undefined!?
                response.result.length,
                //@ts-ignore // there is no matching function
                ...result_objects
            );
            // render the suggestions dropdown
            handler_data.tagify.loading(false);
            handler_data.tagify.dropdown.show.call(
                handler_data.tagify,
                handler_data.value
            );

            const tagify__input = element.parentElement!.querySelector(
                ".tagify__input"
            ) as HTMLElement;
            if (tagify__input) {
                let max = value.length;
                handler_data.tagify.suggestedListItems!.forEach(entry => {
                    max = Math.max(entry.value.length, max);
                });
                const fontSize = parseInt(
                    window
                        .getComputedStyle(tagify__input, null)
                        .getPropertyValue("font-size")
                );
                // Minimum width set by tagify
                const size = Math.max(110, max * (fontSize / 2 + 1));
                tagify__input.style.width = size.toString() + "px";
                tagify__input.parentElement!.style.width =
                    (size + 10).toString() + "px";
            }
        },
        handler_data: {
            value: value,
            tagify: tagify,
        },
    });
}

// Handle Enter key in textfields
export function textinput_enter_submit(event: KeyboardEvent, submit: string) {
    const keyCode = event.which || event.keyCode;
    if (keyCode == 13) {
        if (submit) {
            const button = document.getElementById(submit);
            if (button) button.click();
        }
        return utils.prevent_default_events(event);
    }
}

// Helper function to display nice popup confirm dialogs
export function confirm_dialog(
    optional_args: any,
    confirm_handler: null | (() => void)
) {
    const default_custom_class_args = {
        title: "confirm_title",
        container: "confirm_container",
        popup: "confirm_popup",
        content: "confirm_content",
        htmlContainer: "confirm_content",
        actions: "confirm_actions",
        icon: "confirm_icon",
        confirmButton: "hot",
    };

    let custom_class_args;
    if ("customClass" in optional_args) {
        custom_class_args = {
            ...default_custom_class_args,
            ...optional_args["customClass"],
        };
        delete optional_args["customClass"];
    } else {
        custom_class_args = default_custom_class_args;
    }

    const default_args = {
        // https://sweetalert2.github.io/#configuration
        target: "#page_menu_popups",
        position: "top-start",
        grow: "row",
        allowOutsideClick: false,
        backdrop: false,
        showClass: {
            popup: "",
            backdrop: "",
        },
        hideClass: {
            popup: "",
            backdrop: "",
        },
        buttonsStyling: false,
        showCancelButton: true,
        confirmButtonText: "Yes",
        cancelButtonText: "No",
        icon: "question",
        customClass: custom_class_args,
    };

    const args = {
        ...default_args,
        ...optional_args,
    };

    Swal.fire(args).then(result => {
        if (confirm_handler && result.value) {
            confirm_handler();
        }
    });
}

// Makes a form submittable after explicit confirmation
export function add_confirm_on_submit(form_id: string, message: string) {
    const form = document.getElementById(form_id);
    if (form instanceof HTMLElement) {
        form.addEventListener("submit", e => {
            confirm_dialog({html: message}, () => {
                (document.getElementById(form_id) as HTMLFormElement)?.submit();
            });
            return utils.prevent_default_events(e!);
        });
    } else
        throw new Error(
            `Can not add confirm on submit: The Form with the id ${form_id} does not exist`
        );
}

// Used as onclick handler on links to confirm following the link or not
export function confirm_link(
    url: string,
    message: string,
    custom_args: ConfirmLinkCustomArgs
) {
    confirm_dialog({...custom_args, html: message}, () => {
        location.href = url;
    });
}

// On submit of the filter form (filter popup), remove unnecessary HTTP variables
export function on_filter_form_submit_remove_vars(form_id: string) {
    const form = document.getElementById(form_id) as HTMLFormElement;
    _remove_listof_vars(form);
}

function _remove_listof_vars(form: HTMLFormElement) {
    const rm_classes: string[] = ["vlof_prototype", "orig_index"];
    for (const rm_class of rm_classes) {
        const elements: HTMLCollection = form.getElementsByClassName(rm_class);
        while (elements.length > 0) {
            elements[0].parentNode!.removeChild(elements[0]);
        }
    }
}
