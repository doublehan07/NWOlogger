"""Design tokens, light/dark stylesheet, palette, and theme-coloured SVG icons."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QIconEngine,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ICONS = Path(__file__).resolve().parent.parent / "resources" / "icons"


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    accent_soft: str
    ok: str
    ok_soft: str
    warn: str
    warn_soft: str
    bad: str
    bad_soft: str
    tint: str


LIGHT = Tokens(
    "light", bg="#F5F6F8", surface="#FFFFFF", surface_alt="#F1F3F6", border="#E1E5EA", border_strong="#CBD2DA",
    text="#1B1F24", muted="#5F6B7A", accent="#2563EB", accent_hover="#1D4ED8", accent_text="#FFFFFF",
    accent_soft="#E3ECFD", ok="#15803D", ok_soft="#E5F5EA", warn="#B45309", warn_soft="#FCF1DC", bad="#C42727",
    bad_soft="#FCE8E8", tint="#EFF5FF",
)
DARK = Tokens(
    "dark", bg="#14171C", surface="#1C2026", surface_alt="#22272E", border="#2C323A", border_strong="#3A414B",
    text="#E6E9ED", muted="#97A1AE", accent="#4C8DFF", accent_hover="#6CA2FF", accent_text="#FFFFFF",
    accent_soft="#1E3152", ok="#4ADE80", ok_soft="#15301F", warn="#FBBF24", warn_soft="#382A0E", bad="#F87171",
    bad_soft="#3A1A1A", tint="#1B2942",
)

_current: Tokens = LIGHT
_mode = "system"


def tokens() -> Tokens:
    return _current


def mono_font(size: int | None = None, bold: bool = False) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    for family in ("SF Mono", "Menlo", "Cascadia Mono", "Consolas"):
        if family in QFontDatabase.families():
            f = QFont(family)
            break
    if size:
        f.setPointSize(size)
    f.setBold(bold)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def mono_family() -> str:
    return mono_font().family()


def stylesheet(t: Tokens) -> str:
    mono = mono_family()
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t.text}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {t.bg}; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong}; padding: 4px 6px; }}

QFrame#Section {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px; }}
QLabel#SectionTitle {{ color: {t.muted}; font-weight: 600; font-size: 11px; letter-spacing: 0.5px; }}
QLabel[muted="true"] {{ color: {t.muted}; }}
QLabel#Summary {{ color: {t.muted}; }}
QLabel#Version, QToolButton#Version {{ color: {t.muted}; padding: 0 6px; }}
QToolButton#Version:hover {{ color: {t.accent}; }}
QLabel#Clock {{ font-family: "{mono}"; font-size: 13px; padding: 0 6px; }}
QLabel#BandLabel {{ color: {t.accent}; font-weight: 600; font-family: "{mono}"; }}
QLabel#Hint {{ color: {t.muted}; font-size: 11px; }}

QLineEdit, QComboBox, QDateTimeEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 6px; padding: 3px 7px;
    selection-background-color: {t.accent}; selection-color: {t.accent_text}; min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QDateTimeEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QDateEdit:focus {{ border: 1px solid {t.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.muted}; background: {t.surface_alt}; }}
QLineEdit[autofill="true"], QComboBox[autofill="true"] {{ background: {t.tint}; }}
QDateTimeEdit[invalid="true"] {{ border: 1px solid {t.bad}; color: {t.bad}; }}
QPushButton#SyncBtn {{ padding: 3px 8px; font-family: "{mono}"; }}
QPushButton#SyncBtn:checked {{ background: {t.accent_soft}; color: {t.accent}; border: 1px solid {t.accent}; }}
QLineEdit[opstate="bad"] {{ border: 2px solid {t.bad}; }}
QLineEdit[opstate="check"] {{ border: 2px solid {t.warn}; background: {t.warn_soft}; }}
QLineEdit[guess="true"] {{ background: {t.tint}; font-style: italic; color: {t.muted}; }}
QLineEdit[mono="true"] {{ font-family: "{mono}"; }}
QLineEdit#CallEdit {{ font-family: "{mono}"; font-size: 22px; font-weight: 600; padding: 5px 10px;
    letter-spacing: 1px; }}
QLineEdit#CallEdit[dupe="DUPE"] {{ border: 2px solid {t.bad}; color: {t.bad}; }}
QLineEdit#CallEdit[dupe="SUSPECT"] {{ border: 2px solid {t.warn}; }}
QLineEdit#ContestCall {{ font-family: "{mono}"; font-size: 20px; font-weight: 600; padding: 4px 8px; }}
QLineEdit#ContestCall[dupe="DUPE"] {{ border: 2px solid {t.bad}; color: {t.bad}; }}
QLineEdit#ContestCall[dupe="SUSPECT"] {{ border: 2px solid {t.warn}; }}
QLineEdit#ContestField {{ font-family: "{mono}"; font-size: 16px; padding: 4px 8px; }}
QLineEdit#ContestField[invalid="true"] {{ border: 1px solid {t.bad}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; border: 1px solid {t.border_strong};
    selection-background-color: {t.accent_soft}; selection-color: {t.text}; }}

QPushButton {{ background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 6px;
    padding: 5px 12px; min-height: 18px; }}
QPushButton:hover {{ background: {t.surface_alt}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.surface_alt}; border-color: {t.border}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent};
    font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t.accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {t.surface_alt}; color: {t.muted}; border-color: {t.border}; }}
QPushButton[danger="true"] {{ color: {t.bad}; }}
QPushButton[flat="true"] {{ border: none; background: transparent; }}
QPushButton[flat="true"]:hover {{ background: {t.surface_alt}; }}

QToolBar {{ background: {t.surface}; border: none; border-bottom: 1px solid {t.border}; spacing: 2px;
    padding: 4px 8px; }}
QToolBar::separator {{ background: {t.border}; width: 1px; margin: 4px 6px; }}
QToolButton {{ border: none; border-radius: 6px; padding: 4px 8px; }}
QToolButton:hover {{ background: {t.surface_alt}; }}
QToolButton:pressed {{ background: {t.border}; }}
QToolButton:checked {{ background: {t.accent_soft}; color: {t.accent}; font-weight: 600; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: 10px; background: {t.surface}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 6px 14px; margin-right: 2px; border: none;
    border-bottom: 2px solid transparent; color: {t.muted}; }}
QTabBar::tab:selected {{ color: {t.accent}; border-bottom: 2px solid {t.accent}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t.text}; }}

QTableView {{ background: {t.surface}; alternate-background-color: {t.surface_alt}; gridline-color: {t.border};
    border: none; selection-background-color: {t.accent_soft}; selection-color: {t.text}; }}
QTableView::item {{ padding: 0 4px; }}
QHeaderView::section {{ background: {t.surface_alt}; color: {t.muted}; border: none;
    border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border}; padding: 4px 6px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {t.text}; }}
QTableCornerButton::section {{ background: {t.surface_alt}; border: none; }}
QListWidget, QTreeWidget {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; }}
QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}

QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; color: {t.muted}; }}
QStatusBar QLabel {{ color: {t.muted}; padding: 0 8px; }}

QLabel#Chip {{ border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
QLabel#Chip[kind="bad"] {{ background: {t.bad_soft}; color: {t.bad}; }}
QLabel#Chip[kind="warn"] {{ background: {t.warn_soft}; color: {t.warn}; }}
QLabel#Chip[kind="ok"] {{ background: {t.ok_soft}; color: {t.ok}; }}
QLabel#Chip[kind="info"] {{ background: {t.accent_soft}; color: {t.accent}; }}
QLabel#Chip[kind="muted"] {{ background: {t.surface_alt}; color: {t.muted}; }}
QLabel#Big {{ font-size: 28px; font-weight: 700; font-family: "{mono}"; }}
QLabel#Score {{ font-size: 22px; font-weight: 700; color: {t.accent}; font-family: "{mono}"; }}

QMenu {{ background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}
QMenuBar {{ background: {t.surface}; }}
QMenuBar::item:selected {{ background: {t.accent_soft}; }}

QGroupBox {{ border: 1px solid {t.border}; border-radius: 8px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.muted}; font-weight: 600; }}
QCheckBox {{ spacing: 6px; }}
QSplitter::handle {{ background: {t.bg}; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.border_strong}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border_strong}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {t.surface_alt}; padding: 5px 8px; border-bottom: 1px solid {t.border}; }}
QProgressBar {{ border: 1px solid {t.border}; border-radius: 6px; background: {t.surface_alt}; text-align: center;
    height: 14px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 6px; }}
QDialog#Palette {{ background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 12px; }}
QLineEdit#PaletteEdit {{ font-size: 16px; padding: 8px 10px; border: none; border-bottom: 1px solid {t.border};
    border-radius: 0; background: transparent; }}
QListWidget#PaletteList {{ border: none; }}
"""


def palette(t: Tokens) -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.ColorRole.Window, c(t.bg))
    p.setColor(QPalette.ColorRole.WindowText, c(t.text))
    p.setColor(QPalette.ColorRole.Base, c(t.surface))
    p.setColor(QPalette.ColorRole.AlternateBase, c(t.surface_alt))
    p.setColor(QPalette.ColorRole.Text, c(t.text))
    p.setColor(QPalette.ColorRole.Button, c(t.surface))
    p.setColor(QPalette.ColorRole.ButtonText, c(t.text))
    p.setColor(QPalette.ColorRole.Highlight, c(t.accent))
    p.setColor(QPalette.ColorRole.HighlightedText, c(t.accent_text))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(t.surface))
    p.setColor(QPalette.ColorRole.ToolTipText, c(t.text))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(t.muted))
    p.setColor(QPalette.ColorRole.Link, c(t.accent))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, c(t.muted))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c(t.muted))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c(t.muted))
    return p


def system_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def apply(app: QApplication, mode: str = "system") -> Tokens:
    """Apply 'system', 'light' or 'dark'."""
    global _current, _mode
    _mode = mode
    dark = system_is_dark() if mode == "system" else mode == "dark"
    _current = DARK if dark else LIGHT
    app.setStyle("Fusion")
    app.setPalette(palette(_current))
    app.setStyleSheet(stylesheet(_current))
    return _current


def watch_system(app: QApplication, on_change=None) -> None:
    hints = QGuiApplication.styleHints()
    try:
        def changed(*_):
            if _mode == "system":
                apply(app, "system")
                if on_change:
                    on_change()
        hints.colorSchemeChanged.connect(changed)
    except AttributeError:
        pass


@lru_cache(maxsize=1024)
def _render(name: str, color: str, width: int, height: int, scale: float) -> QPixmap:
    svg = (ICONS / f"{name}.svg").read_text(encoding="utf-8").replace("currentColor", color)
    pm = QPixmap(max(1, round(width * scale)), max(1, round(height * scale)))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, pm.width(), pm.height()))
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


class _ThemedIcon(QIconEngine):
    """Draws the SVG in the current theme's colours whenever it is painted, so every icon follows a theme switch
    (also one macOS makes under Follow system) without being set again."""

    def __init__(self, name: str, color: str | None):
        super().__init__()
        self.name, self.color = name, color

    def clone(self) -> QIconEngine:
        return _ThemedIcon(self.name, self.color)

    def _color(self, mode: QIcon.Mode) -> str:
        if mode == QIcon.Mode.Disabled:
            return _current.muted
        if self.color is None:
            return _current.text
        return getattr(_current, self.color, self.color)  # a token name ("muted") or a colour ("#FFFFFF")

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:  # noqa: N802
        return _render(self.name, self._color(mode), size.width(), size.height(), float(scale))

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def paint(self, painter: QPainter, rect, mode: QIcon.Mode, state: QIcon.State) -> None:
        device = painter.device()
        scale = device.devicePixelRatioF() if device is not None else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, scale))


@lru_cache(maxsize=256)
def icon(name: str, color: str | None = None) -> QIcon:
    """A Lucide icon in the theme's text colour, or in ``color``: a token name such as ``"muted"`` (it follows the
    theme) or a fixed colour such as ``"#FFFFFF"``."""
    if not (ICONS / f"{name}.svg").exists():
        return QIcon()
    return QIcon(_ThemedIcon(name, color))


def repolish(w) -> None:
    """Re-apply the stylesheet after a dynamic property change."""
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()
