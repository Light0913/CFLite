@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   CFLite Build
echo ==================================================

REM ---------- 1. Locate Python 3.11+ ----------
set "PY="
call :try_py "python"
if not defined PY call :try_py "python3"
if not defined PY call :try_py "py -3"
if not defined PY call :try_py "py"
if not defined PY (
    echo [ERROR] Python not found.
    echo         Install Python 3.11+ and add it to PATH, or use the py launcher.
    goto :fail
)
%PY% -c "import sys;raise SystemExit(0 if sys.version_info[0]*100+sys.version_info[1]>=311 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ required. Found:
    %PY% --version
    goto :fail
)
echo [1/4] Python:
%PY% --version

REM ---------- 2. Install Python dependencies ----------
echo [2/4] Installing Python dependencies ...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [WARN] pip install failed. Retry manually:
    echo        %PY% -m pip install -r requirements.txt
    echo        The app can still start if vendor already has the runtime deps.
)

REM ---------- 3. Locate MinGW and build judge.exe ----------
echo [3/4] Building judge.exe ...
set "GXX="
call :try_gxx "%MINGW_HOME%\bin\g++.exe"
call :try_gxx "D:\mingw64\bin\g++.exe"
call :try_gxx "C:\mingw64\bin\g++.exe"
call :try_gxx "C:\Program Files\MinGW64\bin\g++.exe"
call :try_gxx "C:\Program Files\mingw64\bin\g++.exe"
call :try_gxx "C:\Program Files (x86)\mingw64\bin\g++.exe"
call :try_gxx "C:\msys64\mingw64\bin\g++.exe"
call :try_gxx "C:\msys64\ucrt64\bin\g++.exe"
call :try_gxx "C:\w64devkit\bin\g++.exe"
    if not defined GXX (
        where g++ >nul 2>&1
        if not errorlevel 1 (
            for /f "delims=" %%G in ('where g++') do if not defined GXX set "GXX=%%G"
        )
    )
    if defined GXX (
        echo        Compiler: !GXX!
        if not exist bin mkdir bin
        set "SRCS="
        for %%F in (judge\*.cpp) do set "SRCS=!SRCS! "%%F""
        "!GXX!" -O2 -std=c++23 -D_WIN32_WINNT=0x0A00 -DWINVER=0x0A00 -static -static-libgcc -static-libstdc++ !SRCS! -o bin\judge.exe -lpsapi -luserenv -lshell32 -ladvapi32
        if errorlevel 1 (
            echo [ERROR] judge.exe build failed
            goto :fail
        )
    ) else (
        if exist bin\judge.exe (
            echo        [WARN] MinGW not found; using existing bin\judge.exe.
        ) else (
            echo        [ERROR] MinGW-w64 g++.exe not found and bin\judge.exe missing.
            echo                Install winlibs UCRT pack and unzip to e.g. D:\mingw64,
            echo                or set MINGW_HOME to its root directory.
            goto :fail
        )
    )

REM ---------- 4. Init dirs, users.json, run.bat ----------
echo [4/4] Initializing directories and default admin ...
for %%D in (bin build temp problems contests submissions hacks data web\templates web\static tools) do (
    if not exist "%%D" mkdir "%%D"
)
if not exist users.json (
    echo        Creating default admin admin / admin123 - change after login.
    %PY% -c "import json,hashlib;h=hashlib.sha256(b'admin123').hexdigest();json.dump({'next_id':2,'users':[{'id':1,'username':'admin','password_sha256':h,'type':'admin'}]},open('users.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)"
)
> run.bat (
    echo @echo off
    echo cd /d "%%~dp0"
    echo !PY! main.py
    echo pause
)
echo        Wrote run.bat

echo.
echo ==================================================
echo   Build complete!
echo   Start:  run.bat  or: %PY% main.py
echo   Open:   http://127.0.0.1:8000
echo   Note:   AppContainer sandbox needs SYSTEM; otherwise
echo           Job Object isolation is used automatically.
echo ==================================================
endlocal
exit /b 0

:try_py
if defined PY goto :eof
if "%~1"=="" goto :eof
%~1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
goto :eof

:try_gxx
if defined GXX goto :eof
if "%~1"=="" goto :eof
if exist "%~1" set "GXX=%~1"
goto :eof

:fail
echo.
echo Build failed.
pause
exit /b 1
