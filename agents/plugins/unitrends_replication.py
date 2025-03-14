#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

__version__ = "2.3.0b1"

import sys
import time
from urllib.request import urlopen
from xml.dom import minidom

now = int(time.time())
start = now - 24 * 60 * 60
end = now
dpu = 1

url = (
    "http://localhost/recoveryconsole/bpl/syncstatus.php?type=replicate&arguments=start:%s,end:%s&sid=%s&auth=1:"
    % (start, end, dpu)
)

xml = urlopen(url)  # nosec B310 # BNS:28af27 # pylint: disable=consider-using-with

sys.stdout.write("<<<unitrends_replication:sep(124)>>>\n")
dom = minidom.parse(xml)
for item in dom.getElementsByTagName("SecureSyncStatus"):
    application = item.getElementsByTagName("Application")
    if application:
        application = application[0].attributes["Name"].value
    else:
        application = "N/A"
    result = item.getElementsByTagName("Result")[0].firstChild.data
    completed = item.getElementsByTagName("Complete")[0].firstChild.data
    targetname = item.getElementsByTagName("TargetName")[0].firstChild.data
    instancename = item.getElementsByTagName("InstanceName")[0].firstChild.data
    sys.stdout.write(
        "%s|%s|%s|%s|%s\n" % (application, result, completed, targetname, instancename)
    )
