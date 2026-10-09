@echo off
REM ============================================================
REM  轻舟 / Canoe —— Windows 打包脚本
REM  产物：dist\Canoe.exe（单文件，解压出来就这一个）
REM
REM  用法：build.bat
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ==========================================
echo   轻舟 / Canoe  打包
echo   轻舟已过万重山
echo ==========================================
echo.

REM ---- 0. 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python，请先安装 Python 3.10+ 并加入 PATH
    goto :err
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [0/5] Python %PYVER%

REM ---- 1. 安装依赖 ----
echo.
echo [1/5] 安装依赖...
python -m pip install --upgrade pip >nul 2>nul
python -m pip install -e ..\canoe-core || goto :err
python -m pip install -r requirements.txt pyinstaller || goto :err

REM ---- 2. 图标 ----
echo.
echo [2/5] 准备图标...
if not exist "assets\canoe.ico" (
    echo       assets\canoe.ico 不存在，尝试生成...
    python -m pip install pillow >nul 2>nul
    python assets\make_icon.py || echo       [警告] 图标生成失败，将使用默认图标
) else (
    echo       assets\canoe.ico 就绪
)

REM ---- 3. 检查内核 ----
echo.
echo [3/5] 检查内核与规则集...
if not exist "bin\sing-box.exe" (
    echo       [警告] 没找到 bin\sing-box.exe
    echo              打包仍可继续，但用户必须自己放一个 sing-box.exe 才能用。
)
if not exist "bin\ruleset\geosite-cn.srs" (
    echo       [警告] 没找到 bin\ruleset\geosite-cn.srs
    echo              打包后会退化成"启动时去 GitHub 下载规则集"，
    echo              国内网络下大概率失败，"绕过大陆"会失效。
)
if not exist "bin\wintun.dll" (
    echo       [提示] 没找到 bin\wintun.dll —— 全局^(TUN^)模式将不可用。
)

REM ---- 3.5 检查旧产物是否被占用 ----
REM 上一次打包出来的 Canoe.exe 如果还在运行，--clean 删不掉 dist，
REM 会以 PermissionError 中断。这里提前给出可读的提示。
tasklist /FI "IMAGENAME eq Canoe.exe" 2>nul | find /I "Canoe.exe" >nul
if not errorlevel 1 (
    echo.
    echo [错误] 检测到 Canoe.exe 正在运行。
    echo        请先退出程序再打包 —— 否则 PyInstaller 无法清理 dist 目录。
    goto :err
)

REM ---- 4. 打包 ----
echo.
echo [4/5] 打包中（第一次会比较慢）...
python -m PyInstaller canoe.spec --noconfirm --clean || goto :err

REM ---- 5. 自检 ----
REM canoe.spec 已经把整个 bin\ 递归打包进去了（含 ruleset\ 子目录），
REM 所以这里不用再手工 copy。直接跑产物自检，确认内核和规则集都在。
echo.
echo [5/5] 自检打包产物...
if not exist "dist\Canoe.exe" (
    echo       [错误] 没生成 Canoe.exe
    goto :err
)
"dist\Canoe.exe" --selftest >nul 2>&1
if errorlevel 2 (
    echo       [警告] 自检未通过 —— 通常是缺 sing-box.exe 或 wintun.dll
    echo              详情见 %%APPDATA%%\Canoe\selftest.txt
) else (
    echo       自检通过
)

echo.
echo ==========================================
echo   完成
echo ==========================================
echo   产物:   dist\Canoe.exe   （就这一个文件）
echo.
echo   打包发布： python scripts\package_release.py
echo   目标机器不需要装 Python。
echo.
goto :eof

:err
echo.
echo [失败] 打包中断，请检查上面的错误信息。
exit /b 1
