"""全局 QSS。配色取自 canoe_core.Palette（深蓝 / 墨青 / 白，冷淡风）。"""
from __future__ import annotations

from canoe_core import Palette as P


def qss() -> str:
    return f"""
    * {{
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
        color: {P.WHITE};
    }}
    QWidget#Root {{ background: {P.INK}; }}

    QLabel#Brand {{
        font-size: 22px;
        font-weight: 600;
        letter-spacing: 6px;
        color: {P.WHITE};
    }}
    QLabel#Slogan {{
        font-size: 11px;
        letter-spacing: 1px;
        color: {P.DIM};
    }}
    QLabel#Title {{
        font-size: 18px;
        font-weight: 600;
        color: {P.WHITE};
    }}
    QLabel#Subtitle {{ color: {P.MUTED}; font-size: 12px; }}

    QLabel#NodeName {{
        font-size: 25px;
        font-weight: 600;
        letter-spacing: 2px;
        color: {P.WHITE};
    }}
    QLabel#Status {{ font-size: 13px; color: {P.MUTED}; letter-spacing: 1px; }}
    QLabel#Error  {{ color: {P.WARN}; font-size: 12px; }}
    QLabel#Hint   {{ color: {P.DIM}; font-size: 11px; }}

    QLineEdit {{
        background: {P.SURFACE};
        border: 1px solid {P.LINE};
        border-radius: 8px;
        padding: 9px 12px;
        selection-background-color: {P.DEEP_BLUE};
    }}
    QLineEdit:focus {{ border: 1px solid {P.DEEP_BLUE}; }}

    QPushButton {{
        background: {P.DEEP_BLUE};
        border: none;
        border-radius: 8px;
        padding: 10px 18px;
        font-weight: 600;
        color: {P.WHITE};
        letter-spacing: 1px;
    }}
    QPushButton:hover   {{ background: {P.DEEP_BLUE_HOVER}; }}
    QPushButton:pressed {{ background: #17406F; }}
    QPushButton:disabled {{ background: {P.LINE}; color: {P.DIM}; }}

    QPushButton#Guest {{
        background: transparent;
        border: 1px solid {P.LINE};
        color: {P.INK_CYAN};
        font-size: 12px;
        font-weight: 400;
        letter-spacing: 0px;
    }}
    QPushButton#Guest:hover {{
        border-color: {P.INK_CYAN};
        color: {P.INK_CYAN_HOVER};
    }}

    QPushButton#Ghost {{
        background: transparent;
        color: {P.MUTED};
        font-weight: 400;
        padding: 4px 6px;
        letter-spacing: 0px;
    }}
    QPushButton#Ghost:hover {{ color: {P.WHITE}; }}

    /* --- 三个工具按钮：各自一个色调，靠描边和微光区分 --- */
    QPushButton#ToolUpdate, QPushButton#ToolPing, QPushButton#ToolUrl {{
        background: {P.SURFACE};
        border-radius: 10px;
        font-size: 12px;
        font-weight: 500;
        letter-spacing: 1px;
        padding: 4px;
    }}
    QPushButton#ToolUpdate {{
        border: 1px solid #2F6FA8;
        color: #6FA8DC;
    }}
    QPushButton#ToolUpdate:hover {{ background: #16304A; border-color: #4A8FD0; }}
    QPushButton#ToolPing {{
        border: 1px solid {P.INK_CYAN};
        color: {P.INK_CYAN_HOVER};
    }}
    QPushButton#ToolPing:hover {{ background: #123037; border-color: #3FB3C0; }}
    QPushButton#ToolUrl {{
        border: 1px solid #6B5AA8;
        color: #A08FE0;
    }}
    QPushButton#ToolUrl:hover {{ background: #251E3D; border-color: #8A76D0; }}
    QPushButton#ToolUpdate:disabled, QPushButton#ToolPing:disabled,
    QPushButton#ToolUrl:disabled {{
        border-color: {P.LINE};
        color: {P.DIM};
    }}

    /* --- 测试结果框：小、绿色、大字 --- */
    QLabel#LogTitle {{
        color: {P.MUTED};
        font-size: 11px;
        letter-spacing: 1px;
    }}
    QPlainTextEdit#ResultView {{
        background: {P.INK};
        border: 1px solid {P.LINE};
        border-radius: 8px;
        padding: 6px 10px;
        font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
        /* 放大一倍的绿色大字 —— 结果就那几行，小字浪费空间 */
        font-size: 28px;
        font-weight: 700;
        color: #3FD07A;
        selection-background-color: {P.DEEP_BLUE};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #2C3A48;
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #3C4E60; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QPushButton#Launch {{
        background: {P.INK_CYAN};
        font-size: 15px;
        padding: 14px 30px;
        letter-spacing: 4px;
    }}
    QPushButton#Launch:hover {{ background: {P.INK_CYAN_HOVER}; }}
    QPushButton#Launch:disabled {{ background: {P.LINE}; color: {P.DIM}; }}

    QPushButton#Dock {{
        background: {P.SURFACE};
        color: {P.MUTED};
        font-size: 15px;
        padding: 14px 30px;
        letter-spacing: 4px;
        border: 1px solid {P.LINE};
    }}
    QPushButton#Dock:hover {{ border-color: {P.WARN}; color: {P.WARN}; }}
    QPushButton#Dock:disabled {{ background: {P.INK}; color: #33404E; border-color: #1A2532; }}

    QRadioButton {{ spacing: 6px; color: {P.MUTED}; }}
    QRadioButton::indicator {{ width: 13px; height: 13px; }}
    QRadioButton:checked {{ color: {P.WHITE}; }}

    QFrame#Card {{
        background: {P.SURFACE};
        border: 1px solid {P.LINE};
        border-radius: 12px;
    }}
    """
