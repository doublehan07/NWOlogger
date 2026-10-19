"""Main window: toolbar, entry panel (DX / Contest), Log · Paper QSL · Sync history tabs."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, applog, i18n
from ..core import (
    backup_plan,
    calllists,
    dupes,
    dxcc,
    exporter,
    mirror,
    net,
    regions,
    station,
    timeutil,
    uploads,
)
from ..core import db as dbm
from ..core.contest import scp as scpmod
from ..core.fields import DEFAULT_LOG_COLUMNS
from ..core.lookup import clients as lookup_clients
from ..core.operators import OperatorSession
from ..core.sync import positions
from . import theme
from .about_dialog import AboutDialog
from .backup_reminder import BackupReminder
from .bulk_edit import BulkEditDialog
from .command_palette import CommandPalette
from .dupe_dialog import DupeCompareDialog, ignore_all, show_review
from .entry.dx_panel import DXPanel
from .import_dialog import ImportDialog
from .log.filter_bar import FilterBar
from .log.log_model import LogModel
from .log.log_view import LogView
from .log.search_dialog import SearchDialog
from .lookup_fill import LookupFillDialog
from .prefs_dialog import PrefsDialog
from .qsl_paper import PaperQslTab, mark_paper
from .stats.statistics_tab import StatisticsTab
from .sync_controller import SyncController, clear_history
from .widgets import ScrollArea, fit_to_screen, fmt, run_async

tr = i18n.tr


class MainWindow(QMainWindow):
    # The language changed: build the window again in the new language (app.py does, keeping this one's state).
    rebuild_requested = Signal()

    def __init__(self, db: dbm.LogDB, prefs, app: QApplication, *, ops: OperatorSession | None = None,
                 first_run: bool = True):
        super().__init__()
        self.db, self.prefs, self.app = db, prefs, app
        self.actions_: list[QAction] = []
        self.ops = ops or OperatorSession()
        self._rebuild_waiting = False
        self.sync = SyncController(db, prefs, self)
        self.sync.message.connect(self.flash)
        self.sync.prefs_requested.connect(self.open_prefs)
        self.sync.history_requested.connect(self._show_history)
        self.sync.idle.connect(self._sync_idle)
        self.sync.confirmed.connect(self._select_confirmed)
        self.sync.select_requested.connect(self._show_ids)
        self.sync.edit_requested.connect(self.edit_qso)  # Possible duplicates → Edit my QSO
        self._confirmed_sel: set[int] | None = None  # what the last download selected, while you haven't clicked
        self._lookup_clients: list | None = None
        self._screen = None
        self.resize(1480, 920)
        self._build()
        self._update_title()
        restore = prefs.value("window/geometry")
        if restore:
            self.restoreGeometry(restore)
        state = prefs.value("window/splitter")
        if state:
            self.splitter.restoreState(state)
        self.db.add_listener(self._on_db_change)
        self._update_status()
        self._checkpoint_timer = QTimer(self)  # fold the WAL into the file a moment after the last change
        self._checkpoint_timer.setSingleShot(True)
        self._checkpoint_timer.setInterval(2000)
        self._checkpoint_timer.timeout.connect(self.db.idle_checkpoint)
        if first_run:
            QTimer.singleShot(0, self._first_run)
        self._reminder: BackupReminder | None = None
        self._plan_timer = QTimer(self)  # the regular backup plan: a minute after the start, then hourly
        self._plan_timer.setInterval(3_600_000)
        self._plan_timer.timeout.connect(self._backup_plan)
        self._plan_timer.start()
        QTimer.singleShot(60_000, self._backup_plan)

    # ------------------------------------------------------------------ build
    def act(self, text: str, slot, shortcut: str | QKeySequence | None = None, icon: str | None = None,
            tip: str = "") -> QAction:
        a = QAction(tr(text), self)
        if icon:
            a.setIcon(theme.icon(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(lambda *_: slot())
        self.addAction(a)
        self.actions_.append(a)
        return a

    def _build(self) -> None:
        A = self.act  # noqa: N806
        self.a_import = A("Import ADIF or MacLoggerDX…", self.import_file, QKeySequence.StandardKey.Open, "file-down")
        self.a_export_all = A("Export all to ADIF…", lambda: self.export("all"), None, "file-up")
        self.a_export_sel = A("Export selected to ADIF…", lambda: self.export("selected"), "Ctrl+Shift+E")
        self.a_export_flt = A("Export filtered to ADIF…", lambda: self.export("filtered"))
        self.a_new_log = A("New log file…", self.new_log)
        self.a_open_log = A("Open log file…", self.open_log)
        self.a_add_log = A("Add log file…", self.add_log)
        self.a_save_copy = A("Save a copy…", self.save_copy)
        self.a_backup = A("Back up now", lambda: self.flash(tr("Backup saved") + f": {self.db.backup('manual')}"))
        self.a_compact = A("Compact log file…", self.compact_log)
        self.a_quit = A("Quit", self.close, QKeySequence.StandardKey.Quit)
        self.a_bulk = A("Bulk edit selected…", self.bulk_edit, "Ctrl+B", "pencil")
        self.a_edit = A("Edit QSO", self.edit_current, "Ctrl+E")
        self.a_delete = A("Delete selected QSOs", self.delete_selected, "Ctrl+Backspace", "trash-2")
        self.a_dupes = A("Find duplicates in the whole log", self.find_dupes, None, "copy-check")
        self.a_not_dupe = A("Not a dupe", self.not_dupe)
        self.a_show_dupe = A("Compare with the original…", self.show_dupe_partner)
        self.a_review = A("Review suspicious dupes…", self.review_dupes, "Ctrl+Shift+D", "copy-check")
        self.a_merge_sel = A("Merge selected…", self.merge_selected)
        self.a_ignore_all = A("Ignore all marked dupes…", self.ignore_all_dupes)
        self.a_operator = A("Change operator…", self.change_operator, "Ctrl+Shift+O", "user-round")
        self.a_paper_sent = A("Mark paper QSL sent…", lambda: mark_paper(self, self.db, self.view.selected_ids(),
                                                                        "sent"))
        self.a_paper_rcvd = A("Mark paper QSL received…", lambda: mark_paper(self, self.db,
                                                                            self.view.selected_ids(), "rcvd"))
        self.a_paper_queue = A("Queue paper QSL to send", lambda: mark_paper(self, self.db,
                                                                            self.view.selected_ids(), "queue"))
        self.a_paper_not_sent = A("Mark paper QSL not sent", lambda: mark_paper(self, self.db,
                                                                               self.view.selected_ids(), "not_sent"))
        self.a_paper_not_rcvd = A("Mark paper QSL not received", lambda: mark_paper(self, self.db,
                                                                                   self.view.selected_ids(), "not_rcvd"))
        self.a_recompute = A("Recompute country, DXCC and zones", self.recompute_dxcc)
        self.a_cty = A("Update country file (cty.csv)", lambda: self.update_cty(quiet=False))
        self.a_scp = A("Update MASTER.SCP (similar calls)", self.update_scp)
        self.a_lists = A("Update LoTW and eQSL call lists (similar calls)", lambda: self.update_call_lists(quiet=False))
        self.a_lookup_fill = A("Look up and fill missing fields", self.lookup_fill_selected)
        self.a_select_all = A("Select all shown", lambda: self.view.selectAll(), QKeySequence.StandardKey.SelectAll)
        self.a_prefs = A("Preferences…", self.open_prefs, QKeySequence.StandardKey.Preferences, "settings")
        self.a_profiles = A("Station profiles…", lambda: self.open_prefs("Station profiles"), None, "user-round")
        self.a_palette = A("Command palette…", self.palette, "Ctrl+K", "command")
        self.a_search = A("Search log…", self.advanced_search, QKeySequence.StandardKey.Find, "search")
        self.a_focus_call = A("Focus the call field", self.focus_call, "Ctrl+L")
        self.a_log_tab = A("Show the log", lambda: self.tabs.setCurrentIndex(0), "Ctrl+1")
        self.a_paper_tab = A("Show paper QSL", lambda: self.tabs.setCurrentIndex(1), "Ctrl+2")
        self.a_hist_tab = A("Show sync history", lambda: self.tabs.setCurrentIndex(2), "Ctrl+3")
        self.a_stats_tab = A("Show statistics (grid map)", lambda: self.tabs.setCurrentIndex(3), "Ctrl+4")
        self.a_about = A("About NWOlogger", self.about)
        self.a_logfile_folder = A("Show log file folder", self.show_logfile_folder)
        self.a_backups_folder = A("Show backups folder", self.show_backups_folder)
        self.a_log_folder = A("Show diagnostic log folder", self.show_log_folder)
        self.a_mirror_folder = A("Show the backup copy in Documents", self.show_mirror_folder)
        self.a_results = A("Show sync results", self.show_sync_results)
        self.a_clear_hist = A("Clear sync history…", self.clear_sync_history)
        self.a_clear_filters = A("Clear filters", lambda: self.filter.clear(), "Ctrl+Shift+K")
        self.a_suspicious = A("Show suspicious dupes", lambda: self.filter.set_preset("suspicious"))
        sync_acts = {}
        for svc, name in (("lotw", "LoTW"), ("eqsl", "eQSL"), ("qrz", "QRZ.com"), ("hrdlog", "HRDLog")):
            sync_acts[f"{svc}_up"] = A(f"Upload pending to {name}", lambda s=svc: self.sync.upload(s), None, "upload")
            sync_acts[f"{svc}_up_sel"] = A(f"Upload selected to {name}",
                                           lambda s=svc: self.sync.upload(s, self.view.selected_ids() or None))
            if svc != "hrdlog":
                sync_acts[f"{svc}_down"] = A(f"Download from {name}", lambda s=svc: self.sync.download(s), None,
                                             "download")
            if svc in self.sync.RESUME_KEYS:
                sync_acts[f"{svc}_again"] = A("Download everything again…",
                                              lambda s=svc: self.sync.download_all_again(s))
            if svc == "lotw":
                sync_acts["lotw_from"] = A("Download from a date…", lambda: self.sync.download_from_date("lotw"),
                                           None, None, tr("A first download limited to QSOs made on or after a "
                                                          "day, in yearly parts"))
        self.sync_acts = sync_acts
        self.a_sync_all = A("Sync everything", self.sync_all, "Ctrl+Shift+S", "refresh-cw",
                            tr("Download confirmations from LoTW, eQSL and QRZ.com. Nothing is uploaded: grey "
                               "QSOs are not yet on every service; select them and use Upload selected."))
        for a, short in ((self.a_import, "Import"), (self.a_search, "Search"), (self.a_bulk, "Bulk edit"),
                         (self.a_prefs, "Prefs")):
            a.setIconText(tr(short))

        # View switch: Log / Contest
        self.view_group = QActionGroup(self)
        self.a_view_log = QAction(theme.icon("radio"), tr("Log"), self)
        self.a_view_contest = QAction(theme.icon("trophy"), tr("Contest"), self)
        for a, idx, sc in ((self.a_view_log, 0, "Ctrl+Shift+1"), (self.a_view_contest, 1, "Ctrl+Shift+2")):
            a.setCheckable(True)
            a.setShortcut(QKeySequence(sc))
            a.triggered.connect(lambda _=False, i=idx: self.set_view(i))
            self.view_group.addAction(a)
            self.actions_.append(a)
        self.a_view_log.setChecked(True)

        # Menus
        mb = self.menuBar()
        m = mb.addMenu(tr("&File"))
        for a in (self.a_new_log, self.a_open_log, self.a_add_log, self.a_save_copy, None, self.a_import,
                  self.a_export_all, self.a_export_sel, self.a_export_flt, None, self.a_backup, self.a_compact,
                  self.a_cty, self.a_scp, self.a_lists, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&Edit"))
        for a in (self.a_edit, self.a_bulk, self.a_delete, self.a_select_all, None,
                  self.a_dupes, self.a_review, self.a_not_dupe, self.a_show_dupe, self.a_merge_sel,
                  self.a_ignore_all, None, self.a_operator,
                  self.a_recompute, self.a_lookup_fill,
                  None, self.a_prefs):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&View"))
        for a in (self.a_view_log, self.a_view_contest, None, self.a_log_tab, self.a_paper_tab, self.a_hist_tab,
                  self.a_stats_tab,
                  None, self.a_search, self.a_clear_filters, self.a_suspicious, None, self.a_palette):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&QSL"))
        for a in (self.a_paper_sent, self.a_paper_rcvd, self.a_paper_not_sent, self.a_paper_not_rcvd,
                  self.a_paper_queue):
            m.addAction(a)
        m.addSeparator()
        for svc in ("lotw", "eqsl", "qrz", "hrdlog"):
            sub = m.addMenu({"lotw": "LoTW", "eqsl": "eQSL", "qrz": "QRZ.com", "hrdlog": "HRDLog"}[svc])
            for k in (f"{svc}_down", f"{svc}_again", f"{svc}_from", f"{svc}_up", f"{svc}_up_sel"):
                if k in sync_acts:
                    sub.addAction(sync_acts[k])
        m.addSeparator()
        m.addAction(self.a_sync_all)
        m.addAction(self.a_results)
        m.addAction(self.a_clear_hist)
        m = mb.addMenu(tr("&Help"))
        m.addAction(self.a_about)
        m.addAction(self.a_palette)
        m.addSeparator()
        m.addAction(self.a_logfile_folder)
        m.addAction(self.a_backups_folder)
        m.addAction(self.a_mirror_folder)
        m.addAction(self.a_log_folder)

        # Toolbar
        tb = self.addToolBar(tr("Main"))
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.addAction(self.a_view_log)
        tb.addAction(self.a_view_contest)
        tb.addSeparator()
        tb.addAction(self.a_search)
        tb.addAction(self.a_import)
        exp = QToolButton()
        exp.setText(tr("Export"))
        exp.setIcon(theme.icon("file-up"))
        exp.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        exp.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        em = QMenu(exp)
        for a in (self.a_export_all, self.a_export_sel, self.a_export_flt):
            em.addAction(a)
        exp.setMenu(em)
        tb.addWidget(exp)
        syn = QToolButton()
        syn.setText(tr("Sync"))
        syn.setIcon(theme.icon("refresh-cw"))
        syn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        syn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sm = QMenu(syn)
        for svc, name in (("lotw", "LoTW"), ("eqsl", "eQSL"), ("qrz", "QRZ.com"), ("hrdlog", "HRDLog")):
            sub = sm.addMenu(name)
            for k in (f"{svc}_down", f"{svc}_again", f"{svc}_from", f"{svc}_up", f"{svc}_up_sel"):
                if k in sync_acts:
                    sub.addAction(sync_acts[k])
        sm.addSeparator()
        sm.addAction(self.a_sync_all)
        syn.setMenu(sm)
        tb.addWidget(syn)
        tb.addAction(self.a_bulk)
        tb.addSeparator()
        tb.addAction(self.a_prefs)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        pal = QToolButton()
        pal.setDefaultAction(self.a_palette)
        pal.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        pal.setToolTip(tr("Command palette") + " (⌘K / Ctrl+K)")
        tb.addWidget(pal)
        self.clock = QLabel()
        self.clock.setObjectName("Clock")
        tb.addWidget(self.clock)
        self.version_lab = QToolButton()
        self.version_lab.setText(f"v{__version__}")
        self.version_lab.setObjectName("Version")
        self.version_lab.setToolTip(tr("About NWOlogger"))
        self.version_lab.clicked.connect(self.about)
        tb.addWidget(self.version_lab)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()

        # Central
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 6, 8, 0)
        lay.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        lay.addWidget(self.splitter)
        self.entry_stack = QStackedWidget()
        self.dx = DXPanel(self.db, self.prefs, ops=self.ops)
        self.dx.message.connect(self.flash)
        self.dx.logged.connect(self._after_log)
        self.dx.saved.connect(lambda qid: self.view.select_ids([qid]))
        self.dx.lookup_requested.connect(self.lookup)
        self.dx.profiles_requested.connect(lambda: self.open_prefs("Station profiles"))
        self.dx.devices_requested.connect(lambda: self.open_prefs("Devices"))
        self.entry_stack.addWidget(self.dx)
        self.contest = None
        try:
            from .contest.contest_panel import ContestPanel  # noqa: PLC0415

            self.contest = ContestPanel(self.db, self.prefs, self)
            self.contest.message.connect(self.flash)
            self.contest.scp_updated.connect(self.dx.reload_scp)
            self.contest.session_changed.connect(self._contest_session_changed)
            self.entry_stack.addWidget(self.contest)
        except ImportError:
            ph = QLabel(tr("Contest mode is not available in this build."))
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.entry_stack.addWidget(ph)
        # Keep the forms' minimum sizes inside a viewport: display scaling
        # reduces the logical desktop available to the window.
        self.entry_scroll = ScrollArea(self.entry_stack)
        # The forms' button rows sit under the scroll area, outside it, so Log QSO stays in view on a short screen
        # and follows the splitter handle; the row shown is the visible form's.
        self.bar_stack = QStackedWidget()
        self.bar_stack.addWidget(self.dx.bar)
        self.bar_stack.addWidget(self.contest.bar if self.contest is not None else QWidget())
        self.bar_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.entry_pane = QWidget()
        pane = QVBoxLayout(self.entry_pane)
        pane.setContentsMargins(0, 0, 0, 0)
        pane.setSpacing(0)
        pane.addWidget(self.entry_scroll, 1)
        pane.addWidget(self.bar_stack)
        self.splitter.addWidget(self.entry_pane)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        log_tab = QWidget()
        ll = QVBoxLayout(log_tab)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        self.filter = FilterBar()
        self.filter.set_sats(self.db.distinct("sat_name"))
        cols = self.prefs.value("log/columns")
        cols = cols if isinstance(cols, list) and cols else list(DEFAULT_LOG_COLUMNS)
        self.model = LogModel(self.db, cols, self)
        self.view = LogView(self.model)
        self.view.columns_changed.connect(lambda c: self.prefs.set_value("log/columns", c))
        self.view.edit_requested.connect(self.edit_qso)
        self.view.context_requested.connect(self._context_menu)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._update_actions())
        self.filter.changed.connect(self._filter_changed)
        self.filter.advanced_requested.connect(self.advanced_search)
        ll.addWidget(self.filter)
        ll.addWidget(self.view, 1)
        self.tabs.addTab(ScrollArea(log_tab), theme.icon("list"), tr("Log"))
        self.paper = PaperQslTab(self.db)
        self.tabs.addTab(ScrollArea(self.paper), theme.icon("mail"), tr("Paper QSL"))
        self.history = QTableWidget(0, 9)
        self.history.setHorizontalHeaderLabels([tr("Service"), tr("Direction"), tr("Started"), tr("Finished"),
                                                tr("Sent"), tr("Received"), tr("Matched"), tr("Errors"),
                                                tr("Message")])
        self.history.verticalHeader().setVisible(False)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setStretchLastSection(True)
        hist_tab = QWidget()
        hl = QVBoxLayout(hist_tab)
        hl.setContentsMargins(8, 6, 8, 6)
        hbar = QHBoxLayout()
        clear_hist = QPushButton(tr("Clear history…"))
        clear_hist.setIcon(theme.icon("trash-2"))
        clear_hist.setToolTip(tr("Delete the sync history of the services you choose, e.g. only QRZ.com"))
        clear_hist.clicked.connect(self.clear_sync_history)
        hbar.addStretch(1)
        hbar.addWidget(clear_hist)
        hl.addLayout(hbar)
        hl.addWidget(self.history, 1)
        self.tabs.addTab(ScrollArea(hist_tab), theme.icon("history"), tr("Sync history"))
        self.stats = StatisticsTab(self.db, self.prefs)
        self.stats.show_square.connect(self._show_square)
        self.tabs.addTab(ScrollArea(self.stats), theme.icon("map-pin"), tr("Statistics"))
        self.tabs.currentChanged.connect(lambda i: self._refresh_history() if i == 2 else None)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([430, 490])
        self.setCentralWidget(central)

        # Status bar
        sb = self.statusBar()
        self.st_counts = QLabel()
        self.st_stats = QLabel()
        self.st_path = QLabel()
        self.st_msg = QLabel()
        sb.addWidget(self.st_counts)
        sb.addWidget(self.st_msg, 1)
        sb.addPermanentWidget(self.st_stats)
        sb.addPermanentWidget(self.st_path)
        self.model.counts_changed.connect(lambda *_: self._update_status())
        self.filter.emit()
        self._update_actions()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self._screen is None:
            self.windowHandle().screenChanged.connect(self._screen_changed)
        self._screen_changed(self.screen())

    def _screen_changed(self, screen) -> None:
        if self._screen is not screen:
            if self._screen is not None:
                self._screen.availableGeometryChanged.disconnect(self._fit_to_screen)
            self._screen = screen
            if screen is not None:
                screen.availableGeometryChanged.connect(self._fit_to_screen)
        self._fit_to_screen()
        QTimer.singleShot(0, self, self._fit_to_screen)

    def _fit_to_screen(self, *_args) -> None:
        if not getattr(self, "_shut", False):
            fit_to_screen(self)

    # ------------------------------------------------------------------ status / title
    def _tick(self) -> None:
        now = timeutil.now_utc()
        local = datetime.now().strftime("%H:%M")
        self.clock.setText(f"UTC {now.strftime('%H:%M:%S')}   ·   {tr('Local')} {local}")

    def _update_title(self) -> None:
        self.setWindowTitle(f"{APP_NAME} v{__version__} — {self.db.path.name}")
        self.st_path.setText(str(self.db.path))

    def _update_status(self) -> None:
        total = self.db.count()
        shown = len(self.model.ids)
        self.st_counts.setText(f"{total:,} " + tr("QSOs") + (f"  ·  {shown:,} " + tr("shown") if shown != total
                                                            else ""))
        wkd = self.db.conn.execute("SELECT COUNT(DISTINCT dxcc) FROM qso WHERE dxcc IS NOT NULL AND dxcc > 0"
                                   ).fetchone()[0]
        cfm = self.db.conn.execute(
            "SELECT COUNT(DISTINCT dxcc) FROM qso WHERE dxcc > 0 AND (lotw_qsl_rcvd='Y' OR qsl_rcvd='Y')").fetchone()[0]
        calls = self.db.conn.execute("SELECT COUNT(DISTINCT call) FROM qso").fetchone()[0]
        self.st_stats.setText(f"DXCC {wkd} {tr('wkd')} / {cfm} {tr('cfm')}   ·   {calls:,} {tr('calls')}")

    def flash(self, text: str) -> None:
        if getattr(self, "_shut", False):
            return  # a background task finishing after a restart: this window is gone
        try:
            self.st_msg.setText(text)
        except RuntimeError:  # the label is already deleted
            return
        # The label is the timer's context, so a window closed within 8 s doesn't get the call.
        QTimer.singleShot(8000, self.st_msg, lambda t=text: self.st_msg.setText("") if self.st_msg.text() == t
                          else None)

    def _on_db_change(self, kind: str, _ids: list[int]) -> None:
        if not getattr(self, "_shut", False):
            self._checkpoint_timer.start()
        if kind in ("insert", "delete", "reset", "update"):
            self._update_actions()
        if kind == "profiles":
            self.dx.reload_profiles()
        if kind == "sync_log" and self.tabs.currentIndex() == 2:
            self._refresh_history()

    def _update_actions(self) -> None:
        n = len(self.view.selected_ids())
        for a in (self.a_bulk, self.a_delete, self.a_export_sel, self.a_paper_sent, self.a_paper_rcvd,
                  self.a_paper_not_sent, self.a_paper_not_rcvd, self.a_paper_queue, self.a_recompute,
                  self.a_lookup_fill, self.a_not_dupe):
            a.setEnabled(n > 0)
        self.a_edit.setEnabled(n == 1)
        self.a_merge_sel.setEnabled(2 <= n <= 5)
        self.a_show_dupe.setEnabled(n == 1)
        for k, a in self.sync_acts.items():
            if k.endswith("_up_sel"):
                a.setEnabled(n > 0)

    def _refresh_history(self) -> None:
        rows = self.db.sync_history()
        self.history.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["service"], tr(r["direction"]), fmt("time_on", r["started_at"]), fmt("time_on", r["finished_at"]),
                    r["sent"], r["received"], r["matched"], r["errors"], r["message"] or ""]
            for j, v in enumerate(vals):
                self.history.setItem(i, j, QTableWidgetItem(str(v)))

    # ------------------------------------------------------------------ view switching
    def set_view(self, i: int) -> None:
        prev = self.entry_stack.currentIndex()
        if prev != i:
            self.prefs.set_value(f"window/split{prev}", self.splitter.sizes())
        self.entry_stack.setCurrentIndex(i)
        self.bar_stack.setCurrentIndex(i)
        # Only the visible page decides how small the entry area may get.
        for k in range(self.entry_stack.count()):
            pol = QSizePolicy.Policy.Preferred if k == i else QSizePolicy.Policy.Ignored
            self.entry_stack.widget(k).setSizePolicy(pol, pol)
            self.bar_stack.widget(k).setSizePolicy(pol, pol)
        self.entry_stack.updateGeometry()
        self.bar_stack.updateGeometry()
        sizes = self.prefs.value(f"window/split{i}")
        total = sum(self.splitter.sizes())
        if isinstance(sizes, list) and len(sizes) == 2:
            want = [int(x) for x in sizes]
        elif i == 1 and total:
            want = [300, max(200, total - 300)]
        else:
            want = None
        if want:
            self.splitter.setSizes(want)
            QTimer.singleShot(0, lambda w=want: self.splitter.setSizes(w))  # again after the contest panes appear
        (self.a_view_log if i == 0 else self.a_view_contest).setChecked(True)
        if i == 1 and self.contest is not None:
            self.contest.activate()
        else:
            if self.contest is not None:
                self.contest.deactivate()
            self.dx.call.setFocus()

    def _contest_session_changed(self, sid) -> None:
        self.model.contest_session_id = sid
        if self.filter.preset.currentData() == "this_contest":
            self.model.refresh()

    def focus_call(self) -> None:
        if self.entry_stack.currentIndex() == 1 and self.contest is not None:
            self.contest.activate()
        else:
            self.dx.call.setFocus()
            self.dx.call.selectAll()

    # ------------------------------------------------------------------ filters
    def _filter_changed(self, spec: dict) -> None:
        self.model.set_spec(spec)

    def advanced_search(self) -> None:
        dlg = SearchDialog(self.db, self.filter.conditions, self)
        if dlg.exec():
            self.filter.set_conditions(dlg.conditions())
            self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------ log actions
    def _context_menu(self, pos) -> None:
        ids = self.view.selected_ids()
        m = QMenu(self)
        if not ids:
            m.addAction(self.a_dupes)
            m.exec(pos)
            return
        m.addAction(self.a_edit)
        m.addAction(self.a_bulk)
        m.addSeparator()
        m.addAction(self.a_paper_sent)
        m.addAction(self.a_paper_rcvd)
        m.addAction(self.a_paper_not_sent)
        m.addAction(self.a_paper_not_rcvd)
        m.addAction(self.a_paper_queue)
        up = m.addMenu(theme.icon("upload"), tr("Upload selected to"))
        for svc in ("lotw", "eqsl", "qrz", "hrdlog"):
            up.addAction(self.sync_acts[f"{svc}_up_sel"])
        m.addAction(self.a_export_sel)
        m.addSeparator()
        m.addAction(self.a_lookup_fill)
        m.addAction(self.a_recompute)
        m.addSeparator()
        m.addAction(self.a_show_dupe)
        m.addAction(self.a_merge_sel)
        m.addAction(self.a_not_dupe)
        m.addSeparator()
        m.addAction(self.a_delete)
        m.exec(pos)

    def edit_current(self) -> None:
        qid = self.view.current_id()
        if qid:
            self.edit_qso(qid)

    def edit_qso(self, qid: int) -> None:
        q = self.db.get(qid)
        if q and q.get("contest_session_id") and self.contest is not None and \
                self.entry_stack.currentIndex() == 1:
            self.contest.edit_qso(qid)
            return
        self.set_view(0)
        self.dx.load_qso(qid)

    def _after_log(self, qid: int) -> None:
        self.filter.set_sats(self.db.distinct("sat_name"))
        self.dx.reload_sats()
        self.sync.upload_on_log(qid)

    def bulk_edit(self) -> None:
        ids = self.view.selected_ids()
        if not ids:
            return
        dlg = BulkEditDialog(self.db, ids, self)
        if dlg.exec():
            self.flash(tr("Bulk edit applied to") + f" {dlg.changed_count} " + tr("QSOs") + " · " +
                       tr("a backup was saved first"))

    def delete_selected(self) -> None:
        ids = self.view.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, tr("Delete"), f"{tr('Delete')} {len(ids)} {tr('QSOs')}? " +
                                tr("This can't be undone; a backup of the log is saved first.")
                                ) != QMessageBox.StandardButton.Yes:
            return
        n = self.db.delete(ids, label=tr("Delete") + f" {len(ids)}")
        self.flash(tr("Deleted") + f" {n} " + tr("QSOs"))

    def compact_log(self) -> None:
        """Rebuild the log file without the space deleted QSOs left behind (and their old data), after a backup."""
        def size(n: int) -> str:
            return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} KB"

        if QMessageBox.question(
                self, tr("Compact log file"),
                tr("Rebuild the log file without the space deleted QSOs left behind? Their old data is then gone from "
                   "the file as well; backups keep theirs. A backup is saved first.") + "\n\n" +
                tr("Size now:") + f" {size(self.db.file_size())}") != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            before, after, _backup = self.db.compact()
        except (sqlite3.Error, OSError, RuntimeError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, tr("Compact log file"), tr("Could not compact the log file:") + f"\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.flash(tr("Log file compacted") + f": {size(before)} → {size(after)}")

    def find_dupes(self) -> None:
        res = self.rescan_marks(quiet=True)
        d, s = res.dupes, res.suspects
        if not (d or s):
            QMessageBox.information(self, tr("Find duplicates"), tr("No duplicates found."))
            return
        if QMessageBox.question(self, tr("Find duplicates"), f"{d} " + tr("dupes") + f", {s} " +
                                tr("suspicious dupes marked.") + "\n\n" + tr("Review them side by side now?")
                                ) == QMessageBox.StandardButton.Yes:
            self.review_dupes()
        self.filter.set_preset("suspicious")

    def _dupe_windows(self) -> tuple:
        return (self.prefs.get("block_seconds"), self.prefs.warn_seconds())

    def rescan_marks(self, *, quiet: bool = False) -> dupes.RescanResult:
        """Bring the dupe marks up to date with the current windows (after they changed in Preferences, and for
        Find duplicates); the status bar says what the scan did unless ``quiet``."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = dupes.rescan(self.db, block_s=self.prefs.get("block_seconds"), warn_s=self.prefs.warn_seconds())
        finally:
            QApplication.restoreOverrideCursor()
        if not quiet:
            self.flash(tr("Dupe marks rescanned for the new windows:") + f" {res.dupes} " + tr("dupes") +
                       f", {res.suspects} " + tr("suspicious dupes") + f", {res.cleared} " + tr("cleared"))
        return res

    def not_dupe(self) -> None:
        ids = self.view.selected_ids()
        self.db.update_many(ids, {"dupe_flag": "OK", "dupe_of": None}, label=tr("Not a dupe"), track_service=False)

    def show_dupe_partner(self) -> None:
        ids = self.view.selected_ids() or ([self.view.current_id()] if self.view.current_id() else [])
        if not ids:
            return
        show_review(self, self.db, self.prefs, ids, on_edit=self.edit_qso)

    def change_operator(self) -> None:
        if self.entry_stack.currentIndex() == 1 and self.contest is not None:
            self.contest.op_field.setFocus()
            self.contest.op_field.selectAll()
        else:
            self.dx.change_operator()

    def review_dupes(self) -> None:
        show_review(self, self.db, self.prefs, on_edit=self.edit_qso)

    def merge_selected(self) -> None:
        """Merge 2–5 selected QSOs with the same call field by field, even when they aren't marked as dupes."""
        ids = self.view.selected_ids()
        rows = self.db.get_many(ids)
        calls = {(q.get("call") or "").upper() for q in rows.values()}
        if not 2 <= len(rows) <= 5 or len(calls) != 1:
            QMessageBox.information(self, tr("Merge selected"), tr("Select 2 to 5 QSOs with the same call."))
            return
        dlg = DupeCompareDialog(self.db, self.prefs, groups=[ids], fixed=True, start_merge=True, parent=self)
        dlg.edit_requested.connect(self.edit_qso)
        dlg.exec()

    def ignore_all_dupes(self) -> None:
        n = ignore_all(self, self.db)
        if n:
            self.flash(f"{n} " + tr("QSOs marked as not dupes"))

    def recompute_dxcc(self) -> None:
        ids = self.view.selected_ids()
        if not ids or QMessageBox.question(
                self, tr("Recompute country, DXCC and zones"),
                f"{len(ids)} " + tr("QSOs: country, DXCC, zones and continent are replaced from the country file.") +
                " " + tr("This can't be undone; a backup of the log is saved first.")) != QMessageBox.StandardButton.Yes:
            return
        self.db.backup("recompute")
        cty = dxcc.default()

        def ch(q: dict) -> dict:
            r = cty.resolve(q.get("call") or "")
            if not r:
                return {}
            c = {"country": r.country, "dxcc": r.dxcc, "cqz": r.cqz, "ituz": r.ituz, "cont": r.cont}
            if r.dxcc == regions.CHINA_DXCC:
                st = regions.normalize_cn(q.get("state")) if q.get("state") else regions.guess_cn_province(q["call"])
                if st:
                    c["state"] = st
            return c

        n = self.db.update_many(ids, ch, label=tr("Recompute DXCC"))
        self.flash(tr("Updated") + f" {n} " + tr("QSOs"))

    # ------------------------------------------------------------------ lookup
    def lookup_clients(self) -> list:
        if self._lookup_clients is None:
            cl = []
            qrz_pw = self.prefs.secret("qrz_password") if self.prefs.get("qrz_user") else None
            ham_pw = self.prefs.secret("hamqth_password") if self.prefs.get("hamqth_user") else None
            applog.remember_secrets([qrz_pw, ham_pw, self.prefs.get("qrz_user"), self.prefs.get("hamqth_user")])
            if qrz_pw:
                cl.append(lookup_clients.QrzXml(self.prefs.get("qrz_user"), qrz_pw))
            if ham_pw:
                cl.append(lookup_clients.HamQth(self.prefs.get("hamqth_user"), ham_pw))
            self._lookup_clients = cl
        return self._lookup_clients

    def lookup(self, call: str) -> None:
        cached = self.db.cache_get(call, self.prefs.get("lookup_ttl_days"))
        if cached:
            src = cached.pop("_source", "cache")
            self.dx.apply_lookup(call, cached, f"{src} ({tr('cached')})")
            return
        clients = self.lookup_clients()
        if not clients:
            return

        def done(res):
            data, source, errors = res
            if data:
                self.db.cache_put(call, source or "", data)
                self.dx.note_known(call, source or "")
                self.dx.apply_lookup(call, data, source or "")
                if self.contest is not None:
                    self.contest.apply_lookup(call, data)
            elif errors:
                self.flash(errors[0])
            else:
                self._check_similar_online(call)

        run_async(lookup_clients.lookup_chain, call, clients, on_done=done,
                  on_error=lambda m: self.flash(self._safe_lookup_error(m)))

    def _safe_lookup_error(self, msg: str) -> str:
        return net.redact(msg, [s for c in self.lookup_clients() for s in c.secrets()])

    def _check_similar_online(self, call: str) -> None:
        """The call is not on QRZ.com / HamQTH: check up to 3 MASTER.SCP calls one character away there, so the
        ones that exist show in Similar calls (and stay cached)."""
        clients = self.lookup_clients()
        cands = self.dx.online_candidates(call) if clients and len(call) >= 4 else []
        if not cands:
            return

        def work():
            return [(c, *lookup_clients.lookup_chain(c, clients)[:2]) for c in cands]

        def done(rows):
            for c, data, source in rows:
                if data:
                    self.db.cache_put(c, source or "", data)
                    self.dx.note_known(c, source or "")

        run_async(work, on_done=done, on_error=lambda m: self.flash(self._safe_lookup_error(m)))

    def lookup_fill_selected(self, ids: list[int] | None = None) -> None:
        """Look up the selected QSOs' calls and fill their empty fields, in a window with progress and Stop."""
        ids = ids or self.view.selected_ids()
        if not ids:
            return
        cur = getattr(self, "_lookup_fill", None)
        if cur is not None and cur.running:
            cur.show()
            cur.raise_()
            return
        dlg = LookupFillDialog(self, ids)
        dlg.finished_fill.connect(lambda n: self.flash(tr("Filled") + f" {n} " + tr("QSOs")))
        self._lookup_fill = dlg
        dlg.start()

    # ------------------------------------------------------------------ files
    def import_file(self) -> None:
        start = self.prefs.value("last_import_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Import"), start,
            tr("ADIF or MacLoggerDX") + " (*.adi *.adif *.ADI *.sql *.sqlite *.db);;" + tr("All files") + " (*)")
        if not path:
            return
        self.prefs.set_value("last_import_dir", str(Path(path).parent))
        try:
            dlg = ImportDialog(self.db, self.prefs, path, self)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr("Import"), f"{tr('Could not read the file')}:\n{e}")
            return
        if not dlg.exec():
            return
        if dlg.positions_set:
            self.flash(tr("Downloads continue from") + " " + positions.describe(dlg.positions_set, tr))
        if dlg.lotw_result is not None:
            self.sync.lotw_report_applied(Path(path).name, dlg.lotw_result)
            return
        if dlg.result:
            r = dlg.result
            self.filter.set_sats(self.db.distinct("sat_name"))
            msg = f"{tr('Imported')} {r.added}"
            if r.updated:
                msg += f", {tr('updated')} {r.updated}"
            if r.merged:
                msg += f", {tr('merged')} {r.merged}"
            msg += f". {r.dupes} {tr('dupes')}, {r.suspects} {tr('suspicious')} {tr('marked')}."
            self.flash(msg)
            if r.dupes or r.suspects:
                if QMessageBox.question(self, tr("Import"), msg + "\n\n" + tr("Review them side by side now?")
                                        ) == QMessageBox.StandardButton.Yes:
                    self.review_dupes()
                self.filter.set_preset("suspicious")
            self.dx.reload_profiles()
            self.dx.reload_sats()

    def export(self, which: str) -> None:
        if which == "selected":
            ids = self.view.selected_ids()
            if not ids:
                return
        elif which == "filtered":
            ids = list(self.model.ids)
        else:
            ids = None
        default = Path(self.prefs.value("last_export_dir", str(Path.home()))) / \
            f"nwologger-{which}-{timeutil.now_utc().strftime('%Y%m%d-%H%M')}.adi"
        path, _ = QFileDialog.getSaveFileName(self, tr("Export to ADIF"), str(default), "ADIF (*.adi)")
        if not path:
            return
        self.prefs.set_value("last_export_dir", str(Path(path).parent))
        n = exporter.export_ids(self.db, ids, path)
        self.flash(f"{tr('Exported')} {n} {tr('QSOs to')} {path}")

    def bring_to_front(self, path: str | None = None) -> None:
        """Another NWOlogger was started (nwologger/instance.py): show this window instead. When that launch was
        asked to open a log file, say which file this window has, since one NWOlogger runs at a time."""
        if getattr(self, "_shut", False):
            return
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.alert(self)  # Windows may refuse the focus to a background process: its taskbar button flashes
        if path and Path(path).resolve() != self.db.path.resolve():
            QMessageBox.information(self, APP_NAME, tr("NWOlogger is already running with") + f"\n{self.db.path}\n\n" +
                                    tr("Use File → Open log file to switch to another log."))

    def shutdown(self) -> None:
        """Stop timers and listeners before the log file is closed."""
        if getattr(self, "_shut", False):
            return
        self._shut = True
        self.filter._timer.stop()
        self.filter.blockSignals(True)
        self.model.detach()
        self.paper.model.detach()
        self.db.remove_listener(self._on_db_change)
        self.stats.shutdown()
        self.sync.stop_timers()
        self._clock_timer.stop()
        self._plan_timer.stop()
        self._checkpoint_timer.stop()
        self.dx._clock.stop()
        self.dx._check_timer.stop()
        self.dx._lookup_timer.stop()
        self.dx._index_timer.stop()
        self.db.remove_listener(self.dx._on_db_change)
        if self.contest is not None and hasattr(self.contest, "shutdown"):
            self.contest.shutdown()

    def _switch_db(self, name: str) -> None:
        """Restart with another log of the station folder (by file name): the lease stays, the folder is the same."""
        mirror.write_if_changed(self.db)
        self.shutdown()
        self.db.close()
        self.prefs.set("current_log", name)
        QMessageBox.information(self, APP_NAME, tr("NWOlogger will restart with") + f"\n{self.db.path.parent / name}")
        self.app.exit(1000)  # restart code

    def new_log(self) -> None:
        """A new, empty log in the station folder (only its name is chosen; every log lives there)."""
        folder = self.db.path.parent
        path, _ = QFileDialog.getSaveFileName(self, tr("New log file"), str(folder / "new-log.sqlite"),
                                              "SQLite (*.sqlite)")
        if not path:
            return
        name = Path(path).name
        if not name.lower().endswith(".sqlite"):
            name += ".sqlite"
        if (folder / name).exists():
            QMessageBox.warning(self, tr("New log file"), tr("A log with that name is already in the folder."))
            return
        self._switch_db(name)

    def open_log(self) -> None:
        """Switch to another log of the station folder. A file picked elsewhere is added by copy (Add log file)."""
        folder = self.db.path.parent
        path, _ = QFileDialog.getOpenFileName(self, tr("Open log file"), str(folder), "SQLite (*.sqlite)")
        if not path:
            return
        chosen = Path(path)
        if chosen.resolve().parent == folder.resolve():
            try:
                dbm.check_log_file(chosen)  # another program's database is refused, and left as it is
            except dbm.NotALogFile as e:
                QMessageBox.critical(self, tr("Open log file"), str(e))
                return
            self._switch_db(chosen.name)
            return
        self.add_log(chosen)

    def add_log(self, source: Path | None = None) -> None:
        """Copy a log from elsewhere into the station folder (never opened in place) and switch to it."""
        if source is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("Add log file"), str(Path.home()), "SQLite (*.sqlite)")
            if not path:
                return
            source = Path(path)
        from .add_log import add_log as _add  # noqa: PLC0415

        dest = _add(self, source, self.db.path.parent)
        if dest is not None:
            self.flash(tr("Log added") + f": {dest.name}")
            self._switch_db(dest.name)

    def save_copy(self) -> None:
        """A complete copy of the open log wherever you choose; without the write guard for other programs."""
        default = station.mirror_dir() / self.db.path.name
        path, _ = QFileDialog.getSaveFileName(self, tr("Save a copy"), str(default), "SQLite (*.sqlite)")
        if not path:
            return
        other = QMessageBox.question(self, tr("Save a copy"),
                                     tr("Is this copy for another program? Then it is written without the write "
                                        "guard, so that program can edit it. A copy for NWOlogger keeps the guard."),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        from .add_log import save_copy as _save  # noqa: PLC0415

        dest = _save(self, self.db, Path(path), for_other_programs=other)
        if dest is not None:
            self.flash(tr("Copy of the log saved") + f": {dest}")

    # ------------------------------------------------------------------ misc
    def sync_all(self) -> None:
        """Downloads only. Uploads are always your choice (Upload pending / Upload selected): a QSO sent to LoTW
        or eQSL can't be taken back, and eQSL sends a card to the other station."""
        self.sync.downloads(["lotw", "eqsl", "qrz"])

    def open_prefs(self, page: str | None = None) -> None:
        windows = self._dupe_windows()
        dlg = PrefsDialog(self.db, self.prefs, self, page=page)
        if dlg.exec():
            with applog.timed("Preferences OK") as t:
                if self.prefs.get("theme") != theme.mode():  # the dialog's preview has usually applied it already
                    theme.apply(self.app, self.prefs.get("theme"))
                    t.step("theme")
                self._lookup_clients = None
                self.dx.reload_profiles()
                self.dx.reload_lists()
                self.dx.clear_form()
                t.step("entry form")
                self.model.refresh()
                t.step("log table")
            self.stats.apply_provider()
            self.db.backup_keep = int(self.prefs.get("backup_keep") or 20)
            self.db.scheduled_keep = int(self.prefs.get("scheduled_keep") or 8)
            if self._dupe_windows() != windows:  # the marks follow the rule
                self.rescan_marks()
            if self.prefs.get("language") != i18n.lang():
                self.request_rebuild()
        else:
            self.dx.reload_profiles()

    def _select_confirmed(self, service: str, ids: list[int]) -> None:
        """A download's newly confirmed QSOs become the log's selection, so you see them; one click clears it.
        Downloads that finish later (Sync everything) add theirs, unless you have clicked in the log since."""
        if not ids:
            return
        self.model.refresh()  # the confirmed rows are in the table now, not after the change timer
        prev = self._confirmed_sel
        keep = prev if prev is not None and set(self.view.selected_ids()) == prev else set()
        self.view.select_ids(sorted(keep | set(ids)), scroll=True)
        self._confirmed_sel = set(self.view.selected_ids())
        hidden = sum(1 for i in ids if i not in self.model.row_of)
        self.flash(f"{uploads.SERVICES.get(service, service)}: {len(ids)} " + tr("new confirmations selected") +
                   (f" ({hidden} " + tr("hidden by the filter") + ")" if hidden else ""))

    def _show_square(self, sq: str) -> None:
        """Grid map → a square: the Log tab, filtered to the QSOs in it."""
        self.tabs.setCurrentIndex(0)
        self.filter.set_spec({"conditions": [{"field": "gridsquare", "op": "starts", "value": sq}]})
        self.flash(tr("QSOs in") + f" {sq} · " + tr("Clear filters to see them all"))

    def _show_ids(self, ids: list[int]) -> None:
        """Sync results → Select them: the Log tab with those QSOs selected and the first in view."""
        self.tabs.setCurrentIndex(0)
        self.model.refresh()
        self.view.select_ids(ids, scroll=True)
        self._confirmed_sel = set(self.view.selected_ids())
        hidden = sum(1 for i in ids if i not in self.model.row_of)
        self.flash(f"{len(ids) - hidden} " + tr("QSOs selected") +
                   (f" ({hidden} " + tr("hidden by the filter") + ")" if hidden else ""))
        self.view.setFocus()

    # ------------------------------------------------------------------ language switch
    def request_rebuild(self) -> None:
        """A running upload or download reports back to this window, so the switch waits until it finishes."""
        if self.sync.running():
            self._rebuild_waiting = True
            self.flash(tr("The language changes when the running sync finishes."))
            return
        self.rebuild_requested.emit()

    def _sync_idle(self) -> None:
        if self._rebuild_waiting:
            self._rebuild_waiting = False
            QTimer.singleShot(0, self.rebuild_requested.emit)  # after the finished job's own updates

    def rebuilt(self) -> MainWindow:
        """This window built again in the language now set in Preferences: the same log file (no new backup),
        operator and state. This one is closed."""
        lang = self.prefs.get("language")
        with applog.timed(f"Language switch to {lang}", at_least=0) as t:
            state = self.snapshot()
            self.shutdown()
            i18n.load(lang)
            t.step("load")
            new = MainWindow(self.db, self.prefs, self.app, ops=self.ops, first_run=False)
            t.step("build")
            new.restore(state)
            new.show()
            new.raise_()
            new.activateWindow()
            self.hide()
            self.deleteLater()
            t.step("restore")
        return new

    def save_layout(self) -> None:
        self.prefs.set_value("window/geometry", self.saveGeometry())
        self.prefs.set_value("window/splitter", self.splitter.saveState())
        self.prefs.set_value("log/columns", self.view.visual_columns())
        if self.contest is not None:
            self.contest.save_state()

    def snapshot(self) -> dict:
        """What a window built again should show: layout (saved in Preferences), view, tab, filter, sort order,
        selection and scroll position, the entry form and the Sync results rows."""
        self.save_layout()
        hh = self.view.horizontalHeader()
        top = self.view.indexAt(self.view.viewport().rect().topLeft())
        res = self.sync.results
        return {"view": self.entry_stack.currentIndex(), "tab": self.tabs.currentIndex(), "filter": self.filter.spec(),
                "sort": (self.model.columns[hh.sortIndicatorSection()]
                         if 0 <= hh.sortIndicatorSection() < len(self.model.columns) else None,
                         hh.sortIndicatorOrder()),
                "selection": self.view.selected_ids(), "top": self.model.id_at(top.row()) if top.isValid() else None,
                "form": self.dx.form_state(), "results": list(res.entries) if res is not None else [],
                "queued": {s: sorted(ids) for s, ids in self.sync.queued.items() if ids},
                "results_shown": res is not None and res.isVisible()}

    def restore(self, st: dict) -> None:
        if st.get("view") == 1 and self.contest is not None:
            self.set_view(1)
        self.tabs.setCurrentIndex(st.get("tab") or 0)
        self.filter.set_spec(st.get("filter") or {})
        col, order = st.get("sort") or (None, None)
        if col in self.model.columns:
            self.view.horizontalHeader().setSortIndicator(self.model.columns.index(col), order)
        self.view.select_ids(st.get("selection") or [], scroll=False)
        top = st.get("top")
        if top in self.model.row_of:
            self.view.scrollTo(self.model.index(self.model.row_of[top], 0), QAbstractItemView.ScrollHint.PositionAtTop)
        self.dx.restore_form(st.get("form") or {})
        for svc, ids in (st.get("queued") or {}).items():
            self.sync._queue(svc, ids)  # noqa: SLF001 - uploads that were waiting in the old window
        if st.get("results"):
            w = self.sync._window()  # noqa: SLF001 - the controller owns its results window
            w.replay(st["results"])
            if st.get("results_shown"):
                w.show()

    def palette(self) -> None:
        CommandPalette(self.actions_, self).exec()

    def about(self) -> None:
        AboutDialog(self.db, self).exec()

    def show_log_folder(self) -> None:
        """The diagnostic log's folder (Help → Show diagnostic log folder)."""
        self._open_folder(applog.log_dir())

    def show_logfile_folder(self) -> None:
        """The station folder: the log files, with backups/, downloads/, settings.ini and logs/ beside them."""
        self._open_folder(self.db.path.parent)

    def show_mirror_folder(self) -> None:
        """Documents/NWOlogger: the backup copy of the current log (core/mirror.py)."""
        self._open_folder(station.mirror_dir())

    def show_backups_folder(self) -> None:
        self._open_folder(self.db.backup_dir)

    @staticmethod
    def _open_folder(folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def clear_sync_history(self) -> None:
        n = clear_history(self.db, self)
        if n:
            self._refresh_history()
            self.flash(tr("Sync history cleared") + f": {n} " + tr("entries"))

    def show_sync_results(self) -> None:
        w = self.sync._window()  # noqa: SLF001 - the controller owns its results window
        w.show()
        w.raise_()
        w.activateWindow()

    def _show_history(self) -> None:
        self.tabs.setCurrentIndex(2)
        self.raise_()
        self.activateWindow()

    def update_cty(self, quiet: bool = True) -> None:
        def done(n):
            if self._shut:
                return
            dxcc.default.cache_clear()
            self.dx.cty = dxcc.default()
            self.flash(tr("Country file updated") + f" ({n} " + tr("entities") + ")")

        def fail(msg):
            if not quiet and not self._shut:
                QMessageBox.warning(self, tr("Country file"), msg)

        run_async(dxcc.download, on_done=done, on_error=fail)

    def update_scp(self) -> None:
        def done(n):
            if self._shut:
                return
            self.dx.reload_scp()
            if self.contest is not None:
                self.contest._load_scp()
            self.flash(tr("MASTER.SCP updated") + f" ({n:,} " + tr("calls") + ")")

        run_async(scpmod.download, scpmod.path_for(self.db.path), on_done=done,
                  on_error=lambda m: QMessageBox.warning(self, "MASTER.SCP", m))

    def update_call_lists(self, quiet: bool = True) -> None:
        """Download ARRL's LoTW user list and eQSL's AG member list for Similar calls (no login)."""
        folder = calllists.folder_for(self.db.path)

        def done(counts: dict[str, int]) -> None:
            if self._shut:
                return
            self.dx.reload_call_lists()
            self.flash(tr("Call lists updated") + f": {counts.get('lotw', 0):,} " + tr("LoTW users") +
                       f", {counts.get('eqsl_ag', 0):,} " + tr("eQSL AG members"))

        def fail(msg: str) -> None:
            if self._shut:
                return
            if quiet:
                self.flash(msg)
            else:
                QMessageBox.warning(self, tr("Call lists"), msg)

        run_async(calllists.download, folder, on_done=done, on_error=fail)

    def _backup_plan(self) -> None:
        """The regular backup plan (Preferences → Database): the scheduled copy when it is due, then the reminder
        for a copy on another disk, never over another window, a running sync or the welcome of a first run."""
        if getattr(self, "_shut", False) or not self.prefs.get("backup_schedule_on"):
            return
        every = int(self.prefs.get("backup_every_days") or 0)
        try:
            dest = backup_plan.run_scheduled(self.db, every)
        except (sqlite3.Error, OSError) as e:
            applog.log.warning("Regular backup failed: %s", e)
            return
        if dest is not None:
            self.flash(tr("Regular backup saved") + f": {dest.name}")
        self._mirror()
        if not self.prefs.get("backup_remind") or self._reminder is not None:
            return
        if QApplication.activeModalWidget() is not None or self.sync.running() or self.sync.waiting():
            return
        if not backup_plan.external_due(self.db, every):
            return
        dlg = self._reminder = BackupReminder(self.db, self.prefs, self)
        dlg.copied.connect(lambda p: self.flash(tr("Copy of the log saved") + f": {p}"))
        dlg.prefs_requested.connect(lambda: self.open_prefs("Database"))
        dlg.finished.connect(lambda *_a: setattr(self, "_reminder", None))
        dlg.show()

    def _mirror(self) -> None:
        """The backup copy in Documents (core/mirror.py), on a worker thread, when the log changed since the last."""
        if getattr(self, "_shut", False) or not mirror.due(self.db) or getattr(self, "_mirroring", False):
            return
        p = mirror.plan(self.db)
        self._mirroring = True

        def done(_res) -> None:
            self._mirroring = False
            if not getattr(self, "_shut", False):
                mirror.mark(self.db, p.count)

        def failed(msg: str) -> None:
            self._mirroring = False
            applog.log.warning("Backup copy failed: %s", msg)

        run_async(mirror.write_file, p, on_done=done, on_error=failed)

    def _first_run(self) -> None:
        """Scheduled from __init__; the log may have been closed meanwhile (Open log file restarts the app within
        the same second), so every step checks the window is still up."""
        if getattr(self, "_shut", False):
            return
        age = dxcc.user_copy_age_days()
        if (age is None or age > 7) and not os.environ.get("NWOLOGGER_OFFLINE"):
            QTimer.singleShot(3000, lambda: self.update_cty(quiet=True))
        lists_age = calllists.age_days(calllists.folder_for(self.db.path))
        if (lists_age is None or lists_age > calllists.REFRESH_DAYS) and not os.environ.get("NWOLOGGER_OFFLINE"):
            QTimer.singleShot(6000, lambda: self.update_call_lists(quiet=True))
        if not self.db.profiles():
            QMessageBox.information(self, APP_NAME, tr("Welcome! Create a station profile first: your callsign "
                                                       "and grid are added to every QSO you log."))
            if getattr(self, "_shut", False):  # the log was switched while the message was up
                return
            self.open_prefs("Station profiles")

    def closeEvent(self, e) -> None:  # noqa: N802
        running = self.sync.running() + self.sync.waiting()
        if running and QMessageBox.question(
                self, APP_NAME, tr("Still running:") + " " + ", ".join(running) + ".\n\n" +
                tr("Quit anyway? TQSL is stopped; QSOs not reported as uploaded stay pending and can be uploaded "
                   "again later.")) != QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        self.sync.cancel_all()  # TQSL, and the timers of uploads waiting for the gap
        self.save_layout()
        self.shutdown()
        super().closeEvent(e)
