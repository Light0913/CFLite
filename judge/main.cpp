// main.cpp —— judge.exe 主流程。
// 从 stdin 读取单个 JSON 任务，向 stdout 输出 NDJSON 事件（event: test_start/test_done/compile_error/final）。
#include <atomic>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

#include <windows.h>

#include "appcontainer.hpp"
#include "checker.hpp"
#include "common.hpp"
#include "json.hpp"
#include "sandbox.hpp"

namespace fs = std::filesystem;
using namespace cflite;

// ---------------- 任务结构 ----------------
struct Job {
    long long submission_id = 0;
    std::string attempt_id;
    long long problem_id = 0;
    std::string type = "default";
    std::string language = "cpp23";
    std::string problem_dir, testdata_dir, source_path;
    long long time_limit_ms = 1000;
    long long memory_limit_mb = 256;
    long long compile_time_limit_ms = 10000;
    std::string gxx;
    std::vector<std::string> mingw_dirs;
    std::vector<std::string> attachments;
    CheckerConfig checker;
    std::string interactor_bin;
    Json cases;
    std::string run_stage = "pretests";
    std::string temp_dir;
};

static const char* std_flag(const std::string& lang) {
    if (lang == "cpp11") return "-std=c++11";
    if (lang == "cpp14") return "-std=c++14";
    if (lang == "cpp17") return "-std=c++17";
    if (lang == "cpp20") return "-std=c++20";
    if (lang == "cpp23") return "-std=c++23";
    return "-std=c++23";
}

static Job parse_job(const std::string& raw) {
    Json j = Json::parse(raw);
    Job job;
    job.submission_id = j.get("submission_id", Json(0)).as_int();
    job.attempt_id = j.get("attempt_id", Json("")).as_string();
    job.problem_id = j.get("problem_id", Json(0)).as_int();
    job.type = j.get("type", Json("default")).as_string();
    job.language = j.get("language", Json("cpp23")).as_string();
    job.problem_dir = j.get("problem_dir", Json("")).as_string();
    job.testdata_dir = j.get("testdata_dir", Json("")).as_string();
    job.source_path = j.get("source_path", Json("")).as_string();
    job.time_limit_ms = j.get("time_limit_ms", Json(1000)).as_int();
    job.memory_limit_mb = j.get("memory_limit_mb", Json(256)).as_int();
    job.compile_time_limit_ms = j.get("compile_time_limit_ms", Json(10000)).as_int();
    job.gxx = j.get("gxx", Json("")).as_string();
    job.interactor_bin = j.get("interactor", Json(Json::Object())).get("bin", Json("")).as_string();
    job.run_stage = j.get("run_stage", Json("pretests")).as_string();
    job.temp_dir = j.get("temp_dir", Json("")).as_string();
    if (j.contains("checker") && j["checker"].is_object()) {
        const Json& c = j["checker"];
        job.checker.type = c.get("type", Json("default")).as_string();
        job.checker.bin = utf8_to_wide(c.get("bin", Json("")).as_string());
        job.checker.time_limit_ms = c.get("time_limit_ms", Json(3000)).as_int();
        job.checker.memory_limit_mb = c.get("memory_limit_mb", Json(512)).as_int();
    }
    for (const auto& d : j.get("mingw_dirs", Json(Json::Array())).as_array())
        job.mingw_dirs.push_back(d.as_string());
    for (const auto& d : j.get("attachments", Json(Json::Array())).as_array())
        job.attachments.push_back(d.as_string());
    job.cases = j.get("cases", Json(Json::Object()));
    return job;
}

static Json case_list(const Job& job) {
    return job.cases.get(job.run_stage, Json(Json::Array()));
}

// case 路径：绝对路径直接用，否则拼到 testdata 目录下（Hack 输入用绝对路径）
static std::wstring resolve_case_path(const std::wstring& testdata, const std::string& name) {
    std::wstring w = utf8_to_wide(name);
    if (w.size() >= 2 && w[1] == L':') return w;
    if (w.size() >= 2 && w[0] == L'\\' && w[1] == L'\\') return w;
    return path_join(testdata, w);
}

// ---------------- 编译 ----------------
struct CompileResult {
    bool ok = false;
    std::wstring exe;
    std::string err_text;
};

static CompileResult compile_source(const Job& job, const std::wstring& workdir) {
    CompileResult r;
    r.exe = path_join(workdir, L"main.exe");
    std::string args = std::string("-O2 ") + std_flag(job.language) +
                       " -static -static-libgcc -static-libstdc++ \"" + job.source_path +
                       "\" -o \"" + wide_to_utf8(r.exe) + "\"";

    RunOptions opts;
    opts.exe = utf8_to_wide(job.gxx);
    opts.args = utf8_to_wide(args);
    opts.workdir = workdir;
    opts.time_limit_ms = job.compile_time_limit_ms;
    opts.memory_limit_mb = 1024;
    opts.active_process_limit = 0;  // g++ 需要多个子进程
    opts.use_appcontainer = true;
    for (auto& d : job.mingw_dirs) opts.readonly_dirs.push_back(utf8_to_wide(d));
    opts.env_path = utf8_to_wide(job.mingw_dirs.empty() ? "" : (job.mingw_dirs[2] + ";" + job.mingw_dirs[3]));

    RunResult rr = run_in_sandbox(opts);
    if (rr.exit_code == 0 && file_exists(r.exe)) {
        r.ok = true;
        return r;
    }
    r.err_text = rr.stderr_data.empty() ? rr.stdout_data : rr.stderr_data;
    if (r.err_text.empty()) r.err_text = rr.detail;
    return r;
}

// ---------------- default：单进程运行 ----------------
struct RunCaseResult {
    Ver verdict = Ver::AC;
    long long time_ms = 0;
    long long memory_kb = 0;
    std::string output;
    std::string checker_comment;
};

static RunCaseResult run_default_case(const Job& job, const std::wstring& workdir,
                                      const std::wstring& exe, const Json& case_) {
    RunCaseResult r;
    std::wstring testdata = utf8_to_wide(job.testdata_dir);
    std::wstring in_path = resolve_case_path(testdata, case_["in"].as_string());
    std::wstring out_path = resolve_case_path(testdata, case_["out"].as_string());

    RunOptions opts;
    opts.exe = exe;
    opts.workdir = workdir;
    opts.stdin_file = in_path;
    opts.time_limit_ms = job.time_limit_ms;
    opts.memory_limit_mb = job.memory_limit_mb;
    opts.active_process_limit = 1;
    opts.use_appcontainer = true;
    for (auto& d : job.attachments) opts.readonly_dirs.push_back(utf8_to_wide(d));

    RunResult rr = run_in_sandbox(opts);
    r.time_ms = rr.time_ms;
    r.memory_kb = rr.memory_kb;
    r.output = rr.stdout_data;
    if (rr.verdict != Ver::AC) {
        r.verdict = rr.verdict;
        if (!rr.detail.empty()) r.checker_comment = rr.detail;
        return r;
    }

    // 运行成功，跑 checker
    std::wstring out_file = path_join(workdir, L"contestant.out");
    write_file_bytes(out_file, r.output);
    CheckResult cr = run_checker(job.checker, in_path, out_file, out_path);
    r.verdict = cr.verdict;
    r.checker_comment = cr.comment;
    return r;
}

// ---------------- 管道式双进程（interactive / communication） ----------------
struct Spawned {
    HANDLE process = nullptr;
    HANDLE thread = nullptr;
    HANDLE job = nullptr;
    bool ok = false;
    std::string err;
};

static Spawned spawn_piped(const std::wstring& exe, const std::wstring& args,
                           const std::wstring& workdir,
                           HANDLE h_stdin, HANDLE h_stdout, HANDLE h_stderr,
                           long long time_ms, long long mem_mb, int proc_limit,
                           bool appcontainer, const std::vector<std::wstring>& readonly_dirs) {
    Spawned s;
    s.job = CreateJobObjectW(nullptr, nullptr);
    if (!s.job) { s.err = "CreateJobObjectW failed"; return s; }
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION lim = {};
    lim.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_TIME |
        JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY |
        JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION;
    lim.BasicLimitInformation.PerProcessUserTimeLimit.QuadPart = time_ms * 10000LL;
    lim.ProcessMemoryLimit = (SIZE_T)mem_mb * 1024 * 1024;
    lim.JobMemoryLimit = (SIZE_T)mem_mb * 1024 * 1024;
    if (proc_limit > 0) {
        lim.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        lim.BasicLimitInformation.ActiveProcessLimit = (DWORD)proc_limit;
    }
    SetInformationJobObject(s.job, JobObjectExtendedLimitInformation, &lim, sizeof(lim));
    JOBOBJECT_BASIC_UI_RESTRICTIONS ui = {};
    ui.UIRestrictionsClass =
        JOB_OBJECT_UILIMIT_HANDLES | JOB_OBJECT_UILIMIT_DESKTOP |
        JOB_OBJECT_UILIMIT_GLOBALATOMS | JOB_OBJECT_UILIMIT_READCLIPBOARD |
        JOB_OBJECT_UILIMIT_WRITECLIPBOARD | JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS |
        JOB_OBJECT_UILIMIT_DISPLAYSETTINGS | JOB_OBJECT_UILIMIT_EXITWINDOWS;
    SetInformationJobObject(s.job, JobObjectBasicUIRestrictions, &ui, sizeof(ui));

    if (appcontainer && appcontainer_available()) {
        appcontainer_grant(appcontainer_sid(), workdir, GENERIC_ALL);
        for (auto& d : readonly_dirs) appcontainer_grant(appcontainer_sid(), d, GENERIC_READ | GENERIC_EXECUTE);
    }

    STARTUPINFOEXW si = {};
    si.StartupInfo.cb = sizeof(si);
    si.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.StartupInfo.hStdInput = h_stdin;
    si.StartupInfo.hStdOutput = h_stdout;
    si.StartupInfo.hStdError = h_stderr;
    si.StartupInfo.wShowWindow = SW_HIDE;

    // 属性：SECURITY_CAPABILITIES + HANDLE_LIST
    const int ATTR_COUNT = 2;
    SIZE_T sz = 0;
    InitializeProcThreadAttributeList(nullptr, ATTR_COUNT, 0, &sz);
    si.lpAttributeList = (LPPROC_THREAD_ATTRIBUTE_LIST)HeapAlloc(GetProcessHeap(), 0, sz);
    if (!si.lpAttributeList || !InitializeProcThreadAttributeList(si.lpAttributeList, ATTR_COUNT, 0, &sz)) {
        if (si.lpAttributeList) { HeapFree(GetProcessHeap(), 0, si.lpAttributeList); si.lpAttributeList = nullptr; }
    }
    if (si.lpAttributeList) {
        SECURITY_CAPABILITIES caps = {};
        if (appcontainer && appcontainer_available()) {
            caps.AppContainerSid = appcontainer_sid();
            UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                      &caps, sizeof(caps), nullptr, nullptr);
        }
        HANDLE handles[3] = {h_stdin, h_stdout, h_stderr};
        UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                  handles, sizeof(handles), nullptr, nullptr);
    }

    std::wstring sysroot = L"C:\\Windows";
    wchar_t sr[256];
    if (GetEnvironmentVariableW(L"SystemRoot", sr, 256) > 0) sysroot = sr;
    std::wstring env = L"SystemRoot=" + sysroot;
    env.push_back(L'\0');
    env += L"TEMP=" + workdir; env.push_back(L'\0');
    env += L"TMP=" + workdir; env.push_back(L'\0');
    env.push_back(L'\0');

    std::wstring cmdline = L"\"" + exe + L"\"";
    if (!args.empty()) cmdline += L" " + args;
    std::wstring cmd = cmdline;

    DWORD flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW |
                  CREATE_BREAKAWAY_FROM_JOB | CREATE_UNICODE_ENVIRONMENT;
    PROCESS_INFORMATION pi = {};
    BOOL created = CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, TRUE, flags,
                                  (LPVOID)env.data(), workdir.c_str(), &si.StartupInfo, &pi);
    if (!created) {
        flags &= ~CREATE_BREAKAWAY_FROM_JOB;
        created = CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, TRUE, flags,
                                 (LPVOID)env.data(), workdir.c_str(), &si.StartupInfo, &pi);
    }
    if (si.lpAttributeList) {
        DeleteProcThreadAttributeList(si.lpAttributeList);
        HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
    }
    if (!created) {
        s.err = "CreateProcessW: " + narrow_error(GetLastError());
        CloseHandle(s.job); s.job = nullptr;
        return s;
    }
    AssignProcessToJobObject(s.job, pi.hProcess);
    ResumeThread(pi.hThread);
    s.process = pi.hProcess;
    s.thread = pi.hThread;
    s.ok = true;
    return s;
}

static void query_job_stats(HANDLE job, long long& time_ms, long long& memory_kb) {
    time_ms = 0; memory_kb = 0;
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION acc = {};
    if (QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &acc, sizeof(acc), nullptr))
        time_ms = (acc.TotalUserTime.QuadPart + acc.TotalKernelTime.QuadPart) / 10000;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION elim = {};
    if (QueryInformationJobObject(job, JobObjectExtendedLimitInformation, &elim, sizeof(elim), nullptr))
        memory_kb = (long long)(elim.PeakProcessMemoryUsed / 1024);
}

// interactive：interactor ↔ 选手
static RunCaseResult run_interactive_case(const Job& job, const std::wstring& workdir,
                                          const std::wstring& exe, const Json& case_) {
    RunCaseResult r;
    std::wstring testdata = utf8_to_wide(job.testdata_dir);
    std::wstring in_path = resolve_case_path(testdata, case_["in"].as_string());
    std::wstring ans_path = resolve_case_path(testdata, case_["out"].as_string());

    SECURITY_ATTRIBUTES sa; sa.nLength = sizeof(sa); sa.bInheritHandle = TRUE; sa.lpSecurityDescriptor = nullptr;
    HANDLE r1 = nullptr, w1 = nullptr, r2 = nullptr, w2 = nullptr;  // r1/w1: interactor→sol；r2/w2: sol→interactor
    if (!CreatePipe(&r1, &w1, &sa, 0) || !CreatePipe(&r2, &w2, &sa, 0)) {
        r.verdict = Ver::SE; r.checker_comment = "CreatePipe failed"; return r;
    }

    // interactor：Medium IL（不进 AppContainer），stdin=sol stdout，stdout=sol stdin
    Spawned intr = spawn_piped(utf8_to_wide(job.interactor_bin),
                               L"\"" + in_path + L"\" \"" + ans_path + L"\"",
                               utf8_to_wide(job.testdata_dir), r2, w1, GetStdHandle(STD_ERROR_HANDLE),
                               std::max<long long>(5000, job.time_limit_ms * 2), 1024, 0, false, {});
    // 选手：AppContainer，stdin=interactor stdout，stdout=interactor stdin
    std::vector<std::wstring> rd;
    for (auto& d : job.attachments) rd.push_back(utf8_to_wide(d));
    Spawned sol = spawn_piped(exe, L"", workdir, r1, w2, GetStdHandle(STD_ERROR_HANDLE),
                              job.time_limit_ms, job.memory_limit_mb, 1, true, rd);

    CloseHandle(r1); CloseHandle(w1); CloseHandle(r2); CloseHandle(w2);
    if (!intr.ok || !sol.ok) {
        if (sol.process) { TerminateJobObject(sol.job, 1); CloseHandle(sol.process); CloseHandle(sol.thread); CloseHandle(sol.job); }
        if (intr.process) { TerminateJobObject(intr.job, 1); CloseHandle(intr.process); CloseHandle(intr.thread); CloseHandle(intr.job); }
        r.verdict = Ver::SE; r.checker_comment = intr.err.empty() ? sol.err : intr.err; return r;
    }

    // 先等选手（含超时）
    long long wall = job.time_limit_ms * 2 + 1000;
    DWORD wr = WaitForSingleObject(sol.process, (DWORD)wall);
    if (wr == WAIT_TIMEOUT) {
        TerminateJobObject(sol.job, 1);
        TerminateJobObject(intr.job, 1);
        WaitForSingleObject(sol.process, 2000); WaitForSingleObject(intr.process, 2000);
        r.verdict = Ver::TLE;
        query_job_stats(sol.job, r.time_ms, r.memory_kb);
        CloseHandle(sol.process); CloseHandle(sol.thread); CloseHandle(sol.job);
        CloseHandle(intr.process); CloseHandle(intr.thread); CloseHandle(intr.job);
        return r;
    }
    // 选手已退出，判定
    query_job_stats(sol.job, r.time_ms, r.memory_kb);
    DWORD sol_code = 0; GetExitCodeProcess(sol.process, &sol_code);
    CloseHandle(sol.process); CloseHandle(sol.thread); CloseHandle(sol.job);
    if (sol_code != 0) {
        r.verdict = (r.time_ms > job.time_limit_ms) ? Ver::TLE :
                    (r.memory_kb >= job.memory_limit_mb * 1024) ? Ver::MLE : Ver::RE;
        TerminateJobObject(intr.job, 1); WaitForSingleObject(intr.process, 2000);
        CloseHandle(intr.process); CloseHandle(intr.thread); CloseHandle(intr.job);
        return r;
    }
    // 等 interactor 收尾
    DWORD wr2 = WaitForSingleObject(intr.process, (DWORD)wall);
    if (wr2 == WAIT_TIMEOUT) {
        TerminateJobObject(intr.job, 1); WaitForSingleObject(intr.process, 2000);
        r.verdict = Ver::TLE;
        CloseHandle(intr.process); CloseHandle(intr.thread); CloseHandle(intr.job);
        return r;
    }
    DWORD icode = 0; GetExitCodeProcess(intr.process, &icode);
    CloseHandle(intr.process); CloseHandle(intr.thread); CloseHandle(intr.job);
    r.verdict = ver_from_checker_exit((int)icode);
    return r;
}

// communication：同一源码双实例 --role=alice/bob
static RunCaseResult run_communication_case(const Job& job, const std::wstring& workdir,
                                            const std::wstring& exe, const Json& case_) {
    RunCaseResult r;
    std::wstring testdata = utf8_to_wide(job.testdata_dir);
    std::wstring in_path = resolve_case_path(testdata, case_["in"].as_string());
    std::wstring ans_path = resolve_case_path(testdata, case_["out"].as_string());

    // alice stdout → bob stdin（r1/w1）；bob stdout → alice stdin（r2/w2）
    SECURITY_ATTRIBUTES sa; sa.nLength = sizeof(sa); sa.bInheritHandle = TRUE; sa.lpSecurityDescriptor = nullptr;
    HANDLE r1 = nullptr, w1 = nullptr, r2 = nullptr, w2 = nullptr;
    if (!CreatePipe(&r1, &w1, &sa, 0) || !CreatePipe(&r2, &w2, &sa, 0)) {
        r.verdict = Ver::SE; r.checker_comment = "CreatePipe failed"; return r;
    }

    std::vector<std::wstring> rd;
    for (auto& d : job.attachments) rd.push_back(utf8_to_wide(d));
    // alice：stdin = bob 的输出；stdout = bob 的输入；argv: --role=alice <input> <merged_out>
    std::wstring merged_out = path_join(workdir, L"merged.out");
    Spawned alice = spawn_piped(exe, L"--role=alice \"" + in_path + L"\" \"" + merged_out + L"\"",
                                workdir, r2, w1, GetStdHandle(STD_ERROR_HANDLE),
                                job.time_limit_ms, job.memory_limit_mb, 1, true, rd);
    Spawned bob = spawn_piped(exe, L"--role=bob \"" + in_path + L"\"",
                              workdir, r1, w2, GetStdHandle(STD_ERROR_HANDLE),
                              job.time_limit_ms, job.memory_limit_mb, 1, true, rd);
    CloseHandle(r1); CloseHandle(w1); CloseHandle(r2); CloseHandle(w2);

    if (!alice.ok || !bob.ok) {
        if (alice.process) { TerminateJobObject(alice.job, 1); CloseHandle(alice.process); CloseHandle(alice.thread); CloseHandle(alice.job); }
        if (bob.process) { TerminateJobObject(bob.job, 1); CloseHandle(bob.process); CloseHandle(bob.thread); CloseHandle(bob.job); }
        r.verdict = Ver::SE; r.checker_comment = alice.err.empty() ? bob.err : alice.err; return r;
    }

    long long wall = job.time_limit_ms * 2 + 1000;
    HANDLE hs[2] = {alice.process, bob.process};
    DWORD wr = WaitForMultipleObjects(2, hs, TRUE, (DWORD)wall);
    if (wr == WAIT_TIMEOUT) {
        TerminateJobObject(alice.job, 1); TerminateJobObject(bob.job, 1);
        WaitForSingleObject(alice.process, 2000); WaitForSingleObject(bob.process, 2000);
        r.verdict = Ver::TLE;
        CloseHandle(alice.process); CloseHandle(alice.thread); CloseHandle(alice.job);
        CloseHandle(bob.process); CloseHandle(bob.thread); CloseHandle(bob.job);
        return r;
    }
    long long t1 = 0, m1 = 0;
    query_job_stats(alice.job, t1, m1);
    query_job_stats(bob.job, t1, m1);
    r.time_ms = t1; r.memory_kb = m1;
    DWORD c1 = 0, c2 = 0;
    GetExitCodeProcess(alice.process, &c1); GetExitCodeProcess(bob.process, &c2);
    CloseHandle(alice.process); CloseHandle(alice.thread); CloseHandle(alice.job);
    CloseHandle(bob.process); CloseHandle(bob.thread); CloseHandle(bob.job);
    if (c1 != 0 || c2 != 0) {
        r.verdict = (r.time_ms > job.time_limit_ms) ? Ver::TLE :
                    (r.memory_kb >= job.memory_limit_mb * 1024) ? Ver::MLE : Ver::RE;
        return r;
    }
    // 有 interactor → interactor 判；否则 checker 比对 alice 写出的 merged.out
    if (!job.interactor_bin.empty()) {
        // interactor 读输入与 merged 输出做判定
        RunOptions iopts;
        iopts.exe = utf8_to_wide(job.interactor_bin);
        iopts.args = L"\"" + in_path + L"\" \"" + merged_out + L"\"";
        iopts.workdir = utf8_to_wide(job.testdata_dir);
        iopts.time_limit_ms = std::max<long long>(5000, job.time_limit_ms * 2);
        iopts.memory_limit_mb = 1024;
        iopts.active_process_limit = 0;
        iopts.use_appcontainer = false;
        RunResult ir = run_in_sandbox(iopts);
        r.verdict = ver_from_checker_exit(ir.exit_code);
        if (!ir.stderr_data.empty()) r.checker_comment = ir.stderr_data;
        return r;
    }
    CheckResult cr = run_checker(job.checker, in_path, merged_out, ans_path);
    r.verdict = cr.verdict; r.checker_comment = cr.comment;
    return r;
}

// ---------------- 主流程 ----------------
static void emit_test_done(const std::string& name, const RunCaseResult& r) {
    Json ev;
    ev.set("event", Json("test_done"));
    ev.set("name", Json(name));
    ev.set("verdict", Json(ver_str(r.verdict)));
    ev.set("time_ms", Json(r.time_ms));
    ev.set("memory_kb", Json(r.memory_kb));
    if (!r.checker_comment.empty()) ev.set("checker_comment", Json(r.checker_comment));
    emit_json(ev.dump());
}

static void emit_final(const Job& job, const std::string& verdict, const Json& tests,
                       long long passed, long long total, const std::string& failed_test,
                       long long time_ms, long long memory_kb, const std::string& comment) {
    Json ev;
    ev.set("event", Json("final"));
    ev.set("stage", Json(job.run_stage));
    ev.set("verdict", Json(verdict));
    ev.set("tests", tests);
    ev.set("passed", Json(passed));
    ev.set("total", Json(total));
    if (!failed_test.empty()) ev.set("failed_test", Json(failed_test));
    ev.set("time_ms", Json(time_ms));
    ev.set("memory_kb", Json(memory_kb));
    if (!comment.empty()) ev.set("checker_comment", Json(comment));
    emit_json(ev.dump());
}

int main() {
    SetConsoleOutputCP(CP_UTF8);

    // 读取整个 stdin 作为任务 JSON
    std::string raw((std::istreambuf_iterator<char>(std::cin)),
                    std::istreambuf_iterator<char>());
    if (raw.empty()) {
        emit_json(R"({"event":"final","stage":"","verdict":"SE","tests":[],"passed":0,"total":0,"checker_comment":"empty job"})");
        return 0;
    }

    Job job;
    try {
        job = parse_job(raw);
    } catch (const std::exception& e) {
        emit_json(std::string("{\"event\":\"final\",\"stage\":\"\",\"verdict\":\"SE\",\"tests\":[],\"passed\":0,\"total\":0,\"checker_comment\":\"parse: ") +
                  e.what() + "\"}");
        return 0;
    }

    appcontainer_init(L"oj-judge");

    Json cases = case_list(job);
    // 空 cases 处理
    if (cases.is_array() && cases.as_array().empty()) {
        std::string v = (job.run_stage == "pretests") ? "PRETESTS_PASSED" : "AC";
        emit_final(job, v, Json(Json::Array()), 0, 0, "", 0, 0, "");
        appcontainer_cleanup();
        return 0;
    }

    // 编译
    std::wstring base_workdir = utf8_to_wide(job.temp_dir);
    std::wstring build_dir = path_join(base_workdir, L"build");
    fs::create_directories(build_dir);
    CompileResult comp = compile_source(job, build_dir);
    if (!comp.ok) {
        Json ce;
        ce.set("event", Json("compile_error"));
        ce.set("stderr", Json(comp.err_text.substr(0, 4096)));
        emit_json(ce.dump());
        emit_final(job, "CE", Json(Json::Array()), 0, cases.size(), "", 0, 0, "");
        appcontainer_cleanup();
        return 0;
    }

    Json tests = Json(Json::Array());
    long long max_time = 0, max_mem = 0;
    long long passed = 0;
    const auto& arr = cases.as_array();
    for (size_t i = 0; i < arr.size(); ++i) {
        const Json& c = arr[i];
        std::string name = c.get("name", Json(std::to_string(i + 1))).as_string();
        Json ts;
        ts.set("event", Json("test_start"));
        ts.set("name", Json(name));
        ts.set("index", Json((long long)i));
        emit_json(ts.dump());

        std::wstring case_dir = path_join(base_workdir, L"case_" + utf8_to_wide(std::to_string(i)));
        fs::create_directories(case_dir);

        RunCaseResult r;
        if (job.type == "interactive") {
            r = run_interactive_case(job, case_dir, comp.exe, c);
        } else if (job.type == "communication") {
            r = run_communication_case(job, case_dir, comp.exe, c);
        } else {
            r = run_default_case(job, case_dir, comp.exe, c);
        }

        max_time = std::max(max_time, r.time_ms);
        max_mem = std::max(max_mem, r.memory_kb);

        Json t;
        t.set("name", Json(name));
        t.set("verdict", Json(ver_str(r.verdict)));
        t.set("time_ms", Json(r.time_ms));
        t.set("memory_kb", Json(r.memory_kb));
        if (!r.checker_comment.empty()) t.set("checker_comment", Json(r.checker_comment));
        tests.push_back(t);

        emit_test_done(name, r);

        if (r.verdict != Ver::AC) {
            emit_final(job, ver_str(r.verdict), tests, (long long)i, arr.size(), name,
                       max_time, max_mem, r.checker_comment);
            appcontainer_cleanup();
            return 0;
        }
        passed = (long long)i + 1;
    }

    std::string v = (job.run_stage == "pretests") ? "PRETESTS_PASSED" : "AC";
    emit_final(job, v, tests, passed, arr.size(), "", max_time, max_mem, "");
    appcontainer_cleanup();
    return 0;
}
