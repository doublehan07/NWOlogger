"""About NWOlogger: version, authors, credits, system info."""

from __future__ import annotations

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout

from .. import APP_NAME, __version__, applog, i18n
from ..core.db import SCHEMA_VERSION
from . import theme

tr = i18n.tr

REPO = "https://github.com/doublehan07/NWOlogger"
LOGO = theme.ICONS.parent / "brand" / "by1qh.png"


class AboutDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(tr("About NWOlogger"))
        self.resize(620, 560)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(str(LOGO))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(132, 132, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        head.addWidget(logo)
        title = QLabel(f"<div style='font-size:24px;font-weight:700'>{APP_NAME}</div>"
                       f"<div style='font-size:14px'>v{__version__}</div>"
                       f"<p>{tr('A minimal, cross-platform amateur radio logger.')}</p>"
                       f"<p><a href='{REPO}'>{REPO}</a></p>")
        title.setOpenExternalLinks(True)
        title.setWordWrap(True)
        head.addWidget(title, 1)
        lay.addLayout(head)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        mut = theme.tokens().muted
        about = QTextBrowser()
        about.setOpenExternalLinks(True)
        about.setHtml(
            f"<h3>{tr('Authors')}</h3>"
            "<p>Han Zhang (BI1NWO)<br>Claude Opus 5.5 Extra High (Anthropic)<br>"
            "GPT-6.1 Sol Ultra (OpenAI)</p>"
            f"<h3>{tr('Copyright')}</h3>"
            f"<p>© 2026 Han Zhang. {tr('Released under the MIT License.')}</p>"
            f"<h3>{tr('Special thanks')}</h3>"
            f"<p><b>BY1QH</b> · {tr('club station, China')} · "
            "<a href='https://www.qrz.com/db/BY1QH'>qrz.com/db/BY1QH</a><br>"
            f"<span style='color:{mut}'>{tr('The BY1QH logo is used as the NWOlogger icon.')}</span></p>")
        tabs.addTab(about, tr("About"))
        credits = QTextBrowser()
        credits.setOpenExternalLinks(True)
        credits.setHtml(
            "<ul>"
            "<li><a href='https://www.country-files.com/'>AD1C country files</a> (cty.csv): "
            + tr("countries, DXCC and zones") + "</li>"
            "<li><a href='https://www.supercheckpartial.com/'>Super Check Partial</a> (MASTER.SCP)</li>"
            "<li><a href='https://lotw.arrl.org/'>ARRL LoTW / TQSL</a>: " + tr("satellite and propagation lists") +
            "</li><li><a href='https://adif.org.uk/'>ADIF</a> 3.1.8</li>"
            "<li><a href='https://lucide.dev/'>Lucide</a> " + tr("icons (ISC License)") + "</li>"
            "<li><a href='https://www.qt.io/qt-for-python'>Qt for Python (PySide6)</a> (LGPL)</li>"
            "<li>httpx, keyring</li>"
            "<li>" + tr("Grid-map visualization inspired by {gridmaster} and {gridtracker}.").format(
                gridmaster="<a href='https://gridmaster.fr/'>GridmasterMap (FG8OJ)</a>",
                gridtracker="<a href='https://gridtracker.org/'>GridTracker</a>") + "</li>"
            "<li>" + tr("Reference programs: MacLoggerDX, N1MM Logger+, BBLogger, Wavelog, Club Log") + "</li>"
            "</ul>")
        tabs.addTab(credits, tr("Credits"))
        info = QTextBrowser()
        self.info_text = (f"{APP_NAME} {__version__}\nQt {qVersion()} · PySide6 {PYSIDE_VERSION}\n"
                          f"{tr('Database schema')} v{SCHEMA_VERSION}\n{tr('Log file')}: {db.path}\n"
                          f"{tr('Backups')}: {db.backup_dir}\n"
                          f"{tr('Diagnostic log')}: {applog.tidy_path(applog.log_file())}")
        info.setPlainText(self.info_text)
        info.setFont(theme.mono_font())
        tabs.addTab(info, tr("System"))
        bar = QHBoxLayout()
        copy = QPushButton(tr("Copy system info"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.info_text))
        close = QPushButton(tr("Close"))
        close.setProperty("primary", True)
        close.clicked.connect(self.accept)
        bar.addWidget(copy)
        bar.addStretch(1)
        bar.addWidget(close)
        lay.addLayout(bar)
