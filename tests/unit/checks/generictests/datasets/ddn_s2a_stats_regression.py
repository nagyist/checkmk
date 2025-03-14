#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# fmt: off
# mypy: disable-error-code=var-annotated


checkname = "ddn_s2a_stats"


info = [
    [
        "0@108@OK@0",
        "of",
        "0",
        "parameters",
        "were",
        "successful.@All_ports_Read_MBs@100038.8@Read_MBs@10009.8@Read_MBs@9.8@Read_MBs@9.7@Read_MBs@9.5@All_ports_Write_MBs@141.6@Write_MBs@35.3@Write_MBs@35.5@Write_MBs@35.5@Write_MBs@35.3@All_ports_Total_MBs@180.3@Total_MBs@45.2@Total_MBs@45.3@Total_MBs@45.1@Total_MBs@44.8@All_ports_Read_IOs@587@Read_IOs@147@Read_IOs@147@Read_IOs@147@Read_IOs@146@All_ports_Write_IOs@2214@Write_IOs@553@Write_IOs@554@Write_IOs@553@Write_IOs@554@All_ports_Total_IOs@2801@Total_IOs@700@Total_IOs@701@Total_IOs@700@Total_IOs@700@All_ports_Read_Hits@99.4@Read_Hits@99.3@Read_Hits@99.6@Read_Hits@99.6@Read_Hits@99.2@All_ports_Prefetch_Hits@49.3@Prefetch_Hits@49.4@Prefetch_Hits@48.5@Prefetch_Hits@49.9@Prefetch_Hits@49.4@All_ports_Prefetches@7.6@Prefetches@4.5@Prefetches@10.9@Prefetches@4.0@Prefetches@10.5@All_ports_Writebacks@100.0@Writebacks@100.0@Writebacks@100.0@Writebacks@100.0@Writebacks@100.0@All_ports_Rebuild_MBs@0.0@Rebuild_MBs@0.0@Rebuild_MBs@0.0@Rebuild_MBs@0.0@Rebuild_MBs@0.0@All_ports_Verify_MBs@0.7@Verify_MBs@0.3@Verify_MBs@0.0@Verify_MBs@0.3@Verify_MBs@0.0@Total_Disk_IOs@752@Read_Disk_IOs@559@Write_Disk_IOs@193@Total_Disk_MBs@187.3@Read_Disk_MBs@24.5@Write_Disk_MBs@162.8@Total_Disk_Pieces@1014658688@Read_Disk_Pieces@810219904@Write_Disk_Pieces@204438752@BDB_Pieces@12941@Skip_Pieces@1737@Piece_map_Reads@551815573@Piece_map_Writes@0@Piece_map_Reads@20021561@Piece_map_Writes@0@Piece_map_Reads@16946297@Piece_map_Writes@0@Piece_map_Reads@1347647@Piece_map_Writes@0@Piece_map_Reads@2909204@Piece_map_Writes@0@Piece_map_Reads@67189@Piece_map_Writes@0@Piece_map_Reads@25551@Piece_map_Writes@0@Piece_map_Reads@28146@Piece_map_Writes@0@Piece_map_Reads@15044@Piece_map_Writes@0@Piece_map_Reads@9938@Piece_map_Writes@0@Piece_map_Reads@9984@Piece_map_Writes@0@Piece_map_Reads@13283@Piece_map_Writes@0@Piece_map_Reads@10382@Piece_map_Writes@0@Piece_map_Reads@14801@Piece_map_Writes@0@Piece_map_Reads@2754@Piece_map_Writes@0@Piece_map_Reads@40494@Piece_map_Writes@0@Cache_Writeback_data@7.6@Rebuild_data@0.0@Verify_data@0.0@Cache_data_locked@0.0@$",
    ],
    ["OVER"],
]


ddn_s2a_readhits_default_levels = (85.0, 70.0)


discovery = {
    "": [("1", {}), ("2", {}), ("3", {}), ("4", {}), ("Total", {})],
    "io": [("1", {}), ("2", {}), ("3", {}), ("4", {}), ("Total", {})],
    "readhits": [
        ("1", ddn_s2a_readhits_default_levels),
        ("2", ddn_s2a_readhits_default_levels),
        ("3", ddn_s2a_readhits_default_levels),
        ("4", ddn_s2a_readhits_default_levels),
        ("Total", ddn_s2a_readhits_default_levels),
    ],
}


checks = {
    "": [
        (
            "1",
            {"total": (5033164800, 5767168000)},
            [
                (
                    0,
                    "Read: 10009.80 MB/s",
                    [("disk_read_throughput", 10496036044.8, None, None, None, None)],
                ),
                (
                    0,
                    "Write: 35.30 MB/s",
                    [("disk_write_throughput", 37014732.8, None, None, None, None)],
                ),
                (2, "Total: 10045.10 MB/s (warn/crit at 4800.00/5500.00 MB/s)", []),
            ],
        ),
        (
            "2",
            {"total": (5033164800, 5767168000)},
            [
                (
                    0,
                    "Read: 9.80 MB/s",
                    [("disk_read_throughput", 10276044.8, None, None, None, None)],
                ),
                (
                    0,
                    "Write: 35.50 MB/s",
                    [("disk_write_throughput", 37224448.0, None, None, None, None)],
                ),
                (0, "Total: 45.30 MB/s", []),
            ],
        ),
        (
            "3",
            {"total": (5033164800, 5767168000)},
            [
                (
                    0,
                    "Read: 9.70 MB/s",
                    [("disk_read_throughput", 10171187.2, None, None, None, None)],
                ),
                (
                    0,
                    "Write: 35.50 MB/s",
                    [("disk_write_throughput", 37224448.0, None, None, None, None)],
                ),
                (0, "Total: 45.20 MB/s", []),
            ],
        ),
        (
            "4",
            {"total": (5033164800, 5767168000)},
            [
                (
                    0,
                    "Read: 9.50 MB/s",
                    [("disk_read_throughput", 9961472.0, None, None, None, None)],
                ),
                (
                    0,
                    "Write: 35.30 MB/s",
                    [("disk_write_throughput", 37014732.8, None, None, None, None)],
                ),
                (0, "Total: 44.80 MB/s", []),
            ],
        ),
        (
            "Total",
            {"total": (5033164800, 5767168000)},
            [
                (
                    0,
                    "Read: 100038.80 MB/s",
                    [("disk_read_throughput", 104898284748.8, None, None, None, None)],
                ),
                (
                    0,
                    "Write: 141.60 MB/s",
                    [("disk_write_throughput", 148478361.6, None, None, None, None)],
                ),
                (2, "Total: 100180.40 MB/s (warn/crit at 4800.00/5500.00 MB/s)", []),
            ],
        ),
    ],
    "io": [
        (
            "1",
            {"total": (28000, 33000)},
            [
                (0, "Read: 147.00 1/s", [("disk_read_ios", 147.0, None, None, None, None)]),
                (0, "Write: 553.00 1/s", [("disk_write_ios", 553.0, None, None, None, None)]),
                (0, "Total: 700.00 1/s", []),
            ],
        ),
        (
            "2",
            {"total": (28000, 33000)},
            [
                (0, "Read: 147.00 1/s", [("disk_read_ios", 147.0, None, None, None, None)]),
                (0, "Write: 554.00 1/s", [("disk_write_ios", 554.0, None, None, None, None)]),
                (0, "Total: 701.00 1/s", []),
            ],
        ),
        (
            "3",
            {"total": (28000, 33000)},
            [
                (0, "Read: 147.00 1/s", [("disk_read_ios", 147.0, None, None, None, None)]),
                (0, "Write: 553.00 1/s", [("disk_write_ios", 553.0, None, None, None, None)]),
                (0, "Total: 700.00 1/s", []),
            ],
        ),
        (
            "4",
            {"total": (28000, 33000)},
            [
                (0, "Read: 146.00 1/s", [("disk_read_ios", 146.0, None, None, None, None)]),
                (0, "Write: 554.00 1/s", [("disk_write_ios", 554.0, None, None, None, None)]),
                (0, "Total: 700.00 1/s", []),
            ],
        ),
        (
            "Total",
            {"total": (28000, 33000)},
            [
                (0, "Read: 587.00 1/s", [("disk_read_ios", 587.0, None, None, None, None)]),
                (0, "Write: 2214.00 1/s", [("disk_write_ios", 2214.0, None, None, None, None)]),
                (0, "Total: 2801.00 1/s", []),
            ],
        ),
    ],
    "readhits": [
        ("1", (85.0, 70.0), [(0, "99.3%", [("read_hits", 99.3, 85.0, 70.0, None, None)])]),
        ("2", (85.0, 70.0), [(0, "99.6%", [("read_hits", 99.6, 85.0, 70.0, None, None)])]),
        ("3", (85.0, 70.0), [(0, "99.6%", [("read_hits", 99.6, 85.0, 70.0, None, None)])]),
        ("4", (85.0, 70.0), [(0, "99.2%", [("read_hits", 99.2, 85.0, 70.0, None, None)])]),
        ("Total", (85.0, 70.0), [(0, "99.4%", [("read_hits", 99.4, 85.0, 70.0, None, None)])]),
    ],
}
