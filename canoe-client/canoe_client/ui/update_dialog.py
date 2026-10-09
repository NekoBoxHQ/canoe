"""「有新版本」那扇窗。

以前这是个 `QMessageBox.information`：只显示版本号和**下载地址**，一个 OK
按钮，点完什么也不发生 —— 用户得自己复制地址、开浏览器、下载、解压、
手动覆盖。而且那个框是系统浅色底，跟夜色主题撞在一起，所有字都糊成灰的
（用户截图报过来的就是这个）。

现在整条路都在这一扇窗里走完：

    看到版本差异 -> 点「立即更新」-> 盯着进度条 -> 程序自己换成新版并重启

窗口本身直接用主界面的 FramelessWindow（圆角、夜色底、自绘标题栏），
所以不会再出现"系统弹框跟主题打架"这种事。
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QWidget,
)

from .. import update
from ..update import UpdateInfo
from .window_base import FramelessWindow

WIDTH = 470
#: 高度是按"三行说明 + 一行安装包大小"定的。再高，说明区下面就空出一大块；
#: 再矮，四五行说明就得滚。说明长了滚动条会自己出来，不用担心截断。
HEIGHT = 312


class UpdateDialog(FramelessWindow):
    """发现新版本之后的全部交互。自己不干活，只发信号让主界面去做。"""

    #: 用户点了「立即更新」
    install_requested = Signal()
    #: 下载中点了「取消」，或者关掉了窗口
    cancel_requested = Signal()
    #: 窗口没了（主界面好把引用清掉）
    closed = Signal()

    def __init__(self, info: UpdateInfo, parent: QWidget | None = None) -> None:
        super().__init__(WIDTH, HEIGHT)
        self.info = info
        self.stage = ""
        self.setWindowTitle("有新版本")
        # 弹窗就是弹窗：模态、不给最小化
        self.setWindowModality(Qt.ApplicationModal)
        self.titlebar.set_title("有新版本", f"{info.current} → {info.latest}")
        self.titlebar.min_btn.hide()

        self._build()
        self.set_stage("idle")
        if parent is not None:
            self._center_on(parent)

    # ------------------------------------------------------------------
    def _center_on(self, parent: QWidget) -> None:
        geo = parent.frameGeometry()
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)

    def _build(self) -> None:
        body = self.body_layout
        body.setContentsMargins(24, 8, 24, 18)
        body.setSpacing(10)

        ver = QLabel()
        ver.setObjectName("UpdateVersion")
        ver.setTextFormat(Qt.RichText)
        ver.setText(
            f'当前版本 <b>{html.escape(self.info.current)}</b>'
            f' &nbsp;→&nbsp; 最新版本 <b>{html.escape(self.info.latest)}</b>'
            + (f' <span style="color:#C2603C">（必须升级）</span>'
               if self.info.must_upgrade else "")
        )
        body.addWidget(ver)

        size = f"安装包 {update.human_size(self.info.size)}" if self.info.size else ""
        if size:
            hint = QLabel(size)
            hint.setObjectName("UpdateStatus")
            body.addWidget(hint)

        caption = QLabel("更新说明")
        caption.setObjectName("UpdateCaption")
        body.addWidget(caption)

        # 更新说明吃掉中间所有富余高度。**不给它独自撑开、后面再 addStretch**
        # 的话，中间会空出一大块，窗口看着散。
        body.addWidget(self._notes(), 1)

        # 进度区：平时藏起来，点「立即更新」才露出来 —— 一上来就摆个空
        # 进度条会让人以为已经开始下了
        self.bar = QProgressBar()
        self.bar.setObjectName("UpdateBar")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.hide()
        body.addWidget(self.bar)

        self.status = QLabel()
        self.status.setObjectName("UpdateStatus")
        self.status.hide()
        body.addWidget(self.status)

        self.error = QLabel()
        self.error.setObjectName("UpdateError")
        self.error.setWordWrap(True)
        self.error.hide()
        body.addWidget(self.error)

        body.addSpacing(4)
        body.addLayout(self._buttons())

    def _notes(self) -> QWidget:
        text = (self.info.notes or "").strip() or "（这次没有写更新说明）"
        label = QLabel(text)
        label.setObjectName("UpdateNotes")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        area = QScrollArea()
        area.setObjectName("NotesScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(label)
        area.setMinimumHeight(96)
        # 视口默认是刷底色的，不关掉就会在圆角卡片上盖一块灰
        area.viewport().setAutoFillBackground(False)
        return area

    def _buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)

        self.later_btn = QPushButton("稍后再说")
        self.later_btn.setObjectName("Outline")
        self.later_btn.setFixedSize(120, 38)
        self.later_btn.setCursor(Qt.PointingHandCursor)
        self.later_btn.clicked.connect(self._on_later)
        row.addWidget(self.later_btn)

        self.action_btn = QPushButton("立即更新")
        self.action_btn.setObjectName("Primary")
        self.action_btn.setFixedSize(120, 38)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.clicked.connect(self._on_action)
        row.addWidget(self.action_btn)
        return row

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def set_stage(self, stage: str, message: str = "") -> None:
        """idle / downloading / installing / restarting / error"""
        self.stage = stage

        if stage == "idle":
            self.bar.hide()
            self.status.hide()
            self.error.hide()
            self.action_btn.show()
            self.action_btn.setText("立即更新")
            self.action_btn.setEnabled(True)
            self.later_btn.setText("稍后再说")
            self.later_btn.setEnabled(not self.info.must_upgrade)
            self.later_btn.setVisible(not self.info.must_upgrade)
            return

        if stage == "error":
            self.bar.hide()
            self.status.hide()
            self.error.setText(message)
            self.error.show()
            self.action_btn.show()
            self.action_btn.setText("重试")
            self.action_btn.setEnabled(True)
            self.later_btn.setText("关闭")
            self.later_btn.setVisible(True)
            self.later_btn.setEnabled(True)
            return

        # 下面几个都在跑，按钮一律收起来 —— 能点的只剩「取消」
        self.action_btn.hide()
        self.bar.show()
        self.status.show()
        self.error.hide()
        self.status.setText(message)
        if stage == "downloading":
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            self.later_btn.setText("取消")
        else:
            # 收尾阶段没有可数的进度，让条子来回跑
            self.bar.setRange(0, 0)
            if stage == "restarting":
                # 到这一步已经回不了头了，别留个点了没用的取消
                self.later_btn.setVisible(False)
            self.later_btn.setText("取消")
        self.later_btn.setVisible(stage != "restarting")
        self.later_btn.setEnabled(stage != "restarting")

    def set_progress(self, done: int, total: int) -> None:
        if self.stage != "downloading":
            return
        if total > 0:
            self.bar.setRange(0, 1000)
            # 千分比：63MB 的包，百分比整数跳动看着像卡住
            self.bar.setValue(int(done * 1000 / total))
            self.status.setText(
                f"正在下载 {update.human_size(done)} / {update.human_size(total)}"
                f"（{done * 100 / total:.0f}%）"
            )
        else:
            self.bar.setRange(0, 0)
            self.status.setText(f"正在下载 {update.human_size(done)}")

    def fail(self, message: str, cancelled: bool = False) -> None:
        """下载/替换失败。取消不算失败 —— 悄悄回到原样。"""
        if cancelled:
            self.set_stage("idle")
        else:
            self.set_stage("error", message)

    # ------------------------------------------------------------------
    def _on_action(self) -> None:
        if self.stage in ("idle", "error"):
            self.set_stage("downloading", "正在连接…")
            self.install_requested.emit()

    def _on_later(self) -> None:
        if self.stage in ("downloading", "installing"):
            self.cancel_requested.emit()
            return
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Esc = 取消 / 稍后。Esc 关窗是所有人的肌肉记忆，得有。"""
        if event.key() == Qt.Key_Escape:
            self._on_later()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        # 下载中关窗 = 取消，不然线程还在后台拉
        if self.stage in ("downloading", "installing"):
            self.cancel_requested.emit()
        self.closed.emit()
        super().closeEvent(event)
