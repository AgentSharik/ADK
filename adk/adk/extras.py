"""Настройки почтовых уведомлений и текст «карточки» с паролем для сотрудника.

Чистая функция :func:`password_card_text` — без Qt, покрыта тестами; используется окном «Смена пароля».
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
)

from . import db, notify
from .config import settings
from .widgets import FramelessDialog, run_in_background

log = logging.getLogger(__name__)


# ============================================================================ карточка с паролем
def password_card_text(login: str, password: str, must_change: bool, domain: str = "") -> str:
    """Текст «карточки» для сотрудника (в буфер обмена)."""
    who = f"{domain}\\{login}" if domain else login
    lines = [f"Логин: {who}", f"Пароль: {password}"]
    if must_change:
        lines.append("При первом входе система попросит сменить пароль.")
    return "\n".join(lines)


# ============================================================================ уведомления
class NotifySettingsDialog(FramelessDialog):
    """[Notify] из config.ini: почта (SMTP), список действий, кнопка «Проверить»."""

    def __init__(self, app, parent=None):
        super().__init__("📣 Почтовые уведомления о событиях журнала", parent, (600, 440))
        self.app = app
        n = settings.notify
        mg = QGroupBox("Почта (SMTP)")
        mf = QFormLayout(mg)
        self.smtp_host = QLineEdit(n["smtp_host"])
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(n["smtp_port"]))
        self.smtp_from = QLineEdit(n["smtp_from"])
        self.smtp_to = QLineEdit(n["smtp_to"])
        self.smtp_user = QLineEdit(n["smtp_user"])
        self.smtp_pass = QLineEdit(n["smtp_password"])
        self.smtp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.smtp_tls = QCheckBox("STARTTLS")
        self.smtp_tls.setChecked(bool(n["smtp_tls"]))
        mf.addRow("Сервер:", self.smtp_host)
        mf.addRow("Порт:", self.smtp_port)
        mf.addRow("От кого:", self.smtp_from)
        mf.addRow("Кому:", self.smtp_to)
        mf.addRow("Логин (если нужен):", self.smtp_user)
        mf.addRow("Пароль:", self.smtp_pass)
        mf.addRow(self.smtp_tls)
        self.body.addWidget(mg)
        wf = QFormLayout()
        self.actions = QLineEdit(",".join(n["actions"]))
        self.actions.setPlaceholderText("reset_password,disable_user,… или * — все")
        self.watch = QLineEdit(",".join(n["watch_logins"]))
        self.watch.setPlaceholderText("логины, о которых сообщать всегда (через запятую)")
        wf.addRow("Какие действия:", self.actions)
        wf.addRow("Следить за учётками:", self.watch)
        self.body.addLayout(wf)
        self.lbl = QLabel("")
        self.body.addWidget(self.lbl)
        btns = QHBoxLayout()
        test = QPushButton("🧪 Сохранить и проверить")
        test.clicked.connect(self.test)
        btns.addWidget(test)
        btns.addStretch()
        save = QPushButton("💾 Сохранить")
        save.setObjectName("btnPrimary")
        save.clicked.connect(lambda: (self.save(), self.accept()))
        btns.addWidget(save)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self.body.addLayout(btns)

    def values(self) -> dict:
        return {"smtp_host": self.smtp_host.text().strip(), "smtp_port": self.smtp_port.value(),
                "smtp_from": self.smtp_from.text().strip(), "smtp_to": self.smtp_to.text().strip(),
                "smtp_user": self.smtp_user.text().strip(), "smtp_password": self.smtp_pass.text(),
                "smtp_tls": "true" if self.smtp_tls.isChecked() else "false",
                "actions": self.actions.text().strip(), "watch_logins": self.watch.text().strip()}

    def save(self):
        settings.save_section("Notify", self.values())
        settings.reload()

    def test(self):
        self.save()
        self.lbl.setText("⏳ Отправляю тестовые сообщения…")

        def done(res):
            if not res:
                self.lbl.setText("Почта не настроена (заполните сервер и адрес «Кому»).")
                return
            self.lbl.setText(" · ".join(f"{'✅' if ok else '❌'} {name}" for name, ok in res))
            db.log_action(self.app.admin_name, "notify_test", "*", ", ".join(f"{n}:{'ok' if ok else 'fail'}" for n, ok in res))

        run_in_background(self, notify.test_channels, done, lambda m: self.lbl.setText(f"⚠️ {m}"))
