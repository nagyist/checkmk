#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# fmt: off
# mypy: disable-error-code=var-annotated

checkname = "jolokia_jvm_garbagecollectors"

info = [
    [
        "MyJIRA",
        "java.lang:name=*,type=GarbageCollector/CollectionCount,CollectionTime,Name",
        '{"java.lang:name=PS MarkSweep,type=GarbageCollector": {"CollectionTime": 4753, "Name": "PS MarkSweep", "CollectionCount": 7}, "java.lang:name=PS Scavenge,type=GarbageCollector": {"CollectionTime": 209798, "Name": "PS Scavenge", "CollectionCount": 2026}}',
    ]
]

discovery = {"": [("MyJIRA GC PS MarkSweep", {}), ("MyJIRA GC PS Scavenge", {})]}

checks = {
    "": [
        (
            "MyJIRA GC PS MarkSweep",
            {},
            [
                (
                    0,
                    "Garbage collections: 0.00/s",
                    [("jvm_garbage_collection_count", 0.0, None, None, None, None)],
                ),
                (
                    0,
                    "Time spent collecting garbage: 0.00 %",
                    [("jvm_garbage_collection_time", 0.0, None, None, None, None)],
                ),
            ],
        ),
        (
            "MyJIRA GC PS Scavenge",
            {},
            [
                (
                    0,
                    "Garbage collections: 0.00/s",
                    [("jvm_garbage_collection_count", 0.0, None, None, None, None)],
                ),
                (
                    0,
                    "Time spent collecting garbage: 0.00 %",
                    [("jvm_garbage_collection_time", 0.0, None, None, None, None)],
                ),
            ],
        ),
        (
            "MyJIRA GC PS Scavenge",
            {"CollectionCount": (-6.0, 6.0)},
            [
                (
                    1,
                    "Garbage collections: 0.00/s (warn/crit at -0.10/s/0.10/s)",
                    [("jvm_garbage_collection_count", 0.0, -0.1, 0.1, None, None)],
                ),
                (
                    0,
                    "Time spent collecting garbage: 0.00 %",
                    [("jvm_garbage_collection_time", 0.0, None, None, None, None)],
                ),
            ],
        ),
        (
            "MyJIRA GC PS Scavenge",
            {"collection_time": (-0.1, 0.1)},
            [
                (
                    0,
                    "Garbage collections: 0.00/s",
                    [("jvm_garbage_collection_count", 0.0, None, None, None, None)],
                ),
                (
                    1,
                    "Time spent collecting garbage: 0.00 % (warn/crit at -0.10 %/0.10 %)",
                    [("jvm_garbage_collection_time", 0.0, -0.1, 0.1, None, None)],
                ),
            ],
        ),
    ]
}
