# 05 · Windows 打包：生成 Canoe.exe

## 一句话

```bat
cd canoe-client
build.bat
```

产物：`dist\Canoe\Canoe.exe`。**目标机器不需要装 Python。**

---

## 1. 打包前准备

| 项 | 要求 |
|---|---|
| Python | 3.10+（开发用 3.13 + sing-box 1.14.2 验证过） |
| 依赖 | `build.bat` 会自动装 `pyinstaller` 和 `requirements.txt` |
| 图标 | `assets/canoe.ico`（已有；改了设计稿就跑 `python assets/make_icon.py` 重新生成，需要 Pillow） |
| 内核 | `bin/sing-box.exe` —— **没有也能打包**，但用户跑不起来 |
| 规则集 | `bin/ruleset/geosite-cn.srs`、`geoip-cn.srs` —— **漏了"绕过大陆"会失效** |
| 驱动 | `bin/wintun.dll` —— 只有全局(TUN)模式需要 |

---

## 2. build.bat 做了什么

```
[0/5] 检查 Python 版本
[1/5] pip install -e ..\canoe-core   +   requirements.txt   +   pyinstaller
[2/5] 图标就绪检查（缺了就用 make_icon.py 生成）
[3/5] 内核与规则集检查（缺了给警告，不中断）
[4/5] pyinstaller canoe.spec --noconfirm --clean
[5/5] 对产物跑 Canoe.exe --selftest 自检
```

---

## 2.1 自检：`Canoe.exe --selftest`

打包后的程序没有控制台，用户报"打不开"时很难排查。
所以客户端内置了一个自检开关：

```bat
dist\Canoe\Canoe.exe --selftest
```

它会检查内核路径、内核版本、规则集、图标是否就位，
结果打印出来并写到 `%APPDATA%\Canoe\selftest.txt`：

```json
{
  "version": "1.0.0",
  "frozen": true,
  "executable": "...\\dist\\Canoe\\Canoe.exe",
  "bin_dir": "...\\dist\\Canoe\\_internal\\bin",
  "bin_dir_exists": true,
  "singbox": "...\\_internal\\bin\\sing-box.exe",
  "singbox_found": true,
  "singbox_version": "sing-box version 1.14.2",
  "ruleset": { "geoip-cn.srs": 34185, "geosite-cn.srs": 56144 }
}
```

退出码：`0` 正常，`2` 缺内核。让用户把这个文件发过来，一眼就知道缺什么。

---

## 3. canoe.spec 里的关键决定

### 用 onedir 而不是 onefile

```python
COLLECT(exe, a.binaries, a.datas, name="Canoe")
```

| | onedir | onefile |
|---|---|---|
| 启动速度 | 快 | 每次启动都要解压到临时目录 |
| 杀软误报 | 少 | 多（自解压行为像恶意软件） |
| 换内核 | 直接替换 `bin\sing-box.exe` | 要重新打包 |
| 分发 | 打成一个 zip | 单个 exe |

代理工具的用户经常需要换 sing-box 版本，onedir 明显更合适。

### 关掉 UPX

```python
upx=False
```

UPX 压缩能减小体积，但**是被杀软误报的头号原因**。分发给用户的程序不值得为几十 MB 冒这个风险。

### 排除用不到的重型模块

```python
excludes=[
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtPdf",
    "PySide6.QtDesigner", "tkinter", "matplotlib", "numpy",
]
```

轻舟只用到 PySide6 的 `QtCore / QtGui / QtWidgets`。不排除的话，
光 QtWebEngine 一个模块就能把体积撑到几百 MB。

### ★ 把 canoe-core 的源码目录加进 pathex（否则打包后必崩）

```python
CORE_DIR = BASE.parent / "canoe-core"   # ../canoe-core

a = Analysis(
    ["run.py"],
    pathex=[str(BASE), str(CORE_DIR)],   # ← 这一行是关键
    hiddenimports=["canoe_core", *collect_submodules("canoe_core"), ...],
)
```

**这一条是实测踩出来的，只写 `hiddenimports` 不够。**

`canoe-core` 通常是用 `pip install -e ../canoe-core` 装的。editable 安装走的是
PEP 660，装进 site-packages 的只是一个 `__editable__.*.pth` 之类的**查找器桩**，
真正的源码还在项目目录里。PyInstaller 的依赖分析器解析不了这种桩，
结果就是：**打出来的 exe 一启动就 `ModuleNotFoundError: No module named 'canoe_core'`**。

（这个错误在打包阶段完全不报，只在运行打包产物时才暴露。所以
`docs/05-packaging.md` 里第 8 节才强调"打完必须实际跑一次 exe"。）

把源码目录放进 `pathex` 后，分析器能直接找到 `canoe_core/` 包，
同时不影响开发时的可编辑安装。

替代做法：构建前改用非 editable 安装 —— `pip install ../canoe-core`。
但那样改了 core 就得重装，开发时更麻烦，所以推荐 pathex 方案。

`hiddenimports` 里显式列出 `canoe_core` 仍然要保留，作用是防止它被
当成"未使用"而漏掉。

### 不要黑框

```python
console=False
```

桌面程序，双击不能弹出控制台窗口。

---

## 4. 产物结构

```
dist/Canoe/
├── Canoe.exe              ← 主程序（已嵌入 assets/canoe.ico 作为 exe 图标）
├── assets/
│   ├── canoe.ico
│   ├── canoe-logo.png     ← 界面里用的徽章（圆外透明）
│   └── canoe.png
├── bin/
│   ├── sing-box.exe       ← 内核（build.bat 拷贝过来的）
│   ├── wintun.dll         ← 全局模式需要
│   └── README.md
├── _internal/             ← Python 运行时与依赖
└── ...
```

分发方式：把**整个 `dist/Canoe/` 目录**打成 zip 发给用户，解压即用。

---

## 5. 给 exe 加版本信息（可选）

Windows 资源管理器里能看到版本号、公司名。做法：

1. 写一个 `version_info.txt`：

```
VSVersionInfo(
  ffi=FixedFileInfo(filevers=(1,0,0,0), prodvers=(1,0,0,0)),
  kids=[
    StringFileInfo([StringTable('080404B0', [
        StringStruct('CompanyName', 'Canoe'),
        StringStruct('FileDescription', '轻舟 · One boat, one tap.'),
        StringStruct('FileVersion', '1.0.0.0'),
        StringStruct('ProductName', '轻舟 / Canoe'),
    ])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
```

2. 在 `canoe.spec` 的 `EXE(...)` 里把 `version=None` 改成
   `version=str(BASE / "version_info.txt")`。

2052 是简体中文的 LCID，1200 是 Unicode 代码页。

---

## 6. 代码签名（重要，尤其是要对外分发时）

未签名的 exe 下载时会触发 SmartScreen 警告"Windows 已保护你的电脑"。
解决方式是买一张代码签名证书：

```bat
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
    /f mycert.pfx /p <密码> dist\Canoe\Canoe.exe
```

签名这一步**必须在 PyInstaller 打包之后做**，否则签名会失效。

如果只是内部使用 / 用户量小，可以让用户点"仍要运行"，或者在
`build.bat` 末尾提示这一点。

---

## 7. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 打包后运行报 `ModuleNotFoundError: canoe_core` | **editable 安装的经典坑**（见第 3 节）。确认 `canoe.spec` 里 `pathex` 含 `../canoe-core`，然后 `--clean` 重打包 |
| `--clean` 报 `PermissionError: ... base_library.zip / *.dll` | 上次打出来的 `Canoe.exe` 还在运行，文件被占用。**先退出程序**再打包（`build.bat` 已加前置检查）。顽固的话手动 `rmdir /s /q dist build` |
| 打包后闪退、无提示 | 把 `canoe.spec` 里 `console=False` 临时改成 `True`，再跑一次看报错 |
| 体积几百 MB | 检查 `excludes` 是否生效；确认没把 QtWebEngine 打进去 |
| 杀软报毒 | 确认 `upx=False`；考虑代码签名 |
| 双击无反应 | 可能被 SmartScreen 拦了；右键属性看"解除锁定" |
| 全局模式起不来 | `dist\Canoe\bin\wintun.dll` 缺失，或没以管理员运行 |
| 打包很慢 | 首次 3–10 分钟正常（要分析 PySide6）。之后用 `--noconfirm` 不加 `--clean` 会快很多 |

---

## 8. 服务端打包（可选）

服务端一般跑在 Linux 服务器上，直接部署 Python 代码即可，不需要打包成单个文件。

如果要给 Windows 客户交付服务端，用 `pyinstaller` 打 `run.py` 也可以：

```bat
pyinstaller --name CanoeServer --onedir --add-data "canoe_server;canoe_server" run.py
```

但更推荐的做法是容器化：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY canoe-core /app/canoe-core
COPY canoe-server /app/canoe-server
RUN pip install --no-cache-dir /app/canoe-core -r /app/canoe-server/requirements.txt
WORKDIR /app/canoe-server
EXPOSE 8000
CMD ["uvicorn", "canoe_server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```
