// sandbox.cpp —— Job Object + AppContainer 隔离。
#include "sandbox.hpp"

#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>

#include "appcontainer.hpp"
#include "common.hpp"

#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "userenv.lib")

namespace cflite {

static const long long OUTPUT_LIMIT = 16LL * 1024 * 1024;
static const long long STDERR_LIMIT = 4LL * 1024 * 1024;

namespace {
void read_pipe(HANDLE rd, std::string& buf, std::mutex& mu,
               std::atomic<bool>& overflow, long long limit) {
    char tmp[8192];
    while (true) {
        DWORD n = 0;
        if (!ReadFile(rd, tmp, sizeof(tmp), &n, nullptr) || n == 0) break;
        std::lock_guard<std::mutex> lk(mu);
        if ((long long)(buf.size() + n) > limit) {
            overflow = true;
            break;
        }
        buf.append(tmp, n);
    }
}
}  // namespace

RunResult run_in_sandbox(const RunOptions& opts) {
    RunResult res;

    // ---------- 1. Job Object ----------
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (!job) {
        res.verdict = Ver::SE;
        res.detail = "CreateJobObjectW: " + narrow_error(GetLastError());
        return res;
    }

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION lim = {};
    lim.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_TIME |
        JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY |
        JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
    lim.BasicLimitInformation.PerProcessUserTimeLimit.QuadPart = opts.time_limit_ms * 10000LL;  // 100ns
    SIZE_T mem_bytes = (SIZE_T)opts.memory_limit_mb * 1024 * 1024;
    lim.ProcessMemoryLimit = mem_bytes;
    lim.JobMemoryLimit = mem_bytes;
    if (opts.active_process_limit > 0) {
        lim.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        lim.BasicLimitInformation.ActiveProcessLimit = (DWORD)opts.active_process_limit;
    }
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &lim, sizeof(lim))) {
        CloseHandle(job);
        res.verdict = Ver::SE;
        res.detail = "SetInformationJobObject: " + narrow_error(GetLastError());
        return res;
    }
    // UI 限制（独立 info class，禁止弹窗/桌面交互）
    JOBOBJECT_BASIC_UI_RESTRICTIONS ui = {};
    ui.UIRestrictionsClass =
        JOB_OBJECT_UILIMIT_HANDLES | JOB_OBJECT_UILIMIT_DESKTOP |
        JOB_OBJECT_UILIMIT_GLOBALATOMS | JOB_OBJECT_UILIMIT_READCLIPBOARD |
        JOB_OBJECT_UILIMIT_WRITECLIPBOARD | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS |
        JOB_OBJECT_UILIMIT_DISPLAYSETTINGS | JOB_OBJECT_UILIMIT_EXITWINDOWS;
    SetInformationJobObject(job, JobObjectBasicUIRestrictions, &ui, sizeof(ui));

    // ---------- 2. AppContainer 白名单 ----------
    bool use_ac = opts.use_appcontainer && appcontainer_available();
    if (use_ac) {
        appcontainer_grant(appcontainer_sid(), opts.workdir, GENERIC_ALL);
        for (const auto& d : opts.readonly_dirs)
            appcontainer_grant(appcontainer_sid(), d, GENERIC_READ | GENERIC_EXECUTE);
    }

    // ---------- 3. 管道 ----------
    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = nullptr;

    HANDLE out_rd = nullptr, out_wr = nullptr;
    HANDLE err_rd = nullptr, err_wr = nullptr;
    if (!CreatePipe(&out_rd, &out_wr, &sa, 0) || !CreatePipe(&err_rd, &err_wr, &sa, 0)) {
        CloseHandle(job);
        res.verdict = Ver::SE;
        res.detail = "CreatePipe: " + narrow_error(GetLastError());
        return res;
    }
    SetHandleInformation(out_rd, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(err_rd, HANDLE_FLAG_INHERIT, 0);

    // stdin
    HANDLE in_handle = nullptr;
    std::wstring stdin_src = opts.stdin_file.empty() ? L"NUL" : opts.stdin_file;
    in_handle = CreateFileW(stdin_src.c_str(), GENERIC_READ, FILE_SHARE_READ, &sa,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (in_handle == INVALID_HANDLE_VALUE) {
        in_handle = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ, &sa,
                                OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    }

    // ---------- 4. STARTUPINFOEX + AppContainer 属性 ----------
    STARTUPINFOEXW si = {};
    si.StartupInfo.cb = sizeof(si);
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.StartupInfo.hStdInput = in_handle;
    si.StartupInfo.hStdOutput = out_wr;
    si.StartupInfo.hStdError = err_wr;
    si.StartupInfo.wShowWindow = SW_HIDE;

    SIZE_T attr_size = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &attr_size);
    si.lpAttributeList = (LPPROC_THREAD_ATTRIBUTE_LIST)HeapAlloc(GetProcessHeap(), 0, attr_size);
    if (si.lpAttributeList && !InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, &attr_size)) {
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
        si.lpAttributeList = nullptr;
    }
    SECURITY_CAPABILITIES caps = {};
    if (use_ac && si.lpAttributeList) {
        caps.AppContainerSid = appcontainer_sid();
        UpdateProcThreadAttribute(si.lpAttributeList, 0,
                                  PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                  &caps, sizeof(caps), nullptr, nullptr);
    }

    // ---------- 5. 环境变量 ----------
    std::wstring sysroot = L"C:\\Windows";
    wchar_t sr[256];
    if (GetEnvironmentVariableW(L"SystemRoot", sr, 256) > 0) sysroot = sr;
    std::wstring env = L"SystemRoot=" + sysroot;
    env.push_back(L'\0');
    if (!opts.env_path.empty()) { env += L"PATH=" + opts.env_path; env.push_back(L'\0'); }
    env += L"TEMP=" + opts.workdir; env.push_back(L'\0');
    env += L"TMP=" + opts.workdir; env.push_back(L'\0');
    env.push_back(L'\0');

    // ---------- 6. CreateProcess ----------
    std::wstring cmdline = L"\"" + opts.exe + L"\"";
    if (!opts.args.empty()) cmdline += L" " + opts.args;
    std::wstring cmd = cmdline;  // 可被修改的缓冲

    DWORD flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED |
                  CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB |
                  CREATE_UNICODE_ENVIRONMENT;
    PROCESS_INFORMATION pi = {};
    BOOL created = CreateProcessW(opts.exe.c_str(), cmd.data(), nullptr, nullptr, TRUE,
                                  flags, (LPVOID)env.data(), opts.workdir.c_str(),
                                  &si.StartupInfo, &pi);
    if (!created) {
        // 若 breakaway 被父 job 拒绝，重试一次不带 breakaway
        flags &= ~CREATE_BREAKAWAY_FROM_JOB;
        created = CreateProcessW(opts.exe.c_str(), cmd.data(), nullptr, nullptr, TRUE,
                                 flags, (LPVOID)env.data(), opts.workdir.c_str(),
                                 &si.StartupInfo, &pi);
    }
    if (!created) {
        DWORD e = GetLastError();
        CloseHandle(out_rd); CloseHandle(out_wr);
        CloseHandle(err_rd); CloseHandle(err_wr);
        CloseHandle(in_handle);
        if (si.lpAttributeList) {
            DeleteProcThreadAttributeList(si.lpAttributeList);
            HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
        }
        CloseHandle(job);
        res.verdict = Ver::SE;
        res.detail = "CreateProcessW(" + wide_to_utf8(opts.exe) + ")[" + std::to_string((long)e) +
                     "]: " + narrow_error(e);
        return res;
    }

    CloseHandle(in_handle);
    CloseHandle(out_wr);
    CloseHandle(err_wr);
    if (si.lpAttributeList) {
        DeleteProcThreadAttributeList(si.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    }

    // 分配进 Job（创建时已 SUSPENDED，先分配再运行，防止逃逸）
    AssignProcessToJobObject(job, pi.hProcess);
    ResumeThread(pi.hThread);

    // ---------- 7. 读管道 + 等待 ----------
    std::string out_data, err_data;
    std::mutex out_mu, err_mu;
    std::atomic<bool> out_overflow{false}, err_overflow{false};
    std::thread out_t(read_pipe, out_rd, std::ref(out_data), std::ref(out_mu),
                      std::ref(out_overflow), OUTPUT_LIMIT);
    std::thread err_t(read_pipe, err_rd, std::ref(err_data), std::ref(err_mu),
                      std::ref(err_overflow), STDERR_LIMIT);

    long long wall_limit_ms = opts.time_limit_ms * 2 + 1000;
    auto start = std::chrono::steady_clock::now();
    bool exited = false;
    while (true) {
        DWORD r = WaitForSingleObject(pi.hProcess, 50);
        if (r == WAIT_OBJECT_0) { exited = true; break; }
        if (out_overflow || err_overflow) {
            TerminateJobObject(job, 1);
            WaitForSingleObject(pi.hProcess, 2000);
            res.verdict = Ver::OLE;
            break;
        }
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                           std::chrono::steady_clock::now() - start).count();
        if (elapsed > wall_limit_ms) {
            TerminateJobObject(job, 1);
            WaitForSingleObject(pi.hProcess, 2000);
            res.verdict = Ver::TLE;
            break;
        }
    }
    out_t.join();
    err_t.join();
    CloseHandle(out_rd);
    CloseHandle(err_rd);

    // ---------- 8. 统计 ----------
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION acc = {};
    if (QueryInformationJobObject(job, JobObjectBasicAccountingInformation,
                                  &acc, sizeof(acc), nullptr)) {
        long long user_ms = acc.TotalUserTime.QuadPart / 10000;
        long long kernel_ms = acc.TotalKernelTime.QuadPart / 10000;
        res.time_ms = user_ms + kernel_ms;
    }
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION elim = {};
    if (QueryInformationJobObject(job, JobObjectExtendedLimitInformation,
                                  &elim, sizeof(elim), nullptr)) {
        res.memory_kb = (long long)(elim.PeakProcessMemoryUsed / 1024);
    }

    DWORD exit_code = 0;
    if (pi.hProcess) {
        GetExitCodeProcess(pi.hProcess, &exit_code);
        res.exit_code = (int)exit_code;
    }

    res.stdout_data = out_data;
    res.stderr_data = err_data;
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    CloseHandle(job);

    // ---------- 9. 最终判定 ----------
    if (res.verdict != Ver::OLE && res.verdict != Ver::TLE) {
        long long user_ms = acc.TotalUserTime.QuadPart / 10000;
        bool cpu_tle = (user_ms > opts.time_limit_ms) || (res.exit_code == 0xC000013A);
        if (res.exit_code == 0xC000013A || cpu_tle) {
            res.verdict = Ver::TLE;
        } else if (res.memory_kb >= opts.memory_limit_mb * 1024) {
            res.verdict = Ver::MLE;
        } else if (res.exit_code != 0) {
            res.verdict = Ver::RE;
        } else {
            res.verdict = Ver::AC;
        }
    }
    return res;
}

}  // namespace cflite
