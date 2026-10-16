"""Main window: toolbar, entry panel (DX / Contest), Log · Paper QSL · Sync history tabs."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
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
from ..core import calllists, dupes, dxcc, exporter, net, regions, timeutil
from ..core import db as dbm
from ..core.contest import scp as scpmod
from ..core.fields import DEFAULT_LOG_COLUMNS
from ..core.lookup import clients as lookup_clients
from ..core.operators import OperatorSession
from . import theme
from .about_dialog import AboutDialog
from .bulk_edit import BulkEditDialog
from .command_palette import CommandPalette
from .dupe_dialog import show_review
from .entry.dx_panel import DXPanel
from .import_dialog import ImportDialog
from .log.filter_bar import FilterBar
from .log.log_model import LogModel
from .log.log_view import LogView
from .log.search_dialog import SearchDialog
from .prefs_dialog import PrefsDialog
from .qsl_paper import PaperQslTab, mark_paper
from .sync_controller import SyncController, clear_history
from .widgets import fmt, run_async

tr = i18n.tr


class MainWindow(QMainWindow):
    def __init__(self, db: dbm.LogDB, prefs, app: QApplication):
        super().__init__()
        self.db, self.prefs, self.app = db, prefs, app
        self.actions_: list[QAction] = []
        self.ops = OperatorSession()
        self.sync = SyncController(db, prefs, self)
        self.sync.message.connect(self.flash)
        self.sync.prefs_requested.connect(self.open_prefs)
        self.sync.history_requested.connect(self._show_history)
        self._lookup_clients: list | None = None
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
        QTimer.singleShot(0, self._first_run)

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
        self.a_backup = A("Back up now", lambda: self.flash(tr("Backup saved") + f": {self.db.backup('manual')}"))
        self.a_quit = A("Quit", self.close, QKeySequence.StandardKey.Quit)
        self.a_undo = A("Undo", self.undo, QKeySequence.StandardKey.Undo, "undo-2")
        self.a_bulk = A("Bulk edit selected…", self.bulk_edit, "Ctrl+B", "pencil")
        self.a_edit = A("Edit QSO", self.edit_current, "Ctrl+E")
        self.a_delete = A("Delete selected QSOs", self.delete_selected, "Ctrl+Backspace", "trash-2")
        self.a_dupes = A("Find duplicates in the whole log", self.find_dupes, None, "copy-check")
        self.a_not_dupe = A("Not a dupe", self.not_dupe)
        self.a_show_dupe = A("Compare with the original…", self.show_dupe_partner)
        self.a_review = A("Review suspicious dupes…", self.review_dupes, "Ctrl+Shift+D", "copy-check")
        self.a_operator = A("Change operator…", self.change_operator, "Ctrl+Shift+O", "user-round")
        self.a_paper_sent = A("Mark paper QSL sent…", lambda: mark_paper(self, self.db, self.view.selected_ids(),
                                                                        "sent"))
        self.a_paper_rcvd = A("Mark paper QSL received…", lambda: mark_paper(self, self.db,
                                                                            self.view.selected_ids(), "rcvd"))
        self.a_paper_queue = A("Queue paper QSL to send", lambda: mark_paper(self, self.db,
                                                                            self.view.selected_ids(), "queue"))
        self.a_recompute = A("Recompute country, DXCC and zones", self.recompute_dxcc)
        self.a_cty = A("Update country file (cty.csv)", lambda: self.update_cty(quiet=False))
        self.a_scp = A("Update MASTER.SCP (similar calls)", self.update_scp)
        self.a_lists = A("Update LoTW and eQSL call lists (similar calls)", lambda: self.update_call_lists(quiet=False))
        self.a_check_backups = A("Check backups for saved passwords…", lambda: self.check_secrets("menu"))
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
        self.a_about = A("About NWOlogger", self.about)
        self.a_log_folder = A("Show log folder", self.show_log_folder)
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
        self.sync_acts = sync_acts
        self.a_sync_all = A("Sync everything", self.sync_all, "Ctrl+Shift+S", "refresh-cw",
                            tr("Download confirmations from LoTW, eQSL and QRZ.com. Nothing is uploaded: grey "
                               "QSOs are not yet on every service; select them and use Upload selected."))
        for a, short in ((self.a_import, "Import"), (self.a_search, "Search"), (self.a_bulk, "Bulk edit"),
                         (self.a_prefs, "Prefs"), (self.a_undo, "Undo")):
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
        for a in (self.a_new_log, self.a_open_log, None, self.a_import, self.a_export_all, self.a_export_sel,
                  self.a_export_flt, None, self.a_backup, self.a_check_backups, self.a_cty, self.a_scp, self.a_lists, None,
                  self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&Edit"))
        for a in (self.a_undo, None, self.a_edit, self.a_bulk, self.a_delete, self.a_select_all, None,
                  self.a_dupes, self.a_review, self.a_not_dupe, self.a_show_dupe, None, self.a_operator,
                  self.a_recompute, self.a_lookup_fill,
                  None, self.a_prefs):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&View"))
        for a in (self.a_view_log, self.a_view_contest, None, self.a_log_tab, self.a_paper_tab, self.a_hist_tab,
                  None, self.a_search, self.a_clear_filters, self.a_suspicious, None, self.a_palette):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu(tr("&QSL"))
        for a in (self.a_paper_sent, self.a_paper_rcvd, self.a_paper_queue):
            m.addAction(a)
        m.addSeparator()
        for svc in ("lotw", "eqsl", "qrz", "hrdlog"):
            sub = m.addMenu({"lotw": "LoTW", "eqsl": "eQSL", "qrz": "QRZ.com", "hrdlog": "HRDLog"}[svc])
            for k in (f"{svc}_down", f"{svc}_up", f"{svc}_up_sel"):
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
            for k in (f"{svc}_down", f"{svc}_up", f"{svc}_up_sel"):
                if k in sync_acts:
                    sub.addAction(sync_acts[k])
        sm.addSeparator()
        sm.addAction(self.a_sync_all)
        syn.setMenu(sm)
        tb.addWidget(syn)
        tb.addAction(self.a_bulk)
        tb.addAction(self.a_undo)
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
        self.splitter.addWidget(self.entry_stack)

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
        self.tabs.addTab(log_tab, theme.icon("list"), tr("Log"))
        self.paper = PaperQslTab(self.db)
        self.tabs.addTab(self.paper, theme.icon("mail"), tr("Paper QSL"))
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
        self.tabs.addTab(hist_tab, theme.icon("history"), tr("Sync history"))
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
        self.st_msg.setText(text)
        # The label is the timer's context, so a window closed within 8 s doesn't get the call.
        QTimer.singleShot(8000, self.st_msg, lambda t=text: self.st_msg.setText("") if self.st_msg.text() == t
                          else None)

    def _on_db_change(self, kind: str, _ids: list[int]) -> None:
        if kind in ("insert", "delete", "reset", "update"):
            self._update_actions()
        if kind == "profiles":
            self.dx.reload_profiles()
        if kind == "sync_log" and self.tabs.currentIndex() == 2:
            self._refresh_history()

    def _update_actions(self) -> None:
        n = len(self.view.selected_ids())
        for a in (self.a_bulk, self.a_delete, self.a_export_sel, self.a_paper_sent, self.a_paper_rcvd,
                  self.a_paper_queue, self.a_recompute, self.a_lookup_fill, self.a_not_dupe):
            a.setEnabled(n > 0)
        self.a_edit.setEnabled(n == 1)
        self.a_show_dupe.setEnabled(n == 1)
        for k, a in self.sync_acts.items():
            if k.endswith("_up_sel"):
                a.setEnabled(n > 0)
        label = self.db.last_undo_label()
        self.a_undo.setEnabled(bool(label))
        self.a_undo.setText(tr("Undo") + (f" “{label}”" if label else ""))
        self.a_undo.setIconText(tr("Undo"))
        self.a_undo.setToolTip(self.a_undo.text())

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
        # Only the visible page decides how small the entry area may get.
        for k in range(self.entry_stack.count()):
            pol = QSizePolicy.Policy.Preferred if k == i else QSizePolicy.Policy.Ignored
            self.entry_stack.widget(k).setSizePolicy(pol, pol)
        self.entry_stack.updateGeometry()
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

    def undo(self) -> None:
        label = self.db.undo_last()
        if label:
            self.flash(tr("Undone") + f": {label}")
        self._update_actions()

    def bulk_edit(self) -> None:
        ids = self.view.selected_ids()
        if not ids:
            return
        dlg = BulkEditDialog(self.db, ids, self)
        if dlg.exec():
            self.flash(tr("Bulk edit applied to") + f" {dlg.changed_count} " + tr("QSOs") + " · " +
                       tr("Undo is in the Edit menu"))

    def delete_selected(self) -> None:
        ids = self.view.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, tr("Delete"), f"{tr('Delete')} {len(ids)} {tr('QSOs')}? " +
                                tr("You can undo this.")) != QMessageBox.StandardButton.Yes:
            return
        n = self.db.delete(ids, label=tr("Delete") + f" {len(ids)}")
        self.flash(tr("Deleted") + f" {n} " + tr("QSOs"))

    def find_dupes(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            d, s = dupes.rescan(self.db, block_s=self.prefs.get("block_seconds"),
                                warn_s=self.prefs.get("warn_hours") * 3600)
        finally:
            QApplication.restoreOverrideCursor()
        if not (d or s):
            QMessageBox.information(self, tr("Find duplicates"), tr("No duplicates found."))
            return
        if QMessageBox.question(self, tr("Find duplicates"), f"{d} " + tr("dupes") + f", {s} " +
                                tr("suspicious dupes marked.") + "\n\n" + tr("Review them side by side now?")
                                ) == QMessageBox.StandardButton.Yes:
            self.review_dupes()
        self.filter.set_preset("suspicious")

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

    def recompute_dxcc(self) -> None:
        ids = self.view.selected_ids()
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

    def lookup_fill_selected(self) -> None:
        ids = self.view.selected_ids()
        rows = self.db.get_many(ids)
        calls = sorted({q["call"] for q in rows.values() if q.get("call")})
        if not calls:
            return
        clients = self.lookup_clients()
        ttl = self.prefs.get("lookup_ttl_days")
        cached = {c: self.db.cache_get(c, ttl) for c in calls}
        todo = [c for c in calls if not cached[c]]
        if todo and not clients:
            QMessageBox.information(self, tr("Lookup"), tr("Set a QRZ.com or HamQTH account in Preferences → Lookup."))
            if not any(cached.values()):
                return
        self.flash(tr("Looking up") + f" {len(todo)} " + tr("calls…"))

        def work():
            out = {}
            for c in todo:
                data, source, _err = lookup_clients.lookup_chain(c, clients)
                if data:
                    out[c] = (data, source)
            return out

        def done(found):
            for c, (data, source) in found.items():
                self.db.cache_put(c, source or "", data)
                self.dx.note_known(c, source or "")
                cached[c] = data
            cols = ["first_name", "last_name", "qth", "address", "state", "cnty", "gridsquare", "email", "web",
                    "qsl_via", "iota"]

            def ch(q: dict) -> dict:
                d = cached.get(q.get("call")) or {}
                out = {c: d[c] for c in cols if d.get(c) and not q.get(c)}
                if out.get("state") and q.get("dxcc") == regions.CHINA_DXCC:
                    out["state"] = regions.normalize_cn(out["state"]) or out["state"]
                return out

            n = self.db.update_many(ids, ch, label=tr("Look up and fill"))
            self.flash(tr("Filled") + f" {n} " + tr("QSOs"))

        run_async(work, on_done=done, on_error=lambda m: self.flash(self._safe_lookup_error(m)))

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
        if dlg.exec() and dlg.result:
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
        self._clock_timer.stop()
        self.dx._clock.stop()
        self.dx._check_timer.stop()
        self.dx._lookup_timer.stop()
        self.dx._index_timer.stop()
        self.db.remove_listener(self.dx._on_db_change)
        if self.contest is not None and hasattr(self.contest, "shutdown"):
            self.contest.shutdown()

    def _switch_db(self, path: str) -> None:
        self.shutdown()
        self.db.close()
        self.prefs.set("db_path", path)
        QMessageBox.information(self, APP_NAME, tr("NWOlogger will restart with") + f"\n{path}")
        self.app.exit(1000)  # restart code

    def new_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("New log file"), str(dbm.DEFAULT_DIR / "new-log.sqlite"),
                                              "SQLite (*.sqlite)")
        if path:
            self._switch_db(path)

    def open_log(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Open log file"), str(dbm.DEFAULT_DIR), "SQLite (*.sqlite)")
        if path:
            self._switch_db(path)

    # ------------------------------------------------------------------ misc
    def sync_all(self) -> None:
        """Downloads only. Uploads are always your choice (Upload pending / Upload selected): a QSO sent to LoTW
        or eQSL can't be taken back, and eQSL sends a card to the other station."""
        for svc in ("lotw", "eqsl", "qrz"):
            self.sync.download(svc)

    def open_prefs(self, page: str | None = None) -> None:
        dlg = PrefsDialog(self.db, self.prefs, self, page=page)
        if dlg.exec():
            theme.apply(self.app, self.prefs.get("theme"))
            self._lookup_clients = None
            self.check_secrets("prefs")
            self.dx.reload_profiles()
            self.dx.reload_lists()
            self.dx.clear_form()
            self.model.refresh()
        else:
            self.dx.reload_profiles()

    def palette(self) -> None:
        CommandPalette(self.actions_, self).exec()

    def about(self) -> None:
        AboutDialog(self.db, self).exec()

    def show_log_folder(self) -> None:
        folder = applog.log_dir()
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
            dxcc.default.cache_clear()
            self.dx.cty = dxcc.default()
            self.flash(tr("Country file updated") + f" ({n} " + tr("entities") + ")")

        def fail(msg):
            if not quiet:
                QMessageBox.warning(self, tr("Country file"), msg)

        run_async(dxcc.download, on_done=done, on_error=fail)

    def update_scp(self) -> None:
        def done(n):
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
            self.dx.reload_call_lists()
            self.flash(tr("Call lists updated") + f": {counts.get('lotw', 0):,} " + tr("LoTW users") +
                       f", {counts.get('eqsl_ag', 0):,} " + tr("eQSL AG members"))

        def fail(msg: str) -> None:
            if quiet:
                self.flash(msg)
            else:
                QMessageBox.warning(self, tr("Call lists"), msg)

        run_async(calllists.download, folder, on_done=done, on_error=fail)

    SECRET_CHECK = "2"  # bump to run the check once more after an update

    def check_secrets(self, reason: str = "start") -> None:
        """Look for saved passwords and keys left in this log and its backups, and clean the log.

        NWOlogger 0.1.0 could save a LoTW / eQSL password in sync history after an HTTP error, and kept credential
        fields of imported ADIF records. Patterns are cleaned when the log opens; this check also uses the saved
        secrets, so it reads the keychain: once after an update (``start``), after each Preferences save
        (``prefs``, quiet unless something is found) and from File → Check backups for saved passwords (``menu``).
        Backups are only listed, for you to delete."""
        if reason == "start" and self.db.meta("secret_check") == self.SECRET_CHECK:
            return
        values = self.prefs.saved_secrets({p.get("station_callsign") for p in self.db.profiles()})
        cleaned = self.db.scrub_secrets(values)
        if reason == "start":
            cleaned += int(self.db.meta("secrets_scrubbed") or 0)
        if cleaned and reason == "start":
            self.db.renew_open_backup()  # nothing changed since open but the cleanup: retake that copy clean
        found = self.db.backups_with_secrets(values)
        pending = self.db.purge_pending()
        self.db.set_meta("secret_check", self.SECRET_CHECK)
        if not cleaned and not found and not pending:
            if reason == "menu":
                QMessageBox.information(self, tr("Saved passwords"), tr(
                    "No backup holds any of your saved passwords or keys, or a password in a web address. "
                    "Passwords you have changed since can't be recognised."))
            return
        text = []
        if cleaned:
            text.append(tr("Items that held a password or key were cleaned in this log (sync history, imported "
                           "fields, undo history):") + f" {cleaned}.")
        if pending:
            text.append(tr("Another program has this log file open. The cleanup finishes when it closes the file."))
        if found:
            text.append(tr("These backups still hold one. Delete them (Show in Finder / Explorer):") + "\n• " +
                        "\n• ".join(f"{p.name} ({n})" for p, n in found))
        text.append(tr("Change those passwords and keys at LoTW, eQSL, QRZ.com or HRDLog: copies may also be in "
                       "Time Machine or cloud backups."))
        box = QMessageBox(QMessageBox.Icon.Warning, tr("Saved passwords"), "\n\n".join(text), parent=self)
        show = box.addButton(tr("Show backups"), QMessageBox.ButtonRole.ActionRole) if found else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if show is not None and box.clickedButton() is show:
            from PySide6.QtCore import QUrl  # noqa: PLC0415
            from PySide6.QtGui import QDesktopServices  # noqa: PLC0415

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.db.backup_dir)))

    def _first_run(self) -> None:
        self.check_secrets("start")
        age = dxcc.user_copy_age_days()
        if (age is None or age > 7) and not os.environ.get("NWOLOGGER_OFFLINE"):
            QTimer.singleShot(3000, lambda: self.update_cty(quiet=True))
        lists_age = calllists.age_days(calllists.folder_for(self.db.path))
        if (lists_age is None or lists_age > calllists.REFRESH_DAYS) and not os.environ.get("NWOLOGGER_OFFLINE"):
            QTimer.singleShot(6000, lambda: self.update_call_lists(quiet=True))
        if not self.db.profiles():
            QMessageBox.information(self, APP_NAME, tr("Welcome! Create a station profile first: your callsign "
                                                       "and grid are added to every QSO you log."))
            self.open_prefs("Station profiles")

    def closeEvent(self, e) -> None:  # noqa: N802
        running = self.sync.running()
        if running and QMessageBox.question(
                self, APP_NAME, tr("Still running:") + " " + ", ".join(running) + ".\n\n" +
                tr("Quit anyway? TQSL is stopped; QSOs not reported as uploaded stay pending and can be uploaded "
                   "again later.")) != QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        if running:
            self.sync.cancel_all()
        self.prefs.set_value("window/geometry", self.saveGeometry())
        self.prefs.set_value("window/splitter", self.splitter.saveState())
        self.prefs.set_value("log/columns", self.view.visual_columns())
        if self.contest is not None:
            self.contest.save_state()
        self.shutdown()
        super().closeEvent(e)
