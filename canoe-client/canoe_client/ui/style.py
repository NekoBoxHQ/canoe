"""全局 QSS —— 夜色主题（美化稿）。

配色一律取自 `canoe_core.Palette`，这里只负责把它们摆到控件上。
窗口圆角、背景画、勾选框/单选框的指示器**不在这里**：
前者在 `window_base.py` 自绘，后者在 `controls.py` 自绘。
"""
from __future__ import annotations

from canoe_core import Palette as P


def qss() -> str:
    return f"""
    * {{
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
        color: {P.TEXT};
    }}

    /* 底图和圆角由 paintEvent 画，这里全部透明 */
    QWidget#Root, QWidget#Body {{ background: transparent; }}

    /* ---------------- 标题栏 ---------------- */
    QWidget#TitleBar {{
        background: transparent;
        border-bottom: 1px solid rgba(28, 51, 88, 160);
    }}
    QLabel#WinTitle {{ font-size: 13px; font-weight: 600; color: {P.TEXT}; }}
    QLabel#WinSubtitle {{ font-size: 11px; color: {P.TEXT_FAINT}; }}

    QPushButton#WinBtn, QPushButton#WinBtnDanger {{
        background: transparent;
        border: none;
        border-radius: 6px;
        padding: 0px;
    }}
    QPushButton#WinBtn:hover {{ background: rgba(255, 255, 255, 26); }}
    QPushButton#WinBtnDanger:hover {{ background: #D64545; }}

    /* ---------------- 托盘菜单 / 悬浮提示 ----------------
     * 头一条 `* {{ color: 浅色 }}` 会连带作用到 QMenu 上：文字变近白，
     * 而弹出菜单的底还是系统默认的白 —— 白字白底，整个菜单看起来就是
     * 一块空白的白色方块（用户截图反馈的就是这个）。
     * 所以这几个控件必须把底色一起给了，光给字色不行。
     *
     * ⚠ 这里是 f-string，注释里的花括号也要写成双的 —— 写单花括号
     *   Python 会当成插值去求值，`qss()` 一调就 NameError，
     *   整个程序起不来。
     */
    QMenu {{
        background: {P.CARD};
        border: 1px solid {P.CARD_LINE};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 24px 7px 14px;
        border-radius: 7px;
        color: {P.TEXT};
    }}
    QMenu::item:selected {{ background: {P.ACCENT_DEEP}; color: #FFFFFF; }}
    QMenu::separator {{ height: 1px; margin: 5px 8px; background: {P.CARD_LINE}; }}

    QToolTip {{
        background: {P.CARD}; color: {P.TEXT};
        border: 1px solid {P.CARD_LINE}; border-radius: 6px;
        padding: 5px 8px;
    }}

    /* ---------------- 系统消息框（QMessageBox）----------------
     * 跟上面 QMenu / QToolTip 是**同一个病**：头一条 `* {{ color: 近白 }}`
     * 照样作用到 QMessageBox 上，而它的底是系统默认的浅色 —— 白字浅底，
     * "轻舟已经在运行了"那几句几乎看不见（用户截图报的）。
     *
     * 这里把整框拉成夜色，跟主界面一套配色。**每一个可见的东西都要给**：
     * 对话框底、正文 QLabel、按钮 —— 按钮不给的话就是白字浅底，照样看不清。
     *
     * ⚠ 单实例提示（app.py）和提权询问（main_view.py）走的都是 QMessageBox，
     *   规则写在这儿一次管住；以后谁再弹一个也自动是对的。
     */
    QMessageBox {{ background: {P.CARD}; }}
    QMessageBox QLabel {{ color: {P.TEXT}; }}
    QMessageBox QPushButton {{
        color: {P.TEXT};
        background: rgba(255, 255, 255, 18);
        border: 1px solid {P.CARD_LINE};
        border-radius: 8px;
        padding: 6px 18px;
        min-width: 64px;
    }}
    QMessageBox QPushButton:hover {{ background: rgba(255, 255, 255, 34); }}
    QMessageBox QPushButton:default {{
        background: {P.ACCENT_DEEP};
        border-color: {P.ACCENT_DEEP};
        color: #FFFFFF;
    }}

    /* ---------------- 卡片 ---------------- */
    QFrame#Card {{
        background: rgba(13, 27, 51, 214);
        border: 1px solid {P.CARD_LINE};
        border-radius: 14px;
    }}

    /* ---------------- 文字 ---------------- */
    QLabel#Brand {{
        font-size: 36px;
        font-weight: 700;
        letter-spacing: 10px;
        color: {P.TEXT};
    }}
    QLabel#Slogan {{
        font-size: 13px;
        letter-spacing: 1px;
        color: {P.CYAN};
    }}
    QLabel#Title {{ font-size: 21px; font-weight: 700; color: {P.TEXT}; }}
    QLabel#Subtitle {{ font-size: 12px; color: {P.TEXT_DIM}; }}

    QLabel#NodeName {{
        font-size: 27px;
        font-weight: 700;
        letter-spacing: 2px;
        color: {P.TEXT};
    }}
    /* 注意：#RouteMode 这条规则没了 —— 界面上那行"出国模式 / 回国模式"
       被用户砍掉了（"干脆不用显示，看着别闹"）。方向仍然生效，只是不显示。 */
    QLabel#Status {{ font-size: 14px; color: {P.TEXT_DIM}; letter-spacing: 2px; }}
    QLabel#Error  {{ color: {P.AMBER}; font-size: 12px; }}
    QLabel#Hint   {{ color: {P.TEXT_DIM}; font-size: 12px; }}
    /* 底部正中的版本号。压在水面上，得够淡才不抢戏，
       但也不能淡到看不见 —— TEXT_FAINT 是这套配色里最暗的一档。 */
    QLabel#Version {{ color: {P.TEXT_FAINT}; font-size: 11px; letter-spacing: 1px; }}

    /* 主界面顶部「轻舟」两侧的横线 */
    QFrame#Rule {{ background: {P.CARD_LINE}; border: none; }}
    QLabel#Kicker {{
        font-size: 13px;
        color: {P.TEXT_DIM};
        letter-spacing: 6px;
    }}

    /* ---------------- 输入框 ---------------- */
    QLineEdit {{
        background: rgba(7, 18, 36, 190);
        border: 1px solid {P.CARD_LINE};
        border-radius: 10px;
        padding: 11px 12px;
        font-size: 14px;
        color: {P.TEXT};
        selection-background-color: {P.ACCENT_DEEP};
    }}
    QLineEdit:focus {{ border: 1px solid {P.ACCENT}; }}
    QLineEdit::placeholder {{ color: {P.TEXT_FAINT}; }}

    /* ---------------- 按钮 ---------------- */
    /* 主按钮：蓝 -> 青渐变（登录 / 启航） */
    QPushButton#Primary {{
        border: none;
        border-radius: 10px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {P.ACCENT_DEEP}, stop:0.55 {P.ACCENT}, stop:1 {P.CYAN});
        color: #FFFFFF;
        font-size: 16px;
        font-weight: 700;
        letter-spacing: 3px;
    }}
    QPushButton#Primary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 #2E7BFF, stop:0.55 #4AA0FF, stop:1 #55E0F5);
    }}
    QPushButton#Primary:pressed {{ background: {P.ACCENT_DEEP}; }}
    QPushButton#Primary:disabled {{ background: {P.CARD_LINE_SOFT}; color: {P.TEXT_FAINT}; }}

    /* 次按钮：青色描边（直接体验 / 靠岸） */
    QPushButton#Outline {{
        background: rgba(34, 211, 238, 14);
        border: 1px solid rgba(34, 211, 238, 150);
        border-radius: 10px;
        color: {P.CYAN};
        font-size: 14px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QPushButton#Outline:hover {{
        background: rgba(34, 211, 238, 34);
        border-color: {P.CYAN};
    }}
    QPushButton#Outline:pressed {{ background: rgba(34, 211, 238, 52); }}

    /* 「靠岸」用中性描边，别和主按钮抢 */
    QPushButton#Dock {{
        background: rgba(20, 40, 72, 120);
        border: 1px solid {P.CARD_LINE};
        border-radius: 10px;
        color: {P.TEXT_DIM};
        font-size: 16px;
        font-weight: 700;
        letter-spacing: 3px;
    }}
    QPushButton#Dock:hover {{ border-color: {P.ACCENT}; color: {P.TEXT}; }}
    QPushButton#Dock:disabled {{ background: rgba(13, 27, 51, 90); color: #33465F; }}

    /* 纯文字按钮（离舟 / 去造舟） */
    QPushButton#Ghost {{
        background: transparent;
        border: none;
        color: {P.TEXT_DIM};
        font-size: 12px;
        font-weight: 400;
        padding: 4px 6px;
    }}
    QPushButton#Ghost:hover {{ color: {P.TEXT}; }}

    QPushButton#Link {{
        background: transparent;
        border: none;
        color: {P.CYAN};
        font-size: 13px;
        font-weight: 600;
        padding: 4px 2px;
    }}
    QPushButton#Link:hover {{ color: #7FE9FF; }}

    /* ---------------- 工具按钮：图标在上、文字在下 ---------------- */
    QToolButton#ToolUpdate, QToolButton#ToolPing, QToolButton#ToolUrl {{
        background: rgba(13, 27, 51, 190);
        border: 1px solid {P.CARD_LINE};
        border-radius: 12px;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 1px;
        padding: 6px 4px;
    }}
    QToolButton#ToolUpdate {{ color: {P.TOOL_UPDATE}; }}
    QToolButton#ToolUpdate:hover {{ background: rgba(76, 155, 255, 30); border-color: {P.TOOL_UPDATE}; }}
    QToolButton#ToolPing {{ color: {P.TOOL_PING}; }}
    QToolButton#ToolPing:hover {{ background: rgba(34, 211, 238, 30); border-color: {P.TOOL_PING}; }}
    QToolButton#ToolUrl {{ color: {P.TOOL_URL}; }}
    QToolButton#ToolUrl:hover {{ background: rgba(167, 139, 250, 30); border-color: {P.TOOL_URL}; }}
    QToolButton#ToolUpdate:disabled, QToolButton#ToolPing:disabled,
    QToolButton#ToolUrl:disabled {{ color: {P.TEXT_FAINT}; border-color: {P.CARD_LINE_SOFT}; }}

    /* ---------------- 输出结果 ---------------- */
    /* 那排「输出结果」标题（连图标）被用户砍了 —— 就一行字的东西，标题比
       内容还显眼。它的 #ResultTitle 规则一并删掉，别留死样式。 */
    QLabel#ResultText {{
        font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
        font-size: 15px;
        color: {P.GREEN};
    }}
    QFrame#ResultRow {{
        background: rgba(7, 18, 36, 150);
        border: 1px solid {P.CARD_LINE_SOFT};
        border-radius: 10px;
    }}
    QLabel#Dot {{
        background: {P.GREEN};
        border-radius: 4px;
    }}

    /* ---------------- 更新弹窗 ----------------
     * 这一块是给 ui/update_dialog.py 用的。原来"有新版本"是个系统
     * QMessageBox：它是浅色底，而上面那条 `* {{ color: 近白 }}` 照样
     * 作用到它身上 —— 白字白底，整个框的字都是灰的看不清（用户截图报的）。
     * 所以更新这条路整个换成自家的无边框窗口。
     */
    QLabel#UpdateVersion {{
        font-size: 15px;
        font-weight: 700;
        color: {P.TEXT};
    }}
    QLabel#UpdateVersion b {{ color: {P.CYAN}; }}
    QLabel#UpdateCaption {{ color: {P.TEXT}; font-size: 12px; font-weight: 600; }}
    QLabel#UpdateNotes {{ color: {P.TEXT_DIM}; font-size: 12px; }}
    QLabel#UpdateStatus {{ color: {P.TEXT_DIM}; font-size: 12px; }}
    QLabel#UpdateError {{ color: {P.AMBER}; font-size: 12px; }}
    QScrollArea#NotesScroll, QScrollArea#NotesScroll > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}

    QProgressBar#UpdateBar {{
        background: rgba(7, 18, 36, 190);
        border: 1px solid {P.CARD_LINE};
        border-radius: 6px;
        min-height: 10px;
        max-height: 10px;
        /* 百分比用旁边那行字说，条上不叠字（小尺寸上糊成一坨） */
        text-align: center;
        color: transparent;
    }}
    QProgressBar#UpdateBar::chunk {{
        border-radius: 5px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {P.ACCENT}, stop:1 {P.CYAN});
    }}

    /* ---------------- 滚动条 ---------------- */
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #2C3A48; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #3C4E60; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """
