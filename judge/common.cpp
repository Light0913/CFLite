// common.cpp
#include "common.hpp"

#include <iostream>

namespace cflite {

std::wstring utf8_to_wide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    if (n <= 0) return L"";
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

std::string wide_to_utf8(const std::wstring& w) {
    if (w.empty()) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    if (n <= 0) return "";
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
    return s;
}

std::string narrow_error(DWORD code) {
    LPWSTR buf = nullptr;
    DWORD n = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, code, 0, (LPWSTR)&buf, 0, nullptr);
    std::string out;
    if (n && buf) {
        out = wide_to_utf8(buf);
        LocalFree(buf);
    } else {
        out = "error code " + std::to_string(code);
    }
    while (!out.empty() && (out.back() == '\r' || out.back() == '\n' || out.back() == ' '))
        out.pop_back();
    return out;
}

std::string read_file_bytes(const std::wstring& path, bool* ok) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        if (ok) *ok = false;
        return "";
    }
    std::string data;
    char buf[8192];
    DWORD n = 0;
    while (ReadFile(h, buf, sizeof(buf), &n, nullptr) && n > 0) data.append(buf, n);
    CloseHandle(h);
    if (ok) *ok = true;
    return data;
}

bool write_file_bytes(const std::wstring& path, const std::string& data) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    BOOL ok = WriteFile(h, data.data(), (DWORD)data.size(), &written, nullptr);
    CloseHandle(h);
    return ok && written == data.size();
}

bool file_exists(const std::wstring& path) {
    DWORD attr = GetFileAttributesW(path.c_str());
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

std::wstring path_join(const std::wstring& dir, const std::wstring& name) {
    if (dir.empty()) return name;
    if (dir.back() == L'\\' || dir.back() == L'/') return dir + name;
    return dir + L"\\" + name;
}

void emit_json(const std::string& json_line) {
    std::cout << json_line << "\n";
    std::cout.flush();
}

void log_info(const std::string& msg) {
    std::cerr << "[judge] " << msg << "\n";
    std::cerr.flush();
}

void log_error(const std::string& msg) {
    std::cerr << "[judge][error] " << msg << "\n";
    std::cerr.flush();
}

}  // namespace cflite
