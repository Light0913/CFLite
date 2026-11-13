// sandbox.hpp —— 进程沙箱（Job Object + 可选 AppContainer）。
#pragma once
#include <string>
#include <vector>

#include <windows.h>

#include "common.hpp"

namespace cflite {

struct RunOptions {
    std::wstring exe;                  // 可执行文件绝对路径
    std::wstring args;                 // 追加命令行参数（不含 exe 本身）
    std::wstring workdir;              // 工作目录
    std::wstring stdin_file;           // 可选：stdin 重定向源文件
    long long time_limit_ms = 1000;
    long long memory_limit_mb = 256;
    long long active_process_limit = 1;  // 0 = 不限制（编译需要多个子进程）
    bool use_appcontainer = true;
    std::vector<std::wstring> readonly_dirs;  // AppContainer 只读白名单
    std::wstring env_path;                    // 追加到 PATH（编译时需要 mingw bin）
};

struct RunResult {
    Ver verdict = Ver::AC;
    long long time_ms = 0;
    long long memory_kb = 0;
    int exit_code = 0;
    std::string stdout_data;
    std::string stderr_data;
    std::string detail;
};

RunResult run_in_sandbox(const RunOptions& opts);

}  // namespace cflite
