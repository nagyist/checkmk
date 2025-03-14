// Copyright (C) 2023 Checkmk GmbH - License: GNU General Public License v2
// This file is part of Checkmk (https://checkmk.com). It is subject to the
// terms and conditions defined in the file COPYING, which is part of this
// source code package.

#include "livestatus/TableEventConsole.h"

#include <chrono>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <functional>
#include <iosfwd>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <unordered_set>
#include <utility>
#include <variant>  // IWYU pragma: keep

#include "livestatus/Column.h"
#include "livestatus/ColumnFilter.h"
#include "livestatus/DoubleColumn.h"
#include "livestatus/EventConsoleConnection.h"
#include "livestatus/Filter.h"
#include "livestatus/MonitoringCore.h"
#include "livestatus/Query.h"
#include "livestatus/Row.h"
#include "livestatus/StringColumn.h"
#include "livestatus/StringUtils.h"
#include "livestatus/TimeColumn.h"
#include "livestatus/User.h"
#include "livestatus/opids.h"

using namespace std::chrono_literals;

namespace {
// NOTE: Keep this in sync with EC code. Ugly...
const std::vector<std::string> grepping_filters = {
    "event_id",        "event_text",        "event_comment", "event_host",
    "event_contact",   "event_application", "event_rule_id", "event_owner",
    "event_ipaddress", "event_core_host"

};

class ECTableConnection : public EventConsoleConnection {
public:
    ECTableConnection(MonitoringCore *mc, const Table &table, Query &query,
                      std::function<bool(const ECRow &)> is_authorized)
        : EventConsoleConnection(mc->loggerLivestatus(),
                                 mc->paths()->event_console_status_socket())
        , mc_{mc}
        , table_{table}
        , query_{query}
        , is_authorized_{std::move(is_authorized)} {}

private:
    void sendRequest(std::ostream &os) override {
        os << std::nounitbuf;
        emitGET(os);
        emitOutputFormat(os);
        emitColumnsHeader(os);
        emitTimeRangeFilter(os);
        emitGreppingFilter(os);
        os << std::endl;
    }

    void emitGET(std::ostream &os) const {
        // skip "eventconsole" prefix :-P
        os << "GET " << table_.name().substr(12);
    }

    static void emitOutputFormat(std::ostream &os) {
        os << "\nOutputFormat: plain";
    }

    void emitColumnsHeader(std::ostream &os) {
        os << "\nColumns:";
        // Initially we consider all columns used in the query...
        auto all = query_.allColumns();
        // ... then we add some special columns which we might need irrespective
        // of the actual query...
        static std::unordered_set<std::string> special_columns{
            // see receiveReply
            "event_host",
            // see isAuthorizedForEvent
            "event_contact_groups_precedence",
            // see  isAuthorizedForEventViaContactGroups
            "event_contact_groups"};
        table_.any_column([&](const auto &col) {
            if (special_columns.find(col->name()) != special_columns.end()) {
                all.insert(col);
            }
            return false;
        });
        // .. and then we ignore all host-related columns, they are implicitly
        // joined later via ECRow._host later.
        for (const auto &c : all) {
            if (!mk::starts_with(c->name(), "host_")) {
                os << " " << c->name();
            }
        }
    }

    void emitTimeRangeFilter(std::ostream &os) {
        if (auto glb = query_.greatestLowerBoundFor("history_time")) {
            os << "\nFilter: history_time >= " << *glb;
        }
        if (auto lub = query_.leastUpperBoundFor("history_time")) {
            os << "\nFilter: history_time <= " << *lub;
        }
    }

    void emitGreppingFilter(std::ostream &os) {
        for (const auto &column_name : grepping_filters) {
            auto conjuncts =
                query_
                    .partialFilter(
                        column_name,
                        [&column_name](const std::string &columnName) {
                            return column_name == columnName;
                        })
                    ->conjuncts();
            if (conjuncts.size() == 1) {
                if (const auto *column_filter =
                        conjuncts[0]->as_column_filter()) {
                    // NOTE: Keep this in sync with EC code. Ugly...
                    switch (column_filter->oper()) {
                        case RelationalOperator::equal:
                        case RelationalOperator::matches:
                        case RelationalOperator::equal_icase:
                        case RelationalOperator::matches_icase:
                            os << "\nFilter: " << column_name << " "
                               << column_filter->oper() << " "
                               << column_filter->value();
                            continue;
                        case RelationalOperator::not_equal:
                        case RelationalOperator::doesnt_match:
                        case RelationalOperator::not_equal_icase:
                        case RelationalOperator::doesnt_match_icase:
                        case RelationalOperator::less:
                        case RelationalOperator::greater_or_equal:
                        case RelationalOperator::greater:
                        case RelationalOperator::less_or_equal:
                            break;
                    }
                }
            }
            if (auto svr = query_.stringValueRestrictionFor(column_name)) {
                os << "\nFilter: " << column_name << " = " << *svr;
            } else {
                auto glb = query_.greatestLowerBoundFor(column_name);
                auto lub = query_.leastUpperBoundFor(column_name);
                if (glb && lub && glb == lub) {
                    os << "\nFilter: " << column_name << " = " << *glb;
                }
                // NOTE: We could emit >= or <= constraints for cases where we
                // know only one bound or the bounds are different, but the EC
                // can't make use of that currently.
            }
        }
    }

    void receiveReply(std::istream &is) override {
        bool is_header = true;
        std::vector<std::string> headers;
        do {
            std::string line;
            std::getline(is, line);
            if (!is || line.empty()) {
                return;
            }
            std::vector<std::string> columns = mk::split(line, '\t');
            if (is_header) {
                headers = std::move(columns);
                is_header = false;
            } else {
                ECRow row{mc_, headers, columns};
                if (is_authorized_(row) && !query_.processDataset(Row{&row})) {
                    return;
                }
            }
        } while (true);
    }

    MonitoringCore *mc_;
    const Table &table_;
    Query &query_;
    const std::function<bool(const ECRow &)> is_authorized_;
};
}  // namespace

ECRow::ECRow(MonitoringCore *mc, const std::vector<std::string> &headers,
             const std::vector<std::string> &columns) {
    auto column_it = columns.cbegin();
    for (const auto &header : headers) {
        if (column_it != columns.end()) {
            map_[header] = *column_it++;
        }
    }
    auto it = map_.find("event_host");
    host_ = it == map_.end() ? nullptr : mc->getHostByDesignation(it->second);
}

// static
std::unique_ptr<StringColumn<ECRow>> ECRow::makeStringColumn(
    const std::string &name, const std::string &description,
    const ColumnOffsets &offsets) {
    return std::make_unique<StringColumn<ECRow>>(
        name, description, offsets,
        [name](const ECRow &r) { return r.getString(name); });
}

// static
std::unique_ptr<IntColumn<ECRow>> ECRow::makeIntColumn(
    const std::string &name, const std::string &description,
    const ColumnOffsets &offsets) {
    return std::make_unique<IntColumn<ECRow>>(
        name, description, offsets,
        [name](const ECRow &r) { return r.getInt(name); });
}

// static
std::unique_ptr<DoubleColumn<ECRow>> ECRow::makeDoubleColumn(
    const std::string &name, const std::string &description,
    const ColumnOffsets &offsets) {
    return std::make_unique<DoubleColumn<ECRow>>(
        name, description, offsets,
        [name](const ECRow &r) { return r.getDouble(name); });
}

// static
std::unique_ptr<TimeColumn<ECRow>> ECRow::makeTimeColumn(
    const std::string &name, const std::string &description,
    const ColumnOffsets &offsets) {
    return std::make_unique<TimeColumn<ECRow>>(
        name, description, offsets, [name](const ECRow &r) {
            return std::chrono::system_clock::from_time_t(
                static_cast<std::time_t>(r.getDouble(name)));
        });
}

// static
std::unique_ptr<ListColumn<ECRow>> ECRow::makeListColumn(
    const std::string &name, const std::string &description,
    const ColumnOffsets &offsets) {
    return std::make_unique<ListColumn<ECRow>>(
        name, description, offsets, [name](const ECRow &r) {
            return mk::ec::split_list(r.getString(name));
        });
}

std::string ECRow::getString(const std::string &column_name) const {
    return get(column_name, "");
}

int32_t ECRow::getInt(const std::string &column_name) const {
    return static_cast<int32_t>(atol(get(column_name, "0").c_str()));
}

double ECRow::getDouble(const std::string &column_name) const {
    return atof(get(column_name, "0").c_str());
}

std::string ECRow::get(const std::string &column_name,
                       const std::string &default_value) const {
    auto it = map_.find(column_name);
    return it == map_.end() ? default_value : it->second;
}

const IHost *ECRow::host() const { return host_ ? host_.get() : nullptr; }

TableEventConsole::TableEventConsole(MonitoringCore *mc) : Table{mc} {}

namespace {
std::function<bool(const ECRow &)> get_authorizer(const Table &table,
                                                  const User &user) {
    if (table.any_column([](const auto &c) {
            return c->name() == "event_contact_groups_precedence";
        })) {
        return [&user](const ECRow &row) {
            const auto *host = row.host();
            return user.is_authorized_for_event(
                row.getString("event_contact_groups_precedence"),
                row.getString("event_contact_groups"), host);
        };
    }
    return [](const ECRow & /*row*/) { return true; };
}
}  // namespace

void TableEventConsole::answerQuery(Query &query, const User &user) {
    if (core()->mkeventdEnabled()) {
        try {
            ECTableConnection{core(), *this, query, get_authorizer(*this, user)}
                .run();
        } catch (const std::runtime_error &err) {
            query.badGateway(err.what());
        }
    }
}
