# CFLite —— 单机判题系统

Windows 10/11 单机在线判题工具（OJ）。基于 **FastAPI + Jinja2 + 原生 EventSource + SQLite + C++ judge.exe**，
还原 **Codeforces Div.2 分数制**：时间衰减、错误罚分、锁题 Hack、pretests + system tests 两阶段评测。

**无 Node / 无 WSL / 无 Docker / 无 MSYS2 / 无 CDN。可移植到任意 Windows 电脑。**

---

## 一、环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 x64 |
| Python | **3.11+**（`python` 或 `py` 启动器均可） |
| 编译器 | **MinGW-w64（GCC ≥ 14）**；若项目已带 `bin/judge.exe`，可**不装 MinGW** |
| 网络 | 首次构建需联网 `pip install`；之后可离线运行 |

**零硬编码**：不依赖任何特定用户名 / 安装路径。`build.bat` 自动探测 Python 与 MinGW；
`config.py` / `main.py` 同样按「环境变量 → 常见目录 → PATH」探测，可用以下环境变量覆盖：

| 环境变量 | 作用 |
|---|---|
| `MINGW_HOME` | MinGW-w64 根目录（需含 `bin\g++.exe`） |
| `CFLITE_PYTHON` | 指定运行 CFLite 的 Python（默认用 `python`） |
| `CFLITE_HOST` / `CFLITE_PORT` | 监听地址 / 端口（默认 `0.0.0.0:8000`） |

## 二、构建与启动

```bat
build.bat     :: 检测环境 + pip 安装依赖 + 编译 judge.exe + 初始化 + 生成 run.bat
run.bat       :: 启动（等价于 python main.py）
```

- 默认管理员：`admin` / `admin123`（**登录后请立即到 `/settings` 修改密码**）。
- 浏览器访问 `http://127.0.0.1:8000`。

`build.bat` 探测顺序：
- **Python**：`python` → `python3` → `py -3` → `py`（均要求 ≥3.11）
- **MinGW**：`MINGW_HOME` → `D:\mingw64` → `C:\mingw64` → `C:\Program Files\MinGW64` →
  `C:\Program Files\mingw64` → `C:\Program Files (x86)\mingw64` → `C:\msys64\{mingw64,ucrt64}` → `C:\w64devkit` → PATH

## 三、功能一览

### 比赛与题目
- **报名制**：比赛进行中只有**已报名用户**和**管理员**能看题目；匿名跳登录、未报名跳比赛页；比赛结束后题目公开。
- **题面 / 公告**用内置 Markdown 渲染，数学公式用**本地 KaTeX** 渲染（`$...$` / `$$...$$`）。
- **题目列表**显示「当前衰减后的满分」；**排行榜**题号下显示「初始满分」。

### 提交与评测
- 提交后**立即跳转**到评测页：未开始跑测试显示 `Waiting（等待评测）`，跑测试点显示 `Judging（评测中）`，完成后显示最终结果。
- **赛中只跑 pretests**（判定为 `Pretests Passed` 或 `Wrong Answer`）；**赛后统一重测 system tests**，得到最终 `Accepted / Wrong Answer`。
- 测试点明细默认展开（不折叠）；verdict 显示完整英文（`Accepted`、`Compilation Error` 等）。
- 提交页**直接显示代码**（管理员/本人为代码块；锁题选手看他人代码时为**图片**，防抄袭复制）。

### 锁题与 Hack
- 通过该题（`Pretests Passed` / `Accepted`）后可**锁定**（带确认框，不可撤销，锁后该题不能再提交解答）。
- 锁题后可查看他人该题的通过代码（**图片形式**），并提交 Hack。
- Hack 判定：成功击穿目标 → **+100 分**；未击穿 → **-50 分**；输入非法 → `Invalid Input`。**Hack 次数无限制**。
- Hack 输入经 **validator** 校验；成功后自动并入该题 `system_tests`。

### 计分（CF Div.2 官方公式）
```
分数 = max(0.3 × M,  M − M × T / Ctime − 50 × W)
Ctime = D × 25 / 12
```
- `M` 初始满分，`T` 距开赛分钟数，`D` 比赛时长（`end_at − start_at`），`W` **首次 AC 前**的错误次数。
- **以首次 AC 为准**：首个 AC 之后的提交不再影响该题得分；分数下限为 `0.3 × M`。
- 总分 = Σ 各题得分 + Hack 净得分，**不低于 0**。
- 排行榜：`ac`/`failed` 着色、Hack 列显示彩色 `+a -b`、锁题用户的分数**加粗**。

### 通知与管理员
- 顶部铃铛实时通知（被 Hack、重测结果）；打开下拉自动标记已读、清除红点。
- 管理员（`/admin`）：
  - 用户管理（增删、改类型、重置密码）
  - 提交管理（重测、取消/恢复、**删除单条记录**）
  - 注册开关（持久化）
  - 比赛页：**提前结束比赛**；题目页：**重测本题**

## 四、目录结构

```
CFLite/
  main.py  build.bat  run.bat  stop.bat  requirements.txt  README.md
  users.json            # 用户（id + username + SHA256），不进 Git
  backend/              # FastAPI 后端
    app.py  services.py  judge.py  hack.py  lifecycle.py  scoring.py
    repos.py  storage.py  sse.py  db.py  users.py  config.py
    minimd.py  miniyaml.py  codeimage.py
  judge/                # C++ judge.exe 源码
  bin/judge.exe         # 编译产物
  vendor/               # 纯 Python 依赖本地副本（离线兜底）
  web/templates/  web/static/   # Jinja2 模板 + 自包含 CSS/JS + 本地 KaTeX
  problems/<pid>/       # 题目（statement.md + testdata/ + solution.cpp）
  contests/<cid>/       # 比赛（contest.yaml + description.md + announcements/）
  submissions/ hacks/ build/ temp/    # 运行时数据（Git 忽略）
  tools/hash_password.py  tools/get_katex.py
```

## 五、判题流程

```
提交 → Pretests（赛中即时判定）
          ├─ 未通过 → 最终 WA（赛后不再跑 system）
          └─ 通过   → Pretests Passed
                        ↓ 比赛结束 / 管理员提前结束
                    比赛结束 → 重测 system_tests → AC / WA（最终结果）
```

- 比赛状态机：`scheduled → running → draining → rejudging → finalized`（`backend/lifecycle.py`，每 3 秒检查）。
- 管理员「提前结束比赛」会把状态置为 `draining`，随后自动重测并按 `rejudge.concurrency` 并发执行。

## 六、沙箱说明（重要）

`judge.exe` 使用 **Job Object**（CPU / 内存 / 进程数 / UI / 输出上限）限制选手进程；
**AppContainer** 提供文件 / 网络 / 注册表隔离。

- **AppContainer 需要 `SeTcbPrivilege`**：请以 **SYSTEM** 运行后端（Windows 服务 / `psexec -s -i python main.py`）。
- **未以 SYSTEM 运行时**，judge.exe 自动**降级为仅 Job Object 隔离**（仍限制资源），日志会提示。

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

- **checker / validator 采用懒编译**：启动时不编译，首次评测 / 首次 Hack 时才按需编译并缓存。
- 自定义 checker 采用与 testlib 相同的调用约定：`checker <input> <output> <answer>`，退出码 `0=AC / 1=WA / 2=SE`。
- `contest.yaml` 关键字段：`start_at` / `end_at` / `registration` / `score`（`base_scores`、`wrong_penalty`、`decay.floor_ratio`）/ `rejudge.concurrency` / `hack` / `notifications`。

## 八、常见问题

- **提示「未找到 MinGW-w64」**：安装 winlibs UCRT 包，或设置 `MINGW_HOME`；若已有 `bin/judge.exe` 可不装。
- **启动报缺依赖**：运行 `build.bat`；`vendor/` 已含纯 Python 依赖兜底。
- **端口被占用**：`set CFLITE_PORT=8001` 后启动，或 `stop.bat 8000`。
- **重置密码**：`python tools/hash_password.py <明文>` 得到哈希后写入 `users.json`。
- **重新下载 KaTeX**：`python tools/get_katex.py`。
- **清空运行数据**：删除 `data/cflite.db`、`submissions/`、`hacks/`、`temp/`、`build/` 后重启（会重新初始化）。
