// Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
// This file is part of Checkmk (https://checkmk.com). It is subject to the
// terms and conditions defined in the file COPYING, which is part of this
// source code package.

#include "livestatus/TableCommands.h"

#include <memory>
#include <vector>

#include "livestatus/Column.h"
#include "livestatus/MonitoringCore.h"
#include "livestatus/Query.h"
#include "livestatus/Row.h"
#include "livestatus/StringColumn.h"

TableCommands::TableCommands(MonitoringCore *mc) : Table(mc) {
    addColumns(this, "", ColumnOffsets{});
}

std::string TableCommands::name() const { return "commands"; }

std::string TableCommands::namePrefix() const { return "command_"; }

// static
void TableCommands::addColumns(Table *table, const std::string &prefix,
                               const ColumnOffsets &offsets) {
    table->addColumn(std::make_unique<StringColumn<Command>>(
        prefix + "name", "The name of the command", offsets,
        [](const Command &cmd) { return cmd._name; }));
    table->addColumn(std::make_unique<StringColumn<Command>>(
        prefix + "line", "The shell command line", offsets,
        [](const Command &cmd) { return cmd._command_line; }));
}

void TableCommands::answerQuery(Query &query, const User & /*user*/) {
    for (auto &cmd : core()->commands()) {
        if (!query.processDataset(Row{&cmd})) {
            break;
        }
    }
}
