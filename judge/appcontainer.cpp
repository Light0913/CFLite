// appcontainer.cpp
#include "appcontainer.hpp"

#include <aclapi.h>
#include <userenv.h>

#include "common.hpp"

#pragma comment(lib, "userenv.lib")
#pragma comment(lib, "advapi32.lib")

namespace cflite {

static bool g_available = false;
static PSID g_sid = nullptr;
static std::wstring g_name;

bool appcontainer_init(const std::wstring& name) {
    if (g_available) return true;
    g_name = name;
    PSID sid = nullptr;
    HRESULT hr = CreateAppContainerProfile(name.c_str(), name.c_str(), name.c_str(),
                                           nullptr, 0, &sid);
    if (hr == HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)) {
        hr = DeriveAppContainerSidFromAppContainerName(name.c_str(), &sid);
    }
    if (FAILED(hr)) {
        log_info("AppContainer 不可用（" + std::to_string((unsigned long)hr) +
                 "），回退为仅 Job Object 隔离（需以 SYSTEM 运行获得 SeTcbPrivilege）");
        g_available = false;
        return false;
    }
    g_sid = sid;
    g_available = true;
    log_info("AppContainer profile 已创建");
    return true;
}

bool appcontainer_available() { return g_available; }
PSID appcontainer_sid() { return g_sid; }

void appcontainer_cleanup() {
    if (g_available && !g_name.empty()) {
        DeleteAppContainerProfile(g_name.c_str());
    }
    if (g_sid) {
        FreeSid(g_sid);
        g_sid = nullptr;
    }
    g_available = false;
}

void appcontainer_grant(PSID sid, const std::wstring& dir, DWORD access_mask) {
    if (!sid) return;
    PACL old_acl = nullptr;
    PSID owner = nullptr, group = nullptr;
    PSECURITY_DESCRIPTOR sd = nullptr;
    DWORD rc = GetNamedSecurityInfoW(dir.c_str(), SE_FILE_OBJECT,
                                     DACL_SECURITY_INFORMATION,
                                     &owner, &group, &old_acl, nullptr, &sd);
    if (rc != ERROR_SUCCESS) {
        log_error("GetNamedSecurityInfoW 失败: " + narrow_error(rc) +
                  " 路径: " + wide_to_utf8(dir));
        return;
    }
    EXPLICIT_ACCESSW ea = {};
    ea.grfAccessPermissions = access_mask;
    ea.grfAccessMode = GRANT_ACCESS;
    ea.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
    ea.Trustee.TrusteeForm = TRUSTEE_IS_SID;
    ea.Trustee.ptstrName = (LPWSTR)sid;

    PACL new_acl = nullptr;
    rc = SetEntriesInAclW(1, &ea, old_acl, &new_acl);
    if (rc != ERROR_SUCCESS) {
        log_error("SetEntriesInAclW 失败: " + narrow_error(rc));
        LocalFree(sd);
        return;
    }
    rc = SetNamedSecurityInfoW((LPWSTR)dir.c_str(), SE_FILE_OBJECT,
                               DACL_SECURITY_INFORMATION, nullptr, nullptr, new_acl, nullptr);
    if (rc != ERROR_SUCCESS) {
        log_error("SetNamedSecurityInfoW 失败: " + narrow_error(rc));
    }
    LocalFree(new_acl);
    LocalFree(sd);
}

}  // namespace cflite
