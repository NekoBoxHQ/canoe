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
