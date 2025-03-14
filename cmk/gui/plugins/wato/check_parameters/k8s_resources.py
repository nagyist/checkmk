#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

from cmk.gui.i18n import _
from cmk.gui.plugins.wato.utils import (
    CheckParameterRulespecWithoutItem,
    rulespec_registry,
    RulespecGroupCheckParametersApplications,
)
from cmk.gui.valuespec import Dictionary, Percentage, Tuple

######################################################################
# NOTE: This valuespec and associated check are deprecated and will be
#       removed in Checkmk version 2.2.
######################################################################


def _parameter_valuespec_k8s_resources():
    return Dictionary(
        elements=[
            (
                "pods",
                Tuple(
                    title=_("Pods"),
                    elements=[
                        Percentage(title=_("Warning above"), default_value=80.0),
                        Percentage(title=_("Critical above"), default_value=90.0),
                    ],
                ),
            ),
            (
                "cpu",
                Tuple(
                    title=_("CPU"),
                    elements=[
                        Percentage(title=_("Warning above"), default_value=80.0),
                        Percentage(title=_("Critical above"), default_value=90.0),
                    ],
                ),
            ),
            (
                "memory",
                Tuple(
                    title=_("Memory"),
                    elements=[
                        Percentage(title=_("Warning above"), default_value=80.0),
                        Percentage(title=_("Critical above"), default_value=90.0),
                    ],
                ),
            ),
        ],
    )


rulespec_registry.register(
    CheckParameterRulespecWithoutItem(
        check_group_name="k8s_resources",
        group=RulespecGroupCheckParametersApplications,
        match_type="dict",
        parameter_valuespec=_parameter_valuespec_k8s_resources,
        title=lambda: _("Kubernetes resources"),
        is_deprecated=True,
    )
)
