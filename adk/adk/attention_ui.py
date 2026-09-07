"""Диалог «Внимание»: список того, что требует реакции сегодня, с кнопками «отложить» и переходом к объекту."""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem

from . import access, attention, db
from .config import settings
from .widgets import FramelessDialog, MessageBox, app_palette, fit_columns, run_in_background

log = logging.getLogger(__name__)


class AttentionDialog(FramelessDialog):
    def __init__(self, app, parent=None, items: list[dict] | None = None):
        super().__init__("🔔 Внимание: что требует реакции", parent, (900, 560))
        self.app = app
        top = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        self.chk_low = QCheckBox("Показывать низкий приоритет")
        self.chk_low.setChecked(True)
        self.chk_low.toggled.connect(self.render)
        top.addWidget(self.chk_low)
        btn = QPushButton("🔄 Обновить")
        btn.clicked.connect(self.load)
        top.addWidget(btn)
        self.body.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Тип", "Кто / что", "Подробности"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 36)
        self.table.itemDoubleClicked.connect(self.open_selected)
        self.body.addWidget(self.table, 1)
        btns = QHBoxLayout()
        self.btn_open = QPushButton("🔍 Найти в главном окне")
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_snooze = QPushButton("💤 Отложить на 7 дней")
        self.btn_snooze.clicked.connect(lambda: self.snooze(7))
        self.btn_snooze30 = QPushButton("💤 На 30 дней")
        self.btn_snooze30.clicked.connect(lambda: self.snooze(30))
        for b in (self.btn_open, self.btn_snooze, self.btn_snooze30):
            btns.addWidget(b)
        btns.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        self.body.addLayout(btns)
        if not access.can("attention_snooze"):
            self.btn_snooze.setEnabled(False)
            self.btn_snooze30.setEnabled(False)
        self.items: list[dict] = items or []
        if items is None:
            self.load()
        else:
            self.render()

    def load(self):
        self.summary.setText("⏳ Собираю сводку (AD + инвентарь)…")
        run_in_background(self, lambda: attention.collect_from_settings(self.app.get_conn, settings.attention),
                          self._loaded, lambda m: self.summary.setText(f"⚠️ {m}"))

    def _loaded(self, items: list[dict]):
        self.items = items
        self.render()
        if hasattr(self.app, "set_attention_items"):
            self.app.set_attention_items(items)

    def render(self):
        pal = app_palette()
        colors = {"high": pal.danger[0], "medium": pal.warning[0], "low": pal.subtext}
        rows = [i for i in self.items if self.chk_low.isChecked() or i["severity"] != "low"]
        self.table.setRowCount(0)
        for i in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate((i["icon"], i["title"], i["subject"], i["text"])):
                it = QTableWidgetItem(v)
                it.setForeground(QColor(colors.get(i["severity"], pal.text)))
                it.setData(Qt.ItemDataRole.UserRole, i["key"])
                self.table.setItem(r, c, it)
        fit_columns(self.table, max_width=360)
        self.summary.setText(attention.summary_line(self.items))

    def _current(self) -> dict | None:
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        key = it.data(Qt.ItemDataRole.UserRole) if it else None
        return next((i for i in self.items if i["key"] == key), None)

    def open_selected(self, *_):
        i = self._current()
        if i and hasattr(self.app, "search_text"):
            self.app.search_text(i["subject"])
            self.accept()

    def snooze(self, days: int):
        i = self._current()
        if not i:
            MessageBox.information(self, "Внимание", "Выберите строку.")
            return
        db.snooze(i["key"], days, self.app.admin_name)
        db.log_action(self.app.admin_name, "attention_snooze", i["subject"], f"{i['title']} — {days} дн.")
        self.items = [x for x in self.items if x["key"] != i["key"]]
        self.render()
        if hasattr(self.app, "set_attention_items"):
            self.app.set_attention_items(self.items)
