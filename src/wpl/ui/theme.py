"""Colors and stylesheet. Light palette follows mockup A (warm paper, green accent); layout follows mockup C."""
from __future__ import annotations

LIGHT = {"bg": "#faf9f6", "surface": "#ffffff", "nav": "#efede8", "text": "#1d1d1f", "muted": "#5f5f64", "line": "#e0ddd5",
         "accent": "#2f5d50", "accent_text": "#ffffff", "warn": "#8a5a00", "warn_bg": "#fff4dc", "err": "#a3201c",
         "err_bg": "#fde8e6", "syn": "#5b3fa0", "syn_bg": "#efe9fb", "ok": "#1f6b3a", "ins": "#d6f2df", "del": "#fbd9d6"}
DARK = {"bg": "#171716", "surface": "#212120", "nav": "#1c1c1b", "text": "#ececea", "muted": "#a8a8a2", "line": "#3a3a35",
        "accent": "#7fc4ad", "accent_text": "#0e1a16", "warn": "#f0c36a", "warn_bg": "#3a2e12", "err": "#ff9b94",
        "err_bg": "#3d1b19", "syn": "#c3b0f5", "syn_bg": "#2a2340", "ok": "#7fd69d", "ins": "#1f4a31", "del": "#5a2420"}
SERIES = ["#2f5d50", "#b5651d", "#5b3fa0", "#1d6fa5", "#a3201c", "#6b6b00"]
SERIES_DARK = ["#7fc4ad", "#e0a060", "#c3b0f5", "#7fb8e6", "#ff9b94", "#d6d65a"]

_current = dict(LIGHT)
_dark = False


def colors() -> dict[str, str]:
    return _current


def series() -> list[str]:
    return SERIES_DARK if _dark else SERIES


def set_theme(dark: bool) -> None:
    global _current, _dark
    _current = dict(DARK if dark else LIGHT)
    _dark = dark


def stylesheet() -> str:
    c = _current
    return f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 10.5pt; }}
QMainWindow, QWidget#page, QDialog {{ background: {c['bg']}; color: {c['text']}; }}
QWidget {{ color: {c['text']}; }}
QListWidget#nav {{ background: {c['nav']}; border: 0; border-right: 1px solid {c['line']}; padding: 8px 6px; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 12px; border-radius: 6px; margin: 1px 0; }}
QListWidget#nav::item:selected {{ background: {c['surface']}; color: {c['text']}; border-left: 3px solid {c['accent']}; }}
QListWidget#nav::item:hover {{ background: {c['line']}; }}
QFrame#card {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 8px; }}
QLabel#h1 {{ font-size: 18pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#big {{ font-size: 20pt; font-weight: 600; }}
QLabel#badge {{ background: {c['syn_bg']}; color: {c['syn']}; border-radius: 9px; padding: 2px 10px; font-weight: 600; font-size: 9pt; }}
QLabel#banner_err {{ background: {c['err_bg']}; color: {c['err']}; border-radius: 6px; padding: 8px 12px; }}
QLabel#banner_info {{ background: {c['syn_bg']}; color: {c['syn']}; border-radius: 6px; padding: 8px 12px; }}
QLabel#banner_warn {{ background: {c['warn_bg']}; color: {c['warn']}; border-radius: 6px; padding: 8px 12px; }}
QPushButton {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 6px 16px; }}
QPushButton:hover {{ background: {c['bg']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton:focus {{ border: 2px solid {c['accent']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 600; padding: 9px 22px; }}
QPushButton#danger {{ color: {c['err']}; border-color: {c['err']}; }}
QPushButton[chip="true"] {{ padding: 7px 14px; border-radius: 8px; }}
QPushButton[chip="true"]:checked {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 600; }}
QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit, QComboBox {{
  background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 6px; padding: 4px 6px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid {c['accent']}; }}
QPlainTextEdit#editor, QTextBrowser#editor {{ font-family: Georgia, "Times New Roman", serif; font-size: 13pt; padding: 12px 14px; }}
QTableWidget {{ background: {c['surface']}; border: 1px solid {c['line']}; gridline-color: {c['line']}; alternate-background-color: {c['bg']}; }}
QHeaderView::section {{ background: {c['nav']}; border: 0; border-bottom: 1px solid {c['line']}; padding: 5px 8px; font-weight: 600; color: {c['muted']}; }}
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ padding: 8px 10px; color: {c['muted']}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QProgressBar {{ background: {c['line']}; border: 0; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QGroupBox {{ border: 1px solid {c['line']}; border-radius: 8px; margin-top: 12px; padding: 12px; background: {c['surface']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; font-weight: 600; }}
QCheckBox:focus {{ outline: 2px solid {c['accent']}; }}
"""
