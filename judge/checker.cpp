// checker.cpp
#include "checker.hpp"

#include <sstream>
#include <vector>

#include "common.hpp"
#include "sandbox.hpp"

namespace cflite {

namespace {
std::vector<std::string> tokenize(const std::string& s) {
    std::vector<std::string> v;
    std::string cur;
    for (char c : s) {
        if (c == '\r') continue;
        if (c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f') {
            if (!cur.empty()) { v.push_back(cur); cur.clear(); }
        } else {
            cur += c;
        }
    }
    if (!cur.empty()) v.push_back(cur);
    return v;
}
}  // namespace

Ver strict_compare(const std::string& out, const std::string& ans) {
    return tokenize(out) == tokenize(ans) ? Ver::AC : Ver::WA;
}

CheckResult run_checker(const CheckerConfig& cfg, const std::wstring& input,
                        const std::wstring& output, const std::wstring& answer) {
    CheckResult r;
    if (cfg.type != "testlib" || cfg.bin.empty()) {
        r.verdict = strict_compare(read_file_bytes(output), read_file_bytes(answer));
        return r;
    }

    RunOptions opts;
    opts.exe = cfg.bin;
    // testlib checker 标准参数：<input> <output> <answer>
    opts.args = L"\"" + input + L"\" \"" + output + L"\" \"" + answer + L"\"";
    opts.workdir = cfg.bin.substr(0, cfg.bin.find_last_of(L"\\/"));
    opts.time_limit_ms = cfg.time_limit_ms;
    opts.memory_limit_mb = cfg.memory_limit_mb;
    opts.active_process_limit = 0;
    opts.use_appcontainer = false;  // checker 以 Medium IL 运行

    RunResult rr = run_in_sandbox(opts);
    r.verdict = ver_from_checker_exit(rr.exit_code);
    if (!rr.stderr_data.empty())
        r.comment = rr.stderr_data;
    if (rr.verdict == Ver::SE) {
        r.verdict = Ver::SE;
        r.comment = rr.detail;
    }
    return r;
}

}  // namespace cflite
