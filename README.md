# CFLite —— 单机判题系统

Windows 10/11 单机在线判题工具（OJ）。基于 **FastAPI + Jinja2 + 原生 EventSource + SQLite + C++ judge.exe**，
支持 CF Div.2 分数制（含衰减开关）、四种题型（标准 / 交互 / 通信 / 提交答案）、pretests + system_tests 两阶段、
锁题 Hack、实时评测（NDJSON + SSE）、管理员运行时开关等。

**无 Node / 无 WSL / 无 Docker / 无 MSYS2 / 无 CDN。可移植到任意 Windows 电脑。**

---

## 一、环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 x64 |
| Python | **3.11+**（`python` 或 `py` 启动器均可） |
| 编译器 | **MinGW-w64（GCC ≥ 14）**（推荐 winlibs UCRT 独立包） |
| 网络 | 首次构建需联网 `pip install`；之后可离线运行 |

**零硬编码**：项目不依赖任何特定用户名 / 安装路径。`build.bat` 自动探测 Python 与 MinGW；
`config.py` 与 `main.py` 同样按「环境变量 → 常见目录 → PATH」探测，可用以下环境变量覆盖：

| 环境变量 | 作用 |
|---|---|
| `MINGW_HOME` | MinGW-w64 根目录（需含 `bin\g++.exe`） |
| `CFLITE_PYTHON` | 指定运行 CFLite 的 Python（默认用 `python`） |
| `CFLITE_HOST` / `CFLITE_PORT` | 监听地址 / 端口（默认 `0.0.0.0:8000`） |

## 二、构建与启动

```bat
:: 1. 一键构建（检测 Python/MinGW + pip 安装依赖 + 编译 judge.exe + 初始化 + 生成 run.bat）
build.bat

:: 2. 启动（任选其一）
run.bat
:: 或
python main.py
```

- 默认管理员：`admin` / `admin123`（**登录后请立即到 `/settings` 修改密码**）。
- 浏览器访问 `http://127.0.0.1:8000`。

`build.bat` 探测顺序：
- **Python**：`python` → `python3` → `py -3` → `py`（均要求 ≥3.11）
- **MinGW**：`MINGW_HOME` → `D:\mingw64` → `C:\mingw64` → `C:\Program Files\MinGW64` →
  `C:\Program Files\mingw64` → `C:\Program Files (x86)\mingw64` → `C:\msys64\{mingw64,ucrt64}` → `C:\w64devkit` → PATH

## 三、目录结构

```
CFLite/
  main.py  build.bat  run.bat  requirements.txt  README.md
  users.json            # 用户（id + username + SHA256），不进 Git
  backend/              # FastAPI 后端
  judge/                # C++ judge.exe 源码
  bin/judge.exe         # 编译产物
  vendor/               # 纯 Python 依赖本地副本（跨 Python 版本可用，离线兜底）
  web/templates/  web/static/   # Jinja2 模板 + 自包含 CSS/JS
  problems/<pid>/       # 题目（statement.md + testdata/config.yaml + *.in/out + solution.cpp）
  contests/<cid>/       # 比赛（contest.yaml + description.md）
  submissions/ hacks/ build/ temp/    # 运行时数据（Git 忽略）
  tools/hash_password.py
```

## 四、沙箱说明（重要）

`judge.exe` 使用 **Job Object**（CPU / 内存 / 进程数 / UI / 输出上限）限制选手进程；
**AppContainer** 提供文件 / 网络 / 注册表隔离。

- **AppContainer 需要 `SeTcbPrivilege`**：请以 **SYSTEM** 运行后端（Windows 服务 / `psexec -s -i python main.py`）。
- **未以 SYSTEM 运行时**，judge.exe 自动**降级为仅 Job Object 隔离**（仍限制资源），日志会提示。

## 五、出题约定

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
checker: spj.cpp         # checker_type=testlib 时
interactor: interactor.cpp

cases:
  pretests:              # 可省略（省略则赛时直接判 PRETESTS_PASSED）
    - {input: 1.in, output: 1.out}
  system_tests:          # 可省略（省略则赛后直接判 AC）
    - {input: 2.in, output: 2.out}
```

## 六、与设计文档的差异（刻意为之）

1. **无 CDN 前端**：HTMX / CodeMirror 5 / Pico.css / Alpine.js 用**自包含 CSS + 原生 JS + `<textarea>` 编辑器**替代；
   **KaTeX 已内置**（`web/static/katex/`，含 20 个字体文件），`$...$` / `$$...$$` 由前端 KaTeX 渲染完整数学公式。
2. **SQLite 用标准库 `sqlite3`**（而非 aiosqlite）。
3. **AppContainer 可选降级**：非 SYSTEM 时回退 Job Object 隔离。
4. **判题分发用 `subprocess.Popen` + 读取线程**（而非 `asyncio.create_subprocess_exec`）：
   Windows 上后者走「双工命名管道」，在受限环境可能被拒绝；匿名管道更稳，仍可流式读取 NDJSON。
5. **适配 starlette 1.x**：`TemplateResponse(request, name, context)` 新签名。
6. **内置 YAML 解析器（`backend/miniyaml.py`）**：不依赖 pyyaml / ruamel.yaml
   （pyyaml 在 Python 3.15 上没有预编译 wheel，源码编译又需要 MSVC，不属于「所有电脑」都有的环境）。
   内置解析器覆盖 `config.yaml` / `contest.yaml` 所需格式，且**日期时间按字符串处理**
   （避免 pyyaml 把 `2026-01-01T00:00:00+08:00` 强制转成 datetime）。
7. **Markdown + KaTeX**：题面/公告用内置 `backend/minimd.py` 渲染 Markdown；数学公式交给**本地 KaTeX**
   （`web/static/katex/`，无 CDN）。若该目录缺失，运行 `python tools/get_katex.py` 重新下载。

## 七、常见问题

- **编译报「未找到 MinGW-w64」**：安装 winlibs UCRT 包，或设置 `MINGW_HOME` 指向其根目录。
- **启动报缺依赖**：运行 `build.bat`（或 `pip install -r requirements.txt`）；`vendor/` 已含纯 Python 依赖兜底。
- **注册通道关闭**：管理员在 `/admin/settings` 一键开关（DB 持久化，重启保留）。
- **赛后 Git 归档**：`git add problems/*/testdata/` 提交 Hack 并入的测试点。
- **重置密码**：`python tools/hash_password.py <明文>` 得到哈希后写入 `users.json`。
