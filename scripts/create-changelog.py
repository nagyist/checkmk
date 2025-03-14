#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import sys
from collections.abc import Sequence
from pathlib import Path

import cmk.utils.werks
from cmk.utils.werks import Werk


def create_changelog(dest_file_path: str, precompiled_werk_files: Sequence[Path]) -> None:
    werks = load_werks(precompiled_werk_files)

    with open(dest_file_path, "w", encoding="utf-8") as f:
        cmk.utils.werks.write_as_text(werks, f)

        # Append previous werk changes
        p = Path(dest_file_path + ".in")
        if p.exists():
            f.write("\n\n")
            f.write(p.read_text())


def load_werks(precompiled_werk_files: Sequence[Path]) -> dict[int, Werk]:
    werks: dict[int, Werk] = {}
    for path in precompiled_werk_files:
        werks.update(cmk.utils.werks.load_precompiled_werks_file(path))
    return werks


#
# MAIN
#

if len(sys.argv) < 3:
    sys.stderr.write("ERROR: Call like this: create-changelog CHANGELOG WERK_DIR...\n")
    sys.exit(1)

dest_file, arg_precompiled_werk_files = sys.argv[1], sys.argv[2:]
create_changelog(dest_file, [Path(p) for p in arg_precompiled_werk_files])
