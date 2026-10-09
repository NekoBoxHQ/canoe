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
    QLabel#Status {{ font-size: 14px; color: {P.TEXT_DIM}; letter-spacing: 2px; }}
    QLabel#Error  {{ color: {P.AMBER}; font-size: 12px; }}
    QLabel#Hint   {{ color: {P.TEXT_DIM}; font-size: 12px; }}

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
    QLabel#ResultTitle {{
        font-size: 13px;
        font-weight: 600;
        color: {P.TEXT};
    }}
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

    /* ---------------- 滚动条 ---------------- */
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #2C3A48; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #3C4E60; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """
