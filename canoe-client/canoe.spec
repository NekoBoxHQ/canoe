# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 生成 Canoe.exe。

用法：
    pyinstaller canoe.spec --noconfirm --clean

产物：dist/Canoe.exe —— **就这一个文件**，发给用户双击即可。

为什么用 onefile：
    用户拿到的是一个 exe，解压出来不会有"_internal"那一大堆文件，
    桌面上干干净净，也不会有人把 exe 单独拖走然后报"缺 _internal"。
    运行的时候 PyInstaller 把内容解到临时目录（`sys._MEIPASS`），
    进程退出就删掉，机器上不留东西。

代价（认了）：
    · 每次启动要多花一点时间解压（80 多 MB）；
    · 个别杀软对 onefile 更敏感，可能报"可疑的自解压程序"。
    这两条换"只有一个文件"，对一个要发给普通用户的客户端来说划算。

内核还是塞在里面（bin/sing-box.exe、wintun.dll、规则集），
所以 `config.find_singbox()` 找的是解压后的 `_MEIPASS/bin`。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

BASE = Path(SPECPATH)
CORE_DIR = BASE.parent / "canoe-core"   # ../canoe-core

# 把整个 bin/ 目录**递归**带上：
#   bin/sing-box.exe        代理内核
#   bin/wintun.dll          全局模式驱动（用户自己放）
#   bin/ruleset/*.srs       大陆分流规则集
# 递归很关键 —— 只 glob("bin/*") 会漏掉 ruleset/ 子目录，
# 结果就是打包后"绕过大陆"失效（会退化成启动时去 GitHub 下载，国内还下不动）。
bin_files: list[tuple[str, str]] = []
bin_root = BASE / "bin"
if bin_root.is_dir():
    for path in bin_root.rglob("*"):
        if not path.is_file():
            continue
        if path.parent == bin_root:
            dest = "bin"
        else:
            dest = f"bin/{path.parent.relative_to(bin_root).as_posix()}"
        bin_files.append((str(path), dest))

# 图标和 Logo 一并带上（不写死文件名 —— 以后加素材不用改这里）。
asset_files: list[tuple[str, str]] = []
assets_root = BASE / "assets"
#: 只用于「重新生成 Logo」的源文件，不进发布包（省 1.5MB 死重量）
ASSET_SKIP = {"logo-source.png"}
if assets_root.is_dir():
    for path in sorted(assets_root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".png", ".ico"}:
            continue
        if path.name in ASSET_SKIP:
            continue
        asset_files.append((str(path), "assets"))

datas = [*asset_files, *bin_files]

a = Analysis(
    ["run.py"],
    # ★ CORE_DIR 必须加进来。
    #   canoe-core 通常是以 `pip install -e` 装进去的，
    #   而 PyInstaller 解析不了 PEP 660 的可编辑安装桩，
    #   只写 hiddenimports 照样会 ModuleNotFoundError: No module named 'canoe_core'。
    #   直接把源码目录放进搜索路径最省事，也不影响开发时的可编辑安装。
    pathex=[str(BASE), str(CORE_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # canoe_core 显式声明，避免被当成"未使用"而漏掉
        "canoe_core",
        *collect_submodules("canoe_core"),
        # PySide6 只用到 QtCore/QtGui/QtWidgets，排除 WebEngine 等大块头
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 用不到的重型模块，排掉能显著减小体积
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtPdf",
        "PySide6.QtDesigner",
        "tkinter",
        "matplotlib",
        "numpy",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ★ 2026-10-10：从 onefile 改成 **onedir**（exe + 同目录的 _internal/）。
#
#   为什么改：onefile 每次启动都要把约 80MB 解压到 `%TEMP%\_MEIxxxx`，然后再去
#   那个目录里加载 `python313.dll`。用户在这台机器上反复撞到
#
#       Failed to load Python DLL '...\_MEI00003ae42\python313.dll'.
#       LoadLibrary: 找不到指定的模块。
#
#   —— 更新完重启起不来，手动再点一次又好了。猜了一整晚（杀软在翻刚落盘的
#   二进制、临时目录被 pid 撞名、引导器继承了父进程的 _PYI_* 环境变量……）
#   都**没能钉死**。于是按用户的意见换成"根本不解压"的形态：
#   文件一直在磁盘上，进程直接加载。没有解压这一步，就没有这一步能出的错。
#   顺带启动快一大截（不用每次解压 80MB）。
#
#   代价：产物不再是一个文件，而是一个文件夹（Canoe.exe + _internal/）。
#   安装 = 把 zip 解压到 C:\ 得到 C:\Canoe\；更新 = 整体换文件夹。
#   这是用户拍的取舍 —— 稳定性优先于"只有一个文件"。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # 依赖不进 exe，交给下面的 COLLECT 摊进文件夹
    name="Canoe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 压缩容易被杀软误报，关掉
    console=False,       # 桌面程序，不要黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE / "assets" / "canoe.ico"),
    version=None,
)

# 产物目录名就是安装目录名：dist/Canoe/  ->  解压到 C:\ 得到 C:\Canoe\
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Canoe",
)
