#!/usr/bin/env python3
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

# fmt: off
# mypy: disable-error-code=var-annotated

checkname = "skype"

info = [
    ["sampletime", "14425512178844", "10000000"],
    ["[LS:A/V Edge - UDP Counters]"],
    [
        "instance",
        "A/V Edge - Total Relay Sessions",
        "A/V Edge - Active Relay Sessions - Authenticated",
        "A/V Edge - Active Relay Sessions - Allocated Port",
        "A/V Edge - Active Relay Sessions - Data",
        "A/V Edge - Allocated Port Pool Count",
        "A/V Edge - Allocated Port Pool Miss Count",
        "A/V Edge - Allocate Requests/sec",
        "A/V Edge - Authentication Failures",
        "A/V Edge - Authentication Failures/sec",
        "A/V Edge - Allocate Requests Exceeding Port Limit",
        "A/V Edge - Allocate Requests Exceeding Port Limit/sec",
        "A/V Edge - Alternate Server Redirects",
        "A/V Edge - Alternate Server Redirects/sec",
        "A/V Edge - Client Request Errors (4xx Responses)",
        "A/V Edge - Client Request Errors/sec (4xx Responses/sec)",
        "A/V Edge - Client SetActiveDestination Request Errors",
        "A/V Edge - Client SetActiveDestination Request Errors/sec",
        "A/V Edge - Session Idle Timeouts/sec",
        "A/V Edge - Packets Received/sec",
        "A/V Edge - Packets Sent/sec",
        "A/V Edge - Average TURN Packet Latency (milliseconds)",
        " ",
        "A/V Edge - Average Data Packet Latency (milliseconds)",
        " ",
        "A/V Edge - Average TURN BW Packet Latency (milliseconds)",
        " ",
        "A/V Edge - Maximum Packet Latency (milliseconds)",
        "A/V Edge - Packets Dropped/sec",
        "A/V Edge - Packets Not Forwarded/sec",
        "A/V Edge - Average Depth of Connection Receive Queue",
        " ",
        "A/V Edge - Maximum Depth of Connection Receive Queue",
        "A/V Edge - Active Sessions Exceeding Avg Bandwidth Limit",
        "A/V Edge - Active Sessions Exceeding Peak Bandwidth Limit",
        "A/V Edge - Active Federated UDP Sessions",
        "A/V Edge - Active Federated UDP Sessions/sec",
    ],
    [
        "_Total",
        "127527",
        "1",
        "1",
        "1",
        "373",
        "0",
        "380527",
        "74",
        "74",
        "0",
        "0",
        "0",
        "0",
        "131633",
        "131633",
        "0",
        "0",
        "36901",
        "81751143",
        "81195598",
        "0",
        "0",
        "83137339",
        "81518637",
        "0",
        "0",
        "632",
        "0",
        "117987",
        "106330854",
        "81547681",
        "183",
        "0",
        "0",
        "0",
        "0",
    ],
    [
        "Private IPv4 Network Interface",
        "121187",
        "1",
        "1",
        "1",
        "0",
        "0",
        "361037",
        "31",
        "31",
        "0",
        "0",
        "0",
        "0",
        "124524",
        "124524",
        "0",
        "0",
        "5893",
        "47237024",
        "31686484",
        "0",
        "0",
        "47742041",
        "47236960",
        "0",
        "0",
        "23",
        "0",
        "2957",
        "67615154",
        "47236959",
        "81",
        "0",
        "0",
        "0",
        "0",
    ],
    [
        "Public IPv4 Network Interface",
        "6340",
        "0",
        "0",
        "0",
        "373",
        "0",
        "19490",
        "43",
        "43",
        "0",
        "0",
        "0",
        "0",
        "7109",
        "7109",
        "0",
        "0",
        "31008",
        "34514119",
        "49509114",
        "0",
        "0",
        "35395298",
        "34281677",
        "0",
        "0",
        "609",
        "0",
        "115030",
        "38715700",
        "34310722",
        "102",
        "0",
        "0",
        "0",
        "0",
    ],
    [
        "Private IPv6 Network Interface",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
    ],
    [
        "Public IPv6 Network Interface",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
    ],
]


checks = {
    "edge": [
        (
            None,
            {
                "authentication_failures": {"upper": (20, 40)},
                "allocate_requests_exceeding": {"upper": (20, 40)},
                "packets_dropped": {"upper": (200, 400)},
            },
            [
                (
                    0,
                    "UDP auth failures/sec: 0.00",
                    [("edge_udp_failed_auth", 0.0, 20, 40, None, None)],
                ),
                (
                    0,
                    "UDP allocate requests > port limit/sec: 0.00",
                    [("edge_udp_allocate_requests_exceeding_port_limit", 0.0, 20, 40, None, None)],
                ),
                (
                    0,
                    "UDP packets dropped/sec: 0.00",
                    [("edge_udp_packets_dropped", 0.0, 200, 400, None, None)],
                ),
            ],
        )
    ],
}
