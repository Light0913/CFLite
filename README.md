# CFLite —— 单机判题系统

Windows 10/11 单机在线判题工具（OJ）。基于 **FastAPI + Jinja2 + 原生 EventSource + SQLite + C++ judge.exe**，
还原 **Codeforces Div.2 分数制**：时间衰减、错误罚分、锁题 Hack、pretests + system tests 两阶段评测。

**无 Node / 无 WSL / 无 Docker / 无 MSYS2 / 无 CDN。可移植到任意 Windows 电脑。**
支持**一键热更新（数据 + 代码，不停服）**。

---

## 一、环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 x64 |
| Python | **3.11+**（`python` 或 `py` 启动器均可） |
| 编译器 | **MinGW-w64（GCC ≥ 14）**；若项目已带 `bin/judge.exe`，可**不装 MinGW** |
| 网络 | 首次构建需联网 `pip install`；之后可离线运行 |

**零硬编码**：不依赖任何特定用户名 / 安装路径。`build.bat` 自动探测 Python 与 MinGW，
`config.py` / `main.py` 按「环境变量 → 常见目录 → PATH」探测。可用环境变量覆盖：

| 环境变量 | 作用 |
|---|---|
| `MINGW_HOME` | MinGW-w64 根目录（需含 `bin\g++.exe`） |
| `CFLITE_PYTHON` | 指定运行 CFLite 的 Python（默认 `python`） |
| `CFLITE_HOST` / `CFLITE_PORT` | 监听地址 / 端口（默认 `0.0.0.0:8000`） |
| `CFLITE_RELOAD` | 设为 `1` 等价于 `--reload`（开发模式） |

## 二、构建与启动

```bat
build.bat     :: 检测环境 + pip 安装依赖 + 编译 judge.exe + 初始化 + 生成 run.bat
run.bat       :: 启动（守护循环，退出码 42 时自动重启）
```

- 默认管理员：`admin` / `admin123`（**登录后请立即到 `/settings` 修改密码**）。
- 浏览器访问 `http://127.0.0.1:8000`。
- **停止服务**：在 `run.bat` 窗口按 `Ctrl+C`（关服时会先关闭 SSE 长连接，正常退出）。

`build.bat` 探测顺序：
- **Python**：`python` → `python3` → `py -3` → `py`（均要求 ≥3.11）
- **MinGW**：`MINGW_HOME` → `D:\mingw64` → `C:\mingw64` → `C:\Program Files\MinGW64` →
  `C:\Program Files\mingw64` → `C:\Program Files (x86)\mingw64` → `C:\msys64\{mingw64,ucrt64}` → `C:\w64devkit` → PATH

### 热更新（不停服更新）

管理员进入 **`/admin/settings` → 「重载前端 / 数据 / 代码」→ 🔄 立即更新**：

1. 重新扫描 `problems/`、`contests/`、`users.json`，清空模板缓存（数据 / 模板 / 静态立即生效）；
2. 若检测到 `backend/*.py`、`main.py`、`web/` 下有**晚于进程启动**的修改，自动退出并由 `run.bat`
   拉起新进程（约 0.5–1 秒），前端会轮询等待服务恢复后自动刷新；
3. 正在评测的提交会被中断，建议空闲时更新。

对应 API：`POST /api/admin/reload`、`POST /api/admin/restart`（均需管理员）。
开发模式也可 `python main.py --reload`：监听 `backend/` 与 `web/`，改代码自动重启 worker。

## 三、功能一览

### 比赛与导航
- **报名制**：比赛**未开始**时普通用户看不到题目（管理员除外）；进行中只有**已报名用户 / 管理员**能看题目；**结束后公开**。
- 比赛导航：**比赛介绍 / 我的提交 / 查看所有提交 / Hack 记录 / 排行榜**（「查看所有提交」仅管理员或赛后显示）。
- 比赛页直接展示比赛信息、公告与**题目列表**（含当前衰减后的满分）；不再有单独的 `/contests/<id>/problems` 页面。
- 题面 / 公告用内置 Markdown 渲染，数学公式用**本地 KaTeX**；代码块（样例输入/输出等）带**复制按钮**。

### 提交与评测
- 提交后立即跳转评测页：`Waiting（等待评测）` → `Judging（评测中）` → 最终结果。
- **赛中只跑 pretests**（`Pretests Passed` / `Wrong Answer`）；**赛后统一重测 system tests** 得到最终 `Accepted / Wrong Answer`。
- **赛后练习模式**：比赛结束后仍可提交（含已锁定的题目），按 **pretests + system** 完整评测，**不计入排行榜**，也不会覆盖正式提交。
- 测试点明细默认展开；verdict 显示完整英文；CE 会显示编译器报错。
- 提交页直接显示代码（管理员 / 本人为代码块；锁题选手看他人代码时为**图片**，防抄袭）。
- 赛后（`draining / rejudging / finalized`）所有提交记录对所有人（含匿名）公开。

### 锁题与 Hack
- 通过该题（`Pretests Passed` / `Accepted`）后可**锁定**（确认框、不可撤销）；锁后赛中不能再提交解答，且比赛页不再显示锁题按钮。
- 锁题后可查看他人该题的通过代码（**图片**）并提交 Hack。
- Hack：成功 **+100** / 失败 **−50** / 输入非法 `Invalid Input`，**次数无限制**；输入经 validator 校验，成功后自动并入 `system_tests`。
- 赛中所有登录用户可看 Hack 列表，但他人 Hack 只显示**评测结果**；赛后（或管理员 / 发起者本人）才能看 **Hack 输入**；**受害者看不到输入**。

### 计分（CF Div.2 官方公式）
```
分数 = max(0.3 × M,  M − M × T / Ctime − 50 × W)
Ctime = D × 25 / 12
```
- `M` 初始满分，`T` 距开赛分钟，`D` 比赛时长（`end_at − start_at`），`W` **首次 AC 前**的错误次数。
- **以首次 AC 为准**；分数下限 `0.3 × M`；总分 = Σ 各题得分 + Hack 净得分，**不低于 0**。
- 排行榜：AC / 失败着色，Hack 列彩色 `+a -b`，锁题用户分数加粗。

### 排行榜导出

| 按钮 | 说明 |
|---|---|
| **导出为 HTML** | 下载纯静态 `scoreboard.html`（无 JS、无外链 CSS，样式内联），人人可用 |
| **导出所有代码** | 下载 `codes.zip`，结构 `用户名/题号.扩展名`（如 `admin/1001.cpp`），**仅管理员** |

### 通知与管理员
- 顶部铃铛实时通知（被 Hack、重测结果），打开自动标记已读。
- 管理员（`/admin`）：
  - **用户管理**（增删、改类型、重置密码；**不能修改自己的类型**）
  - **提交管理**（重测、取消 / 恢复、删除单条记录）
  - **设置**：注册开关、**热更新**、操作审计日志
  - 比赛页「提前结束比赛」；题目页「重测本题」
- **操作全量审计**：创建用户、改类型、重置密码、删除用户、重测 / 取消 / 恢复、删提交、注册开关、提前结束、重载、重启均写入审计日志（重置密码记为 `***`）。
- **注册通道**：管理员手动开 / 关优先于 `close_at` 截止时间；管理页显示的是**实际生效状态**及原因。
- **封禁用户**：登录被拒绝，已登录会话访问任何页面（登录 / 登出 / 静态资源除外）直接返回 403。

## 四、目录结构

```
CFLite/
  main.py  build.bat  run.bat  requirements.txt  README.md
  users.json            # 用户（id + username + SHA256），不进 Git
  backend/              # FastAPI 后端
    app.py  services.py  judge.py  hack.py  lifecycle.py  scoring.py
    repos.py  storage.py  sse.py  db.py  users.py  config.py  admin_actions.py
    minimd.py  miniyaml.py  codeimage.py
  judge/                # C++ judge.exe 源码
  bin/judge.exe         # 编译产物
  vendor/               # 纯 Python 依赖本地副本（离线兜底）
  web/templates/  web/static/   # Jinja2 模板 + 自包含 CSS/JS + 本地 KaTeX
  problems/<pid>/       # 题目（statement.md + testdata/ + solution.cpp）
  contests/<cid>/       # 比赛（contest.yaml + description.md + announcements/）
  data/cflite.db        # SQLite（提交/Hack/锁题/报名/通知/比赛状态/审计）
  submissions/ hacks/ build/ temp/    # 运行时数据（Git 忽略）
  tools/hash_password.py  tools/get_katex.py
```

## 五、判题流程

```
提交 → Pretests（赛中即时判定）
          ├─ 未通过 → 最终 WA（赛后不再跑 system）
          └─ 通过   → Pretests Passed
                        ↓ 比赛结束 / 管理员提前结束
                    重测 system_tests → AC / WA（最终结果）

比赛结束后 → 练习模式：pretests + system 完整评测，不计入排行榜
```

- 比赛状态机：`scheduled → running → draining → rejudging → finalized`（`backend/lifecycle.py`，每 3 秒检查）。
- 到点自动进入 `draining` 并触发赛后重测；管理员「提前结束比赛」同理，按 `rejudge.concurrency` 并发。
- 赛后提交记录、Hack 记录对所有人（含匿名）公开。

## 六、沙箱说明（重要）

`judge.exe` 使用 **Job Object**（CPU / 内存 / 进程数 / UI / 输出上限）限制选手进程；
**AppContainer** 提供文件 / 网络 / 注册表隔离；AppContainer 创建失败时自动回退为仅 Job Object。

- **AppContainer 需要 `SeTcbPrivilege`**：以 **SYSTEM** 运行后端（Windows 服务 / `psexec -s -i python main.py`）。
- **未以 SYSTEM 运行时**，judge.exe 自动降级为仅 Job Object 隔离（仍限制资源）。
- 编译前会把提交源码复制进本次评测工作目录，避免沙箱读取 `submissions/` 受限导致 CE。

## 七、出题约定

`problems/<pid>/testdata/config.yaml`：

```yaml
id: 1001
title: A + B Problem
type: default            # default | interactive | communication | submit_answer
language: cpp17          # cpp11 | cpp14 | cpp17 | cpp20 | cpp23（工具程序统一 cpp23）
time: 1s
memory: 256m
compile_time: 10s
checker_type: default    # default | testlib
checker: checker.cpp     # checker_type=testlib 时（自定义 checker）
validator: validator.cpp # Hack 输入校验器（可选）
cases:
  pretests:              # 可省略（省略则赛时直接判 PRETESTS_PASSED）
    - {input: 1.in, output: 1.out}
  system_tests:          # 可省略（省略则赛后直接判 AC）
    - {input: 2.in, output: 2.out}
```

- **checker / validator 懒编译**：首次评测 / 首次 Hack 时才编译并缓存。
- 自定义 checker 与 testlib 调用约定一致：`checker <input> <output> <answer>`，退出码 `0=AC / 1=WA / 2=PE`；希望内部错误显示为 System Error 可用 `3`。
- `contest.yaml` 关键字段：`start_at` / `end_at` / `registration` / `score`（`base_scores`、`wrong_penalty`、`decay.floor_ratio`）/ `rejudge.concurrency` / `hack` / `notifications`。
- **测试数据格式必须与 checker 期望一致**，否则 checker 会报错（如 `bad input header`）。

## 八、常见问题

- **提示「未找到 MinGW-w64」**：安装 winlibs UCRT 包，或设置 `MINGW_HOME`；已有 `bin/judge.exe` 可不装。
- **启动报缺依赖**：运行 `build.bat`；`vendor/` 已含纯 Python 依赖兜底。
- **端口被占用**：`set CFLITE_PORT=8001` 后启动。停止服务：`run.bat` 窗口按 `Ctrl+C`，或按端口结束：
  `Get-NetTCPConnection -LocalPort 8000 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`。
- **更新代码 / 数据**：管理员在 `/admin/settings` 点「🔄 立即更新」；前端 / 测试数据 / 配置无需重启。
- **注册通道关不掉 / 开不了**：管理页会显示实际状态与原因；手动开关优先于 `close_at`。
- **重置密码**：`python tools/hash_password.py <明文>` 得到哈希后写入 `users.json`。
- **重新下载 KaTeX**：`python tools/get_katex.py`。
- **清空运行数据**：删除 `data/cflite.db`、`submissions/`、`hacks/`、`temp/`、`build/` 后重启；
  若要清除 Hack 并入的测试点，还需回退各题 `testdata/config.yaml` 中 `source: hack` 的条目并删除 `hack_*.in/out`。
