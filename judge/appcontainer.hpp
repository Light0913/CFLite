// appcontainer.hpp —— AppContainer profile 管理（需 SYSTEM + SeTcbPrivilege）。
#pragma once
#include <string>
#include <vector>

#include <windows.h>

namespace cflite {

// 尝试创建 AppContainer profile；返回是否可用。
// 不可用时（非 SYSTEM / 无权限），后续 sandbox 仅用 Job Object 隔离。
bool appcontainer_init(const std::wstring& name);
bool appcontainer_available();
PSID appcontainer_sid();
void appcontainer_cleanup();

// 给 AppContainer SID 授予某目录的访问权限（递归继承）。
void appcontainer_grant(PSID sid, const std::wstring& dir, DWORD access_mask);

}  // namespace cflite
