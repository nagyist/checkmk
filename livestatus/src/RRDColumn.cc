// Copyright (C) 2019 Checkmk GmbH - License: GNU General Public License v2
// This file is part of Checkmk (https://checkmk.com). It is subject to the
// terms and conditions defined in the file COPYING, which is part of this
// source code package.

#include "RRDColumn.h"

#include <rrd.h>

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <memory>
#include <set>
#include <stdexcept>
#include <type_traits>

#include "livestatus/Interface.h"
#include "livestatus/Logger.h"
#include "livestatus/Metric.h"
#include "livestatus/MonitoringCore.h"
#include "livestatus/PnpUtils.h"
#include "livestatus/strutil.h"

#ifdef CMC
#include "CmcHost.h"
#include "CmcService.h"
#else
#include "NebHost.h"
#include "NebService.h"
#endif

RRDColumnArgs::RRDColumnArgs(const std::string &arguments,
                             const std::string &column_name) {
    auto invalid = [&column_name](const std::string &message) {
        throw std::runtime_error("invalid arguments for column '" +
                                 column_name + ": " + message);
    };
    // We expect the following arguments: RPN:START_TIME:END_TIME:RESOLUTION
    // Example: fs_used,1024,/:1426411073:1426416473:5
    std::vector<char> args(arguments.begin(), arguments.end());
    args.push_back('\0');
    char *scan = args.data();

    // Reverse Polish Notation Expression for extraction start RRD
    char *rpn = next_token(&scan, ':');
    if (rpn == nullptr || rpn[0] == 0) {
        invalid("missing RPN expression for RRD");
    }
    this->rpn = rpn;

    // Start time of queried range - UNIX time stamp
    char *start_time = next_token(&scan, ':');
    if (start_time == nullptr || start_time[0] == 0 || atol(start_time) <= 0) {
        invalid("missing, negative or overflowed start time");
    }
    this->start_time = atol(start_time);

    // End time - UNIX time stamp
    char *end_time = next_token(&scan, ':');
    if (end_time == nullptr || end_time[0] == 0 || atol(end_time) <= 0) {
        invalid(" missing, negative or overflowed end time");
    }
    this->end_time = atol(end_time);

    // Resolution in seconds - might output less
    char *resolution = next_token(&scan, ':');
    if (resolution == nullptr || resolution[0] == 0 || atoi(resolution) <= 0) {
        invalid("missing or negative resolution");
    }
    this->resolution = atoi(resolution);

    // Optional limit of data points
    const char *max_entries = next_token(&scan, ':');
    if (max_entries == nullptr) {
        max_entries = "400";  // RRDTool default
    }
    if (max_entries[0] == 0 || atoi(max_entries) < 10) {
        invalid("Wrong input for max rows");
    }
    this->max_entries = atoi(max_entries);

    if (next_token(&scan, ':') != nullptr) {
        invalid("too many arguments");
    }
}

std::vector<RRDDataMaker::value_type> RRDDataMaker::operator()(
    const IHost &hst, std::chrono::seconds timezone_offset) const {
    return make(hst.name(), dummy_service_description(), timezone_offset);
}

std::vector<RRDDataMaker::value_type> RRDDataMaker::operator()(
    const IService &svc, std::chrono::seconds timezone_offset) const {
    return make(svc.host().name(), svc.description(), timezone_offset);
}

#ifdef CMC
std::vector<RRDDataMaker::value_type> RRDDataMaker::operator()(
    const Host &hst, std::chrono::seconds timezone_offset) const {
    return (*this)(CmcHost{hst}, timezone_offset);
}

std::vector<RRDDataMaker::value_type> RRDDataMaker::operator()(
    const Service &svc, std::chrono::seconds timezone_offset) const {
    return (*this)(CmcService{svc}, timezone_offset);
}
#else
std::vector<RRDDataMaker::value_type> RRDDataMaker::operator()(
    const ::host &hst, std::chrono::seconds timezone_offset) const {
    return (*this)(NebHost{hst}, timezone_offset);
}

std::vector<RRDDataMaker::value_type> RRDDataMaker::operator()(
    const ::service &svc, std::chrono::seconds timezone_offset) const {
    return (*this)(NebService{svc}, timezone_offset);
}
#endif

namespace {
bool isVariableName(const std::string &token) {
    auto is_operator = [](char c) { return strchr("+-/*", c) != nullptr; };
    auto is_number_part = [](char c) {
        return strchr("0123456789.", c) != nullptr;
    };

    return !(is_operator(token[0]) ||
             std::all_of(token.begin(), token.end(), is_number_part));
}

// TODO(sp): copy-n-paste from pnp4nagios.cc
std::string replace_all(const std::string &str, const std::string &chars,
                        char replacement) {
    std::string result(str);
    size_t i = 0;
    while ((i = result.find_first_of(chars, i)) != std::string::npos) {
        result[i++] = replacement;
    }
    return result;
}

std::pair<Metric::Name, std::string> getVarAndCF(const std::string &str) {
    const size_t dot_pos = str.find_last_of('.');
    if (dot_pos != std::string::npos) {
        const Metric::Name head{str.substr(0, dot_pos)};
        const std::string tail = str.substr(dot_pos);
        if (tail == ".max") {
            return std::make_pair(head, "MAX");
        }
        if (tail == ".min") {
            return std::make_pair(head, "MIN");
        }
        if (tail == ".average") {
            return std::make_pair(head, "AVERAGE");
        }
    }
    return std::make_pair(Metric::Name{str}, "MAX");
}

struct Data {
    RRDDataMaker::C::time_point start;
    RRDDataMaker::C::time_point end;
    unsigned long step{0};
    std::vector<double> values;

    [[nodiscard]] std::vector<RRDDataMaker::value_type> as_vector(
        std::chrono::seconds timezone_offset) const {
        // We output meta data as first elements in the list. Note: In Python or
        // JSON we could output nested lists. In CSV mode this is not possible
        // and we rather stay compatible with CSV mode.
        std::vector<RRDDataMaker::value_type> result;
        result.reserve(values.size() + 3);
        result.emplace_back(start + timezone_offset);
        result.emplace_back(end + timezone_offset);
        result.emplace_back(step);
        result.insert(result.end(), values.cbegin(), values.cend());
        return result;
    }
};
};  // namespace

// TODO(mk): Convert all of the RPN expressions that are available in RRDTool
// and that have a different syntax then we have in our metrics system.
// >= --> GE. Or should we also go with GE instead of >=?
// Look at http://oss.oetiker.ch/rrdtool/doc/rrdgraph_rpn.en.html for details!
std::vector<RRDDataMaker::value_type> RRDDataMaker::make(
    const std::string &host_name, const std::string &service_description,
    std::chrono::seconds timezone_offset) const {
    // Prepare the arguments for rrdtool xport in a dynamic array of strings.
    // Note: The actual step might be different!
    std::vector<std::string> argv_s{
        "rrdtool xport",  // name of program (ignored)
        "-s",
        std::to_string(_args.start_time),
        "-e",
        std::to_string(_args.end_time),
        "--step",
        std::to_string(_args.resolution)};

    if (_args.max_entries > 0) {
        argv_s.emplace_back("-m");
        argv_s.emplace_back(std::to_string(_args.max_entries));
    }

    // We have an RPN like fs_used,1024,*. In order for that to work, we need to
    // create DEFs for all RRDs of the service first. The we create a CDEF with
    // our RPN and finally do the export. One difficulty here: we do not know
    // the exact variable names. The filenames of the RRDs have several
    // characters replaced with "_". This is a one-way escaping where we cannot
    // get back the original variable values. So the cleaner (an probably
    // faster) way is to look for the names of variables within our RPN
    // expressions and create DEFs just for them - if the according RRD exists.
    std::string converted_rpn;  // convert foo.max -> foo-max
    std::vector<char> rpn_copy(_args.rpn.begin(), _args.rpn.end());
    rpn_copy.push_back('\0');
    char *scan = rpn_copy.data();

    // map from RRD variable names to perf variable names. The latter ones
    // can contain several special characters (like @ and -) which the RRD
    // variables cannot. The variable names are constructed with "var_%u"
    unsigned next_variable_number = 0;
    std::set<std::string> touched_rrds;

    while (const char *tok = next_token(&scan, ',')) {
        const std::string token = tok;
        if (!converted_rpn.empty()) {
            converted_rpn += ",";
        }
        if (!isVariableName(token)) {
            converted_rpn += token;
            continue;
        }

        // If the token looks like a variable name, then check if there is a
        // matching RRD and create a matching DEF: command if that is the
        // case. The token (assumed to be a metrics variable name) can contain a
        // '.' like e.g. in 'user.max', which select the consolidation function
        // MAX. RRDTool does not allow a variable name to contain a '.', but
        // strangely enough, it allows an underscore. Therefore, we replace '.'
        // by '_' here.
        auto [var, cf] = getVarAndCF(token);
        auto location =
            _mc->metricLocation(host_name, service_description, var);
        std::string rrd_varname;
        if (location.path_.empty() || location.data_source_name_.empty()) {
            rrd_varname = replace_all(var.string(), ".", '_');
        } else {
            rrd_varname = "var_" + std::to_string(++next_variable_number);
            argv_s.push_back(std::string("DEF:")
                                 .append(rrd_varname)
                                 .append("=")
                                 .append(location.path_.string())
                                 .append(":")
                                 .append(location.data_source_name_)
                                 .append(":")
                                 .append(cf));
            touched_rrds.insert(location.path_.string());
        }
        converted_rpn += rrd_varname;
    }

    // Add the two commands for the actual export.
    argv_s.push_back("CDEF:xxx=" + converted_rpn);
    argv_s.emplace_back("XPORT:xxx:");

    // Make RRDTool flush the rrdcached if necessary

    // The cache daemon experiences long delays when queries extend over a
    // large time range and the underlying RRA are in high resolution.

    // For performance reasons the xport tool will not connect to the daemon
    // client to flush the data but will be done in 2 separate steps. First data
    // will be flush only. Then the xport tool will directly read the RRD file.

    // The performance issues with the cache daemon have been reported to
    // RRDTool on the issue
    // https://github.com/oetiker/rrdtool-1.x/issues/1062

    auto *logger = _mc->loggerRRD();
    const auto rrdcached_socket = _mc->paths()->rrdcached_socket();
    if (_mc->pnp4nagiosEnabled() && !rrdcached_socket.empty() &&
        !touched_rrds.empty()) {
        std::vector<std::string> daemon_argv_s{
            "rrdtool flushcached",  // name of program (ignored)
            "--daemon", rrdcached_socket};

        for (const auto &rrdfile : touched_rrds) {
            daemon_argv_s.push_back(rrdfile);
        }

        // Convert our dynamic C++ string array to a C-style argv array
        std::vector<const char *> daemon_argv;
        daemon_argv.reserve(daemon_argv_s.size());
        for (const auto &arg : daemon_argv_s) {
            daemon_argv.push_back(arg.c_str());
        }
        daemon_argv.push_back(nullptr);

        if (logger->isLoggable(LogLevel::debug)) {
            Debug debug(logger);
            debug << "flush RRD data:";
            for (const auto &arg : daemon_argv_s) {
                debug << " " << arg;
            }
        }

        if (rrd_flushcached(static_cast<int>(daemon_argv_s.size()),
                            const_cast<char **>(daemon_argv.data())) != 0) {
            Warning(logger) << "Error flushing RRD: " << rrd_get_error();
        }
    }

    // Convert our dynamic C++ string array to a C-style argv array
    std::vector<const char *> argv;
    argv.reserve(argv_s.size());
    for (const auto &arg : argv_s) {
        argv.push_back(arg.c_str());
    }
    argv.push_back(nullptr);

    if (logger->isLoggable(LogLevel::debug)) {
        Debug debug(logger);
        debug << "retrieving RRD data:";
        for (const auto &arg : argv_s) {
            debug << " " << arg;
        }
    }

    // Now do the actual export. The library function rrd_xport mimics the
    // command line API of rrd xport, but - fortunately - we get direct access
    // to a binary buffer with doubles. No parsing is required.
    int xxsize = 0;
    time_t start = 0;
    time_t end = 0;
    unsigned long step = 0;
    unsigned long col_cnt = 0;
    char **legend_v = nullptr;
    rrd_value_t *rrd_data = nullptr;

    // Clear the RRD error float. RRDTool will not do this and immediately fail
    // if an error already occurred.
    rrd_clear_error();

    Data data;
    if (rrd_xport(static_cast<int>(argv_s.size()),
                  const_cast<char **>(argv.data()), &xxsize, &start, &end,
                  &step, &col_cnt, &legend_v, &rrd_data) != 0) {
        Warning(logger) << "Error accessing RRD: " << rrd_get_error();
        return data.as_vector(timezone_offset);
    }

    // Since we have exactly one XPORT command, we expect exactly one column
    if (col_cnt != 1) {
        Error(logger) << "rrd_xport returned " << col_cnt
                      << " columns, but exactly one was expected.";
    } else {
        // XPORT takes a closed timewindow in its query and returns the
        // timestamped values that represent an intersection with the query
        // window. The returned interval description is right closed.

        // The timestamps associated with a value in RRDtool ALWAYS
        // represent the time the sample was taken. Since any value you
        // sample will represent some sort of past state your sampling
        // apparatus has gathered, the timestamp will always be at the end
        // of the sampling period

        // LEGEND
        // O timestamps of measurements
        // | query values, _start_time and _end_time
        // x returned start, no data contained
        // v returned data rows, includes end y

        // --O---O---O---O---O---O---O---O
        //         |---------------|
        //       x---v---v---v---v---y

        // Exact start time of the represented interval(x). This is <= our
        // _start_time(|), but no value is associated to this time.
        data.start = std::chrono::system_clock::from_time_t(start);
        // Time closing time of the interval(y). This is >= our _end_time, and
        // holds the last data value.
        data.end = std::chrono::system_clock::from_time_t(end);
        // Actual resolution in seconds. This is >= our _resolution
        data.step = step;
        // Now the actual data - double for double
        // Data rows represent past values, thus loop starts with step shift.
        // Interval is right closed, thus iterate until end inclusive.
        rrd_value_t *ptr = rrd_data;
        for (time_t ti = start + step; ti <= end; ti += step) {
            data.values.push_back(*ptr++);
        }
    }

    // rrd_xport uses malloc, so we *have* to use free.
    for (unsigned long j = 0; j < col_cnt; j++) {
        free(legend_v[j]);  // NOLINT
    }
    free(legend_v);  // NOLINT
    free(rrd_data);  // NOLINT
    return data.as_vector(timezone_offset);
}
