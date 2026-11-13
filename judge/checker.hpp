// checker.hpp —— 结果比对（strict / testlib）。
#pragma once
#include <string>

#include "common.hpp"

namespace cflite {

struct CheckerConfig {
    std::string type = "default";  // default | testlib
    std::wstring bin;              // testlib checker.exe 绝对路径
    long long time_limit_ms = 3000;
    long long memory_limit_mb = 512;
};

struct CheckResult {
    Ver verdict = Ver::AC;
    std::string comment;
};

// 逐 token 严格比对：忽略 \r、行末空格、文件末换行
Ver strict_compare(const std::string& out, const std::string& ans);

// 运行 checker：default → strict 比对；testlib → 跑 checker.exe
CheckResult run_checker(const CheckerConfig& cfg, const std::wstring& input,
                        const std::wstring& output, const std::wstring& answer);

}  // namespace cflite
