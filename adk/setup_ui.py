"""Первый запуск: где лежит база ADK (3.5.11).

До окна входа ADK спрашивает, **где будет лежать или уже лежит** файл базы ``pc_mapping.db`` — инвентарь парка, история,
заметки, принтеры. Логика:

* `config.ini` ещё не решал вопрос базы (ключ ``[Paths] db_ready`` не выставлен) → показать :class:`DbSetupDialog`;
* пользователь выбирает папку (по умолчанию ``Documents\\ADK``). Если в ней уже есть ``pc_mapping.db`` — ADK просто
  использует его («вторая копия ADK, ярлык на общую установку»); если нет — файл создаётся, и после входа
  запускается **первичное наполнение** (:class:`InitialFillWorker`): сканер парка (ПК из AD, IP, кто залогинен,
  последний вход) и живой опрос принтеров со всех ПК в сети;
* решение записывается в ``config.ini`` (``db_path`` + ``db_ready = true``), второй раз вопрос не задаётся.

Рекомендация в окне и в документации: установить ADK на **одном** ПК, а на остальных сделать ярлык на этот ``ADK.exe``
— тогда база одна и заполнять её нужно один раз.
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QRadioButton, QSizePolicy, QVBoxLayout)

from . import db
from .config import DOCS_DIR, PORTABLE_DIR, park_pattern, settings
from .i18n import tr  # noqa: E402
from .widgets import FramelessDialog, app_palette
from .workers import BaseWorker, PCScannerWorker

log = logging.getLogger(__name__)

DB_FILE = "pc_mapping.db"


def needs_db_setup() -> bool:
    """Вопрос о базе ещё не решён: в config.ini нет ``db_ready`` **и** база по текущему пути пуста/отсутствует.

    Установки до 3.5.11 (база уже есть и заполнена) вопрос не видят — им просто выставляется флаг.
    """
    if getattr(settings, "db_ready", False):
        return False
    path = settings.db_path
    if path and os.path.exists(path) and os.path.getsize(path) > 0 and db_has_inventory(path):
        settings.save_section("Paths", {"db_path": path, "db_ready": "true"})
        settings.reload()
        return False
    return True


def db_has_inventory(path: str) -> bool:
    """Есть ли в файле хоть один ПК в инвентаре (иначе это пустая заготовка — её надо наполнять)."""
    import contextlib
    import sqlite3
    try:
        with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as c:
            row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pc_inventory'").fetchone()
            if not row:
                return False
            return int(c.execute("SELECT COUNT(*) FROM pc_inventory").fetchone()[0]) > 0
    except sqlite3.Error:
        return False


def describe_db(path: str) -> str:
    """Короткое описание найденного файла: сколько ПК, когда обновлялся."""
    import contextlib
    import sqlite3
    try:
        with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as c:
            n, last = c.execute("SELECT COUNT(*), MAX(last_checked) FROM pc_inventory").fetchone()
        return f"в базе {n} ПК" + (f", обновлялась {str(last)[:16]}" if last else "")
    except sqlite3.Error as exc:
        return f"файл не читается: {exc}"


class DbSetupDialog(FramelessDialog):
    """«Где лежит база ADK?» — папка + подсказка, что в ней найдено. Результат: ``self.db_path``, ``self.is_new``."""

    def __init__(self, parent=None):
        super().__init__("🗄️ База ADK — первый запуск", parent, (660, 600))
        self.setMinimumSize(600, 560)
        self.db_path: str = ""
        self.is_new: bool = True
        pal = app_palette()
        self.body.setSpacing(10)
        self.body.setContentsMargins(18, 4, 18, 4)

        intro = QLabel(tr("ADK хранит инвентарь парка, историю, заметки и принтеры в одном файле <b>pc_mapping.db</b>.<br>"
            "Укажите папку, где он <b>уже лежит</b> (если ADK у вас уже установлен на другом ПК) или где его <b>создать</b>."))
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        card = QFrame()
        card.setObjectName("dashCard")
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)   # карточка не ужимается под текст ниже
        cl = QVBoxLayout(card)
        cl.setSpacing(8)
        cl.setContentsMargins(14, 12, 14, 12)
        self.grp = QButtonGroup(self)
        self.rb_default = QRadioButton(tr("Рядом с программой (папка ADK возле ADK.exe — конфиг и база всегда при нём)")
                                       if PORTABLE_DIR else "В моих документах (база только на этом ПК)")
        self.rb_default.setToolTip(os.path.join(DOCS_DIR, DB_FILE))
        self.rb_default.setMinimumHeight(26)
        self.lbl_default_path = QLabel(os.path.join(DOCS_DIR, DB_FILE))
        self.lbl_default_path.setObjectName("subtle")
        self.lbl_default_path.setContentsMargins(26, 0, 0, 0)
        self.rb_custom = QRadioButton(tr("В другой папке — общая для отдела, сетевая или уже существующая"))
        self.rb_custom.setMinimumHeight(26)
        self.grp.addButton(self.rb_default, 0)
        self.grp.addButton(self.rb_custom, 1)
        cl.addWidget(self.rb_default)
        cl.addWidget(self.lbl_default_path)
        cl.addWidget(self.rb_custom)
        row = QHBoxLayout()
        row.setContentsMargins(26, 0, 0, 0)
        self.path_in = QLineEdit()
        self.path_in.setPlaceholderText(r"например D:\ADK или \\server\share\ADK")
        self.path_in.setMinimumHeight(34)
        self.btn_browse = QPushButton(tr("📁 Обзор…"))
        self.btn_browse.setMinimumHeight(34)
        self.btn_browse.clicked.connect(self.browse)
        row.addWidget(self.path_in, 1)
        row.addWidget(self.btn_browse)
        cl.addLayout(row)
        self.body.addWidget(card)

        self.lbl_found = QLabel("")
        self.lbl_found.setWordWrap(True)
        self.lbl_found.setObjectName("subtle")
        self.lbl_found.setMinimumHeight(84)
        self.lbl_found.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.addWidget(self.lbl_found)

        hint = QLabel(tr("💡 <b>Совет.</b> Установите ADK на <b>одном</b> компьютере, а на остальных создайте ярлык на его <code>ADK.exe</code> — "
            "база будет одна, и заполнять её нужно один раз. Если баз несколько, инвентарь и заметки у коллег будут разными."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {pal.text}; background: {pal.info[1]}; border: 1px solid {pal.info[2]}; "
                           f"border-radius: 8px; padding: 8px 10px;")
        self.body.addWidget(hint)
        self.body.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_cancel = QPushButton(tr("Выход"))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton(tr("Продолжить"))
        self.btn_ok.setObjectName("btnPrimary")
        self.btn_ok.setMinimumHeight(40)
        self.btn_ok.clicked.connect(self.finish)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        self.body.addLayout(btns)

        self.rb_default.toggled.connect(self._refresh)
        self.path_in.textChanged.connect(self._refresh)
        self.rb_default.setChecked(True)
        self._refresh()

    # ------------------------------------------------------------------ логика
    def chosen_path(self) -> str:
        if self.rb_default.isChecked():
            return os.path.join(DOCS_DIR, DB_FILE)
        folder = self.path_in.text().strip().strip('"')
        if not folder:
            return ""
        if folder.lower().endswith(".db"):
            return folder
        return os.path.join(folder, DB_FILE)

    def _refresh(self, *_):
        self.path_in.setEnabled(self.rb_custom.isChecked())
        self.btn_browse.setEnabled(self.rb_custom.isChecked())
        path = self.chosen_path()
        if not path:
            self.lbl_found.setText(tr("Укажите папку."))
            self.btn_ok.setEnabled(False)
            return
        self.btn_ok.setEnabled(True)
        net = ""
        if db.is_network_path(path):
            net = ("<br>⚠️ Сетевая папка: подходит, если ADK запускают <b>ярлыком с одного сервера</b> — "
                   "тогда база одна и парк сканирует один экземпляр.")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            if db_has_inventory(path):
                self.is_new = False
                self.lbl_found.setText(f"✅ База найдена: {describe_db(path)}. ADK будет использовать её как есть.{net}")
                self.btn_ok.setText(tr("Использовать эту базу"))
            else:
                self.is_new = True
                self.lbl_found.setText(f"ℹ️ Файл есть, но инвентарь в нём пуст — после входа ADK заполнит его: опросит домен и ПК.{net}")
                self.btn_ok.setText(tr("Продолжить"))
        else:
            self.is_new = True
            self.lbl_found.setText(tr("🆕 Базы здесь нет — она будет создана, и после входа ADK заполнит её: ") +
                                   f"ПК из домена, их адреса, кто за ними работает, принтеры.{net}")
            self.btn_ok.setText(tr("Создать базу здесь"))

    def browse(self):
        start = self.path_in.text().strip() or DOCS_DIR
        d = QFileDialog.getExistingDirectory(self, "Папка для базы ADK", start)
        if d:
            self.path_in.setText(d)
            self.rb_custom.setChecked(True)

    def finish(self):
        path = self.chosen_path()
        if not path:
            return
        folder = os.path.dirname(path)
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".adk_write_test")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as exc:
            self.lbl_found.setText(f"⚠️ В эту папку нельзя писать: {exc}")
            return
        self.db_path = path
        settings.save_section("Paths", {"db_path": path, "db_ready": "true"})
        settings.reload()
        self.accept()


# --------------------------------------------------------------------------- первичное наполнение
class InitialFillWorker(BaseWorker):
    """Новая база: сканер парка → живой опрос принтеров у ПК в сети. ``progress(text)``, ``finished_fill(summary)``."""
    progress = pyqtSignal(str)
    finished_fill = pyqtSignal(dict)

    def __init__(self, conn_factory, parent=None):
        super().__init__(parent)
        self.conn_factory = conn_factory

    def run(self) -> None:
        from . import fleetpoll
        from .workers import PCScannerWorker
        summary = {"pcs": 0, "printers_pcs": 0, "printers": 0, "error": ""}
        try:
            self.progress.emit("🗄️ Первичное наполнение базы: шаг 1 из 2 — ПК из домена (адреса, кто работает, последний вход)…")
            summary["pcs"] = PCScannerWorker.scan_once(self.conn_factory, progress=self.progress.emit)
            if self.cancelled:
                self.finished_fill.emit(summary)
                return
            hosts = fleetpoll.fleet_hosts(self.conn_factory, online_only=True)
            if not hosts:
                self.progress.emit("🗄️ Шаг 2 из 2 пропущен: ПК в сети не найдено — принтеры можно опросить позже («Принтеры парка»)")
                self.finished_fill.emit(summary)
                return
            self.progress.emit(f"🗄️ Шаг 2 из 2 — принтеры с {len(hosts)} ПК в сети…")
            results = fleetpoll.poll_fleet(
                hosts, fleetpoll.printers_live,
                progress=lambda i, n, h: self.progress.emit(f"🖨️ Принтеры: {i}/{n} · {h}"),
                cancelled=lambda: self.cancelled)
            for comp, r in results.items():
                if "printers" in r:
                    db.replace_printers(comp, r["printers"])
                    summary["printers_pcs"] += 1
                    summary["printers"] += len(r["printers"])
        except Exception as exc:  # noqa: BLE001
            log.exception("InitialFillWorker")
            summary["error"] = str(exc)
        self.finished_fill.emit(summary)


def fill_summary_text(s: dict) -> str:
    if s.get("error"):
        return f"⚠️ Первичное наполнение прервано: {s['error']} — повторите через «Сканировать парк»"
    return (f"✅ База заполнена: ПК из домена — {s['pcs']}, принтеры — {s['printers']} на {s['printers_pcs']} ПК. "
            "Дальше сканер обновляет её сам по расписанию.")


class ParkMaskDialog(FramelessDialog):
    """«Выберите имена ПК, с которыми будет работать ADK» (3.10.0).

    Показывается после выбора базы данных при первом запуске, если маска парка ещё не задана.
    Ввод по-человечески: «PC-» — все ПК на «PC-…» (→ ``PC-*``), «PC-0000» — ровно четыре цифры
    (→ ``PC-????``). Напротив каждого поля — сколько ПК домена подходит прямо сейчас. Кнопка «＋»
    добавляет поле, «✕» убирает. Результат сохраняется в ``[Scanner] host_mask``.
    """

    def __init__(self, parent, computer_names: list[str] | None = None):
        super().__init__("🏷️ Имена ПК парка", parent, (640, 520))
        self.setMinimumSize(560, 460)
        self.computer_names = computer_names or []
        self.mask_saved = False
        self.body.setSpacing(10)
        self.body.setContentsMargins(18, 4, 18, 4)

        intro = QLabel(tr("Выберите имена ПК, с которыми будет работать ADK — остальные машины домена ADK показывать не будет.<br>"
            "Пишите серию как удобно: <b>PC-</b> — все ПК, начинающиеся на PC; <b>PC-0000</b> — ПК "
            "<b>PC-</b> ровно с четырьмя цифрами. Несколько серий — несколько полей."))
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.rows_card = QFrame()
        self.rows_card.setObjectName("dashCard")
        self.rows_lay = QVBoxLayout(self.rows_card)
        self.rows_lay.setContentsMargins(14, 10, 14, 10)
        self.rows_lay.setSpacing(6)
        self.body.addWidget(self.rows_card)

        self.btn_add = QPushButton(tr("＋ Добавить серию"))
        self.btn_add.setMinimumHeight(34)
        self.btn_add.clicked.connect(lambda: self.add_row())
        self.body.addWidget(self.btn_add)

        self.total_lbl = QLabel("")
        self.total_lbl.setObjectName("subtle")
        self.total_lbl.setWordWrap(True)
        self.body.addWidget(self.total_lbl)

        self.body.addStretch(1)
        btns = QHBoxLayout()
        btn_skip = QPushButton(tr("Пропустить — все ПК домена"))
        btn_skip.clicked.connect(self.reject)
        self.btn_ok = QPushButton(tr("💾 Сохранить и продолжить"))
        self.btn_ok.setObjectName("btnPrimary")
        self.btn_ok.setMinimumHeight(40)
        self.btn_ok.clicked.connect(self.save)
        btns.addWidget(btn_skip)
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        self.body.addLayout(btns)

        if not self.computer_names:
            self.total_lbl.setText(tr("ℹ️ Список ПК домена недоступен — количество не показываю, маска всё равно сохранится."))
        self.add_row()
        self.recount()

    # --- строки ввода
    def add_row(self, text: str = ""):
        row = QHBoxLayout()
        edit = QLineEdit(text)
        edit.setMinimumHeight(34)
        edit.setPlaceholderText(tr("например PC- или PC-0000"))
        cnt = QLabel("")
        cnt.setObjectName("subtle")
        cnt.setMinimumWidth(90)
        btn_del = QPushButton("✕")
        btn_del.setFixedWidth(36)
        btn_del.setMinimumHeight(34)
        btn_del.setToolTip(tr("Убрать серию"))
        row.addWidget(edit, 1)
        row.addWidget(cnt)
        row.addWidget(btn_del)
        self.rows_lay.addLayout(row)
        edit.textChanged.connect(self.recount)
        btn_del.clicked.connect(lambda: self.remove_row(edit, cnt, btn_del, row))
        edit.setFocus()
        self.recount()                     # поле с готовым текстом (add_row("PC-")) тоже должно посчитаться

    def remove_row(self, edit, cnt, btn_del, row):
        self.rows_lay.removeItem(row)
        edit.deleteLater()
        cnt.deleteLater()
        btn_del.deleteLater()
        self.recount()

    def _patterns(self) -> list[str]:
        out, seen = [], set()
        for i in range(self.rows_lay.count()):
            lay = self.rows_lay.itemAt(i)
            if lay is None:
                continue
            edit = lay.itemAt(0).widget() if lay.count() else None
            if isinstance(edit, QLineEdit):
                p = park_pattern(edit.text())
                if p and p not in seen:
                    seen.add(p)
                    out.append(p)
        return out

    def recount(self, *_):
        """Живой счётчик: сколько ПК домена подходит под каждое поле и под всё вместе."""
        pats = []
        for i in range(self.rows_lay.count()):
            lay = self.rows_lay.itemAt(i)
            edit = lay.itemAt(0).widget() if lay is not None and lay.count() else None
            cnt = lay.itemAt(1).widget() if lay is not None and lay.count() > 1 else None
            if not isinstance(edit, QLineEdit):
                continue
            p = park_pattern(edit.text())
            pats.append(p)
            if isinstance(cnt, QLabel):
                if not p or not self.computer_names:
                    cnt.setText("")
                else:
                    rx = PCScannerWorker._mask_regex(p)
                    n = sum(1 for name in self.computer_names if rx and rx.match(name))
                    cnt.setText(f"найдено: {n}" if n else "не найдено")
        if self.computer_names:
            total = 0
            for p in pats:
                if p:
                    rx = PCScannerWorker._mask_regex(p)
                    total += sum(1 for name in self.computer_names if rx and rx.match(name))
            uniq = {n for p in pats if p for n in self.computer_names
                    if PCScannerWorker._mask_regex(p) and PCScannerWorker._mask_regex(p).match(n)}
            self.total_lbl.setText(f"В парк попадает ПК: {len(uniq)} из {len(self.computer_names)} в домене."
                                   if uniq else ("Ничего не найдено — проверьте написание." if any(pats) else ""))

    def save(self, *_):
        pats = self._patterns()
        if not pats:
            # 3.12.0: кнопка всегда доступна; пустой ввод — понятная подсказка вместо серой кнопки
            self.total_lbl.setText(tr("Введите серию (например PC- или PC-0000) — или нажмите «Пропустить», "
                                   "чтобы работать со всеми ПК домена."))
            return
        mask = ", ".join(pats)
        settings.host_mask = mask
        settings.save_section("Scanner", {"host_mask": mask})
        log.info("маска парка из окна первого запуска: %s", mask)
        self.mask_saved = True
        self.accept()
