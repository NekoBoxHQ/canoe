"""「记住账号密码」—— 把凭据存到本地，密码用 Windows DPAPI 加密。

**为什么是 DPAPI 而不是直接写文件**

DPAPI（`CryptProtectData`）把密文绑到**当前 Windows 用户**。同一个
`client.json` 拷到别的机器、或者在同一台机器上换个 Windows 账户，
都解不开。所以哪怕配置文件被人顺手拿走，里面的密码也不能直接用。

调它不用装任何东西 —— `ctypes` 直接叫 `crypt32.dll`。这也是没上
`keyring` 的原因：那会为了一个功能拖进一串依赖，而 keyring 在
Windows 上干的就是这件事。

**边界说清楚**

这防的是"配置文件被拷走 / 被人瞥见"，**防不住能在这个 Windows 账户下
运行代码的人** —— 他调一次 `CryptUnprotectData` 就还原了。
「记住密码」这件事本身就有这个上限，换成任何实现都一样。
所以服务端那边该有的防护（令牌可吊销、封禁立即生效、订阅随时收回）
一条都不能少。

**存哪**

`%APPDATA%\\Canoe\\client.json` 里的 `remember` 一节，和别的不敏感配置
放一起 —— 反正密码那一项是密文。用户名本身不加密（它不算秘密，
而且界面上本来就要回填出来）。
"""
from __future__ import annotations

import base64
import ctypes
import sys

#: 附加熵。相当于给密文再加一个"只认这个程序"的标记 ——
#: 别的程序就算能读到这份密文，没有同一串熵也解不开。
_ENTROPY = b"canoe/remember-password/v1"

_WINDOWS = sys.platform == "win32"


class CredStoreError(Exception):
    """DPAPI 不可用或者加解密失败。"""


if _WINDOWS:  # pragma: no cover - 平台相关
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _make_blob(data: bytes):
        """把 bytes 包成 DATA_BLOB。

        ⚠ 返回 (blob, buf)：buf 必须由调用方**一直持有**到系统调用返回，
        否则被 GC 回收，pbData 就成了野指针 —— 表现是偶尔解密失败，
        或者拿到一段垃圾。踩过一次，很难查。
        """
        buf = ctypes.create_string_buffer(data, len(data))
        blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf

    def _read_blob(blob: _Blob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def available() -> bool:
    """这台机器上能不能用（Windows 才行）。"""
    return _WINDOWS


def protect(plain: str) -> str:
    """明文 -> base64 的密文。失败抛 CredStoreError。"""
    if not _WINDOWS:
        raise CredStoreError("DPAPI 只在 Windows 上有")

    data = plain.encode("utf-8")
    blobin, _keep1 = _make_blob(data)
    ent, _keep2 = _make_blob(_ENTROPY)
    blobout = _Blob()

    ok = _crypt32.CryptProtectData(
        ctypes.byref(blobin), "canoe", ctypes.byref(ent), None, None, 0,
        ctypes.byref(blobout),
    )
    if not ok:
        raise CredStoreError(f"CryptProtectData 失败（错误码 {ctypes.get_last_error()}）")
    return base64.b64encode(_read_blob(blobout)).decode("ascii")


def unprotect(token: str) -> str:
    """base64 的密文 -> 明文。解不开抛 CredStoreError。"""
    if not _WINDOWS:
        raise CredStoreError("DPAPI 只在 Windows 上有")

    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredStoreError("凭据不是合法的 base64") from exc

    blobin, _keep1 = _make_blob(raw)
    ent, _keep2 = _make_blob(_ENTROPY)
    blobout = _Blob()

    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(blobin), None, ctypes.byref(ent), None, None, 0,
        ctypes.byref(blobout),
    )
    if not ok:
        # 换过 Windows 账户、换过机器、或者文件被动过，都会走到这里。
        # 这是**正常的**降级路径，不是异常情况 —— 上层当"没记住"处理。
        raise CredStoreError(f"CryptUnprotectData 失败（错误码 {ctypes.get_last_error()}）")

    try:
        return _read_blob(blobout).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CredStoreError("解出来的不是文本") from exc
