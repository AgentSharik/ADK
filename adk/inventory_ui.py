"""Окно «Excel-опись ПК» (переработано в 3.2.2).

Три шага в одном окне: слева — организации из AD с фильтром и счётчиком; по центру — **предпросмотр**
таблицы описи (то, что попадёт в файл) со сводными плитками; справа — состав колонок галочками.
Кнопка «Сохранить Excel» активна только после предпросмотра. Excel пишет :func:`adk.workers.write_inventory_xlsx`:
титул, автофильтр, закреплённая шапка, зебра, подсветка «Не привязан» и второй лист «Сводка».
"""
from __future__ import annotations

import os
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import ad
from .config import CREATE_NO_WINDOW
from .widgets import FramelessDialog, MessageBox, app_palette, fit_columns, run_in_background
from .workers import INVENTORY_COLUMNS, INVENTORY_DEFAULT, InventoryWorker


def _tile(title: str, value: str) -> QFrame:
    pal = app_palette()
    f = QFrame()
    f.setObjectName("dashCard")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(0)
    t = QLabel(title)
    t.setStyleSheet(f"color: {pal.subtext}; font-size: 8pt; font-weight: bold; letter-spacing: 0.5px;")
    v = QLabel(value)
    v.setObjectName("tileValue")
    v.setStyleSheet(f"font-size: 15pt; font-weight: bold; color: {pal.title_accent};")
    lay.addWidget(t)
    lay.addWidget(v)
    return f


class InventoryDialog(FramelessDialog):
    def __init__(self, app, parent=None):
        super().__init__("📊 Excel-опись ПК по организации", parent, (1120, 680))
        self.app = app
        self.worker: InventoryWorker | None = None
        self.out_dir = ""
        self.companies: list[str] = []
        self.rows: list[dict] = []
        self.company = ""
        pal = app_palette()
        root = QHBoxLayout()
        root.setSpacing(12)
        self.body.addLayout(root, 1)

        # ============ 1. организации
        left = QFrame()
        left.setObjectName("dashCard")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 10, 12, 10)
        ll.addWidget(QLabel("<b>1. Организация</b>"))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("🔍 Фильтр…")
        self.filter.textChanged.connect(self._apply_filter)
        ll.addWidget(self.filter)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._company_changed)
        self.list.itemDoubleClicked.connect(lambda _it: self.preview())
        ll.addWidget(self.list, 1)
        self.lbl_companies = QLabel("Загрузка списка организаций…")
        self.lbl_companies.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt;")
        ll.addWidget(self.lbl_companies)
        self.btn_preview = QPushButton("👁 Предпросмотр")
        self.btn_preview.setObjectName("btnPrimary")
        self.btn_preview.setEnabled(False)
        self.btn_preview.clicked.connect(self.preview)
        ll.addWidget(self.btn_preview)
        root.addWidget(left, 3)

        # ============ 2. предпросмотр
        mid = QVBoxLayout()
        mid.setSpacing(8)
        self.lbl_title = QLabel("<b>2. Что попадёт в файл</b> — выберите организацию слева")
        mid.addWidget(self.lbl_title)
        self.tiles = QHBoxLayout()
        self.tiles.setSpacing(8)
        mid.addLayout(self.tiles)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        mid.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        mid.addWidget(self.status)
        root.addLayout(mid, 8)

        # ============ 3. колонки + сохранение
        right = QFrame()
        right.setObjectName("dashCard")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 10, 12, 10)
        rl.addWidget(QLabel("<b>3. Колонки</b>"))
        self.checks: dict[str, QCheckBox] = {}
        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        for i, (key, label) in enumerate(INVENTORY_COLUMNS):
            cb = QCheckBox(label)
            cb.setChecked(key in INVENTORY_DEFAULT)
            cb.toggled.connect(self._columns_changed)
            self.checks[key] = cb
            grid.addWidget(cb, i, 0)
        rl.addLayout(grid)
        quick = QHBoxLayout()
        b_all = QPushButton("Все")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_std = QPushButton("Стандарт")
        b_std.clicked.connect(lambda: [cb.setChecked(k in INVENTORY_DEFAULT) for k, cb in self.checks.items()])
        quick.addWidget(b_all)
        quick.addWidget(b_std)
        rl.addLayout(quick)
        rl.addStretch()
        rl.addWidget(QLabel("<b>Папка</b>"))
        self.btn_dir = QPushButton("📁 Выбрать…")
        self.btn_dir.clicked.connect(self.choose_dir)
        rl.addWidget(self.btn_dir)
        self.cb_open = QCheckBox("Открыть после сохранения")
        self.cb_open.setChecked(True)
        rl.addWidget(self.cb_open)
        self.btn_go = QPushButton("💾 Сохранить Excel")
        self.btn_go.setObjectName("btnSuccess")
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self.generate)
        rl.addWidget(self.btn_go)
        root.addWidget(right, 3)

        foot = QHBoxLayout()
        foot.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.close)
        foot.addWidget(close)
        self.body.addLayout(foot)

        def load():
            c = self.app.get_conn()
            try:
                return ad.get_all_attribute_values(c, "company")
            finally:
                c.unbind()

        def done(items):
            self.companies = items
            self._apply_filter("")
            self.lbl_companies.setText(f"Организаций: {len(items)}")

        run_in_background(self, load, done, lambda m: self.lbl_companies.setText(f"⚠️ {m}"))

    # ------------------------------------------------------------------ шаг 1
    def _apply_filter(self, text: str):
        q = text.strip().lower()
        self.list.clear()
        for c in self.companies:
            if q in c.lower():
                self.list.addItem(QListWidgetItem(f"🏢 {c}"))

    def selected_company(self) -> str:
        items = self.list.selectedItems()
        return items[0].text()[2:] if items else ""

    def _company_changed(self):
        self.btn_preview.setEnabled(bool(self.selected_company()))

    # ------------------------------------------------------------------ шаг 2
    def preview(self):
        company = self.selected_company()
        if not company or (self.worker and self.worker.isRunning()):
            return
        self.company = company
        self.rows = []
        self.btn_go.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.lbl_title.setText(f"<b>2. Что попадёт в файл</b> — {company}")
        self.status.setText("⏳ Собираю данные…")
        self.worker = InventoryWorker(self.app.get_conn, company, "", parent=self, preview=True)
        self.worker.progress.connect(self.status.setText)
        self.worker.rows_ready.connect(self.show_rows)
        self.worker.finished_export.connect(lambda ok, msg: self._fail(msg))
        self.worker.start()

    def _fail(self, msg: str):
        self.btn_preview.setEnabled(True)
        self.status.setText(f"⚠️ {msg}")

    def columns(self) -> tuple[str, ...]:
        return tuple(k for k, _l in INVENTORY_COLUMNS if self.checks[k].isChecked())

    def show_rows(self, rows: list[dict]):
        """Заполнить предпросмотр (вызывается и напрямую — для тестов и демо)."""
        self.rows = rows
        self.btn_preview.setEnabled(True)
        self.btn_go.setEnabled(bool(rows) and bool(self.columns()))
        self._fill_table()
        while self.tiles.count():
            it = self.tiles.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.hide()
                wdg.deleteLater()
        with_pc = sum(1 for r in rows if r["comp"] != "Не привязан")
        online = sum(1 for r in rows if r["online"] == "да")
        oses = {r["os"] for r in rows if r["os"] not in ("—", "Н/Д")}
        for t, v in (("СОТРУДНИКОВ", str(len(rows))), ("С ПК", str(with_pc)), ("БЕЗ ПК", str(len(rows) - with_pc)),
                     ("В СЕТИ", str(online)), ("ВЕРСИЙ ОС", str(len(oses)))):
            self.tiles.addWidget(_tile(t, v))
        self.status.setText(f"Предпросмотр: {len(rows)} строк, {len(self.columns())} колонок. "
                            "Данные из AD, инвентаря и CSV — ничего не записано.")

    def _fill_table(self):
        cols = self.columns()
        labels = dict(INVENTORY_COLUMNS)
        pal = app_palette()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels([labels[c] for c in cols])
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            for c, key in enumerate(cols):
                it = QTableWidgetItem(str(row.get(key, "")))
                if key == "comp" and row.get("comp") == "Не привязан":
                    it.setForeground(Qt.GlobalColor.red if not pal.is_dark else Qt.GlobalColor.white)
                    it.setToolTip("У сотрудника нет привязанного ПК")
                self.table.setItem(r, c, it)
        fit_columns(self.table, max_width=300, min_width=70, wrap=False)

    # ------------------------------------------------------------------ шаг 3
    def _set_all(self, v: bool):
        for cb in self.checks.values():
            cb.setChecked(v)

    def _columns_changed(self):
        if self.rows:
            self._fill_table()
        self.btn_go.setEnabled(bool(self.rows) and bool(self.columns()))

    def choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Папка для описи")
        if path:
            self.out_dir = path
            self.btn_dir.setText(f"📁 {os.path.basename(path) or path}")

    def generate(self):
        if not self.rows or not self.columns():
            MessageBox.warning(self, "Внимание", "Сначала сделайте предпросмотр и выберите хотя бы одну колонку.")
            return
        if not self.out_dir:
            self.choose_dir()
            if not self.out_dir:
                return
        if self.worker and self.worker.isRunning():
            return
        self.btn_go.setEnabled(False)
        self.status.setText("⏳ Запись Excel…")
        self.worker = InventoryWorker(self.app.get_conn, self.company, self.out_dir, parent=self,
                                      columns=self.columns(), rows=self.rows)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished_export.connect(self._done)
        self.worker.start()

    def _done(self, ok: bool, msg: str):
        self.btn_go.setEnabled(True)
        if not ok:
            self.status.setText(f"⚠️ {msg}")
            MessageBox.critical(self, "Опись", msg)
            return
        self.status.setText(f"✅ Сохранено: {msg}")
        if self.cb_open.isChecked() and os.name == "nt":
            try:
                subprocess.Popen(["explorer", "/select,", msg], creationflags=CREATE_NO_WINDOW)
            except OSError:
                pass
        MessageBox.information(self, "Опись", f"Опись сохранена:\n{msg}")

    def on_dialog_done(self):
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
