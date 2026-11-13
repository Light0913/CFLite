// common.hpp —— Windows 通用工具（UTF-8 转换、文件读写、NDJSON emit、verdict）。
#pragma once
#include <string>
#include <vector>

#include <windows.h>

namespace cflite {

// ---------------- verdict ----------------
enum class Ver {
    AC, WA, PE, RE, OLE, TLE, MLE, CE, SE, PRETESTS_PASSED, SKIP
};

inline const char* ver_str(Ver v) {
    switch (v) {
        case Ver::AC: return "AC";
        case Ver::WA: return "WA";
        case Ver::PE: return "PE";
        case Ver::RE: return "RE";
        case Ver::OLE: return "OLE";
        case Ver::TLE: return "TLE";
        case Ver::MLE: return "MLE";
        case Ver::CE: return "CE";
        case Ver::SE: return "SE";
        case Ver::PRETESTS_PASSED: return "PRETESTS_PASSED";
        default: return "SKIP";
    }
}

inline Ver ver_from_checker_exit(int code) {
    switch (code) {
        case 0: return Ver::AC;
        case 1: return Ver::WA;
        case 2: return Ver::PE;
        default: return Ver::SE;
    }
}

// ---------------- string conversion ----------------
std::wstring utf8_to_wide(const std::string& s);
std::string wide_to_utf8(const std::wstring& w);
std::string narrow_error(DWORD code);

// ---------------- file io ----------------
std::string read_file_bytes(const std::wstring& path, bool* ok = nullptr);
bool write_file_bytes(const std::wstring& path, const std::string& data);
bool file_exists(const std::wstring& path);
std::wstring path_join(const std::wstring& dir, const std::wstring& name);

// ---------------- NDJSON emit ----------------
// 直接写 stdout 一行完整 JSON 并 flush；日志走 stderr。
void emit_json(const std::string& json_line);
void log_info(const std::string& msg);
void log_error(const std::string& msg);

}  // namespace cflite
