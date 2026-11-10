"""PyInstaller runtime hook: make bundled Qt DLL directories explicit.

PyInstaller's stock PySide6 hook adds only ``sys._MEIPASS`` to ``PATH``.
In a onefile build, QtGui.pyd and its Qt DLLs live under
``_MEIPASS/PySide6``; registering that directory is required for Windows
to resolve the dependent DLLs before Python imports QtGui.
"""
from __future__ import annotations

import os
import sys


_DLL_HANDLES = []

if sys.platform == "win32" and hasattr(sys, "_MEIPASS"):
    root = sys._MEIPASS
    os.environ["PATH"] = root + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        _DLL_HANDLES.append(os.add_dll_directory(root))

    for name in ("PySide6", "shiboken6"):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            _DLL_HANDLES.append(os.add_dll_directory(path))

    # Pin the exact DLLs before QtGui.pyd resolves its imports. Merely changing
    # PATH is not sufficient in some onefile bootloader layouts, and Qt6Core's
    # ICU dependency must not be allowed to resolve to an unrelated copy on PATH.
    import ctypes

    system_icu = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icuuc.dll")
    for path in (
        system_icu,
        os.path.join(root, "PySide6", "MSVCP140.dll"),
        os.path.join(root, "PySide6", "MSVCP140_1.dll"),
        os.path.join(root, "PySide6", "MSVCP140_2.dll"),
        os.path.join(root, "PySide6", "VCRUNTIME140.dll"),
        os.path.join(root, "PySide6", "VCRUNTIME140_1.dll"),
        os.path.join(root, "PySide6", "Qt6Core.dll"),
    ):
        if os.path.isfile(path):
            _DLL_HANDLES.append(ctypes.WinDLL(path))
