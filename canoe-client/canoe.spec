# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 生成 Canoe.exe。

用法：
    pyinstaller canoe.spec --noconfirm --clean

产物：dist/Canoe/Canoe.exe（onedir 模式，启动快、便于替换内核）

为什么用 onedir 而不是 onefile：
    onefile 每次启动都要把整个程序解压到临时目录，杀软也更容易误报。
    onedir 启动快，而且 bin/sing-box.exe 是明文放在旁边，方便用户替换内核版本。
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

datas = [
    (str(BASE / "assets" / "canoe.ico"), "assets"),
    (str(BASE / "assets" / "canoe.png"), "assets"),
    *bin_files,
]

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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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
    version=None,        # 想加版本信息可指向一个 version_info 文件
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Canoe",
)
