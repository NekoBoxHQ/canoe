"""单实例保护。

**为什么必须有**：轻舟占着一个固定的本地端口（mixed_port）和一个固定名字的
TUN 网卡（canoe）。两个实例根本没法共存 —— 后来者绑不上端口、也建不了那张
网卡，表现就是"启航就报错"：

    FATAL start inbound/mixed[mixed-in]: listen tcp 127.0.0.1:20818:
    bind: Only one usage of each socket address ... is normally permitted

而两个窗口长得一模一样，用户关掉上面那个、下面那个就露出来 —— 看着就像
"点 × 窗口不消失"。（真机上就是这么报过来的。）

用 QLockFile：它把持有者的 pid 写进锁文件，持有者没了就自动判定为陈旧锁并
接管，不用自己写清理逻辑。锁文件放在配置目录，和 client.json 挨着。
"""
from __future__ import annotations

from PySide6.QtCore import QLockFile

from .config import CONFIG_DIR

#: 另一个实例占着锁时先等多久（毫秒）。
#:
#: 故意很短。**提权重启那条路不需要它** —— 那边是先 release() 再起新进程
#: （见 tun.relaunch_as_admin），新进程一来就能拿到锁。这里留 3 秒只是
#: 兜一下"用户手快双击了两次"这种抖动；再长就变成点两下、愣十几秒才
#: 被告知"已经在跑了"，那才是真的难用。
WAIT_MS = 3000

_lock: QLockFile | None = None


def acquire() -> bool:
    """抢锁。拿到了返回 True；已经有别的实例占着返回 False。"""
    global _lock
    if _lock is not None:
        return True

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 建不了目录就没法用锁文件。宁可放行（用户至少能用），
        # 也不要因为保护措施本身失败把程序拦在门外。
        return True

    lock = QLockFile(str(CONFIG_DIR / "canoe.lock"))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(WAIT_MS):
        return False
    _lock = lock
    return True


def release() -> None:
    """主动放锁。

    提权重启那条路必须先放：新进程起来第一件事就是抢这把锁，而我们这边
    还要几秒才退干净。
    """
    global _lock
    if _lock is not None:
        _lock.unlock()
        _lock = None
