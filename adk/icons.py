"""Контурные интерфейсные иконки (3.4.0).

Раньше иконки в интерфейсе были эмодзи — они зависят от шрифта системы и выглядят разношёрстно.
Теперь это контурные SVG-пиктограммы: штрих 1.75 px, скруглённые концы и углы, единая сетка 24×24,
цвет — из текущей темы (текст или акцент), так что иконки одинаково выглядят во всех десяти темах.

Главная иконка приложения (assets/icon.ico, logo.png) не меняется.

Как это работает: тексты по всему проекту по-прежнему начинаются с эмодзи («🔍 Найти») — это удобный
и читаемый маркер смысла. Функция :func:`install` подменяет конструкторы Qt-виджетов так, что ведущий
эмодзи снимается с текста и превращается в настоящую QIcon; для QLabel с rich-text — во встроенный
``<img>`` с SVG. Таблица соответствия — :data:`EMOJI_ICON`.
"""
from __future__ import annotations

import base64
import re

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap

# ---------------------------------------------------------------- контуры (viewBox 0 0 24 24, только stroke)
_S = 'fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"'

PATHS: dict[str, str] = {
    "magnifyingglass": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21"/>',
    "person.crop.circle": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3.2"/><path d="M6.2 18.4c1.4-2.3 3.4-3.4 5.8-3.4s4.4 1.1 5.8 3.4"/>',
    "person.2": '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19c.6-3.3 2.6-5 5.5-5s4.9 1.7 5.5 5"/><path d="M15.5 5.6a3 3 0 0 1 0 5.8"/><path d="M17.2 14.3c2.3.4 3.6 1.9 4 4.7"/>',
    "person.badge.plus": '<circle cx="10" cy="8.5" r="3.2"/><path d="M4 19c.6-3.3 2.7-5 6-5 1 0 1.9.2 2.7.5"/><path d="M18 14v6M15 17h6"/>',
    "key": '<circle cx="8" cy="14" r="4.2"/><path d="M11.2 11.2 20 2.5M16.5 6l2.5 2.5M14 8.5l2 2"/>',
    "shield": '<path d="M12 3.2 5 6v5.2c0 4.3 2.9 7.6 7 9.6 4.1-2 7-5.3 7-9.6V6z"/>',
    "lock": '<rect x="5.5" y="10.5" width="13" height="10" rx="2.2"/><path d="M8.5 10.5V7.8a3.5 3.5 0 0 1 7 0v2.7"/>',
    "lock.open": '<rect x="5.5" y="10.5" width="13" height="10" rx="2.2"/><path d="M8.5 10.5V7.8a3.5 3.5 0 0 1 6.8-1"/>',
    "desktopcomputer": '<rect x="3" y="4.5" width="18" height="12" rx="2"/><path d="M9 20h6M12 16.5V20"/>',
    "server.rack": '<rect x="4" y="4" width="16" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="16" height="6.5" rx="1.5"/><path d="M7.5 7.2h.01M7.5 16.8h.01M11 7.2h5M11 16.8h5"/>',
    "waveform.path.ecg": '<path d="M3 12h3.2l2.1-5 3.4 10 2.6-7.5 1.7 2.5H21"/>',
    "wifi": '<path d="M2.5 9.2a14 14 0 0 1 19 0M5.7 12.6a9.4 9.4 0 0 1 12.6 0M9 16a4.6 4.6 0 0 1 6 0"/><circle cx="12" cy="19.2" r=".9" fill="currentColor"/>',
    "network": '<circle cx="12" cy="5" r="2.2"/><circle cx="5" cy="19" r="2.2"/><circle cx="19" cy="19" r="2.2"/><path d="M12 7.2v4.3M12 11.5l-5.6 5.4M12 11.5l5.6 5.4"/>',
    "cpu": '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/><rect x="10" y="10" width="4" height="4" rx="1"/><path d="M9 3v3.5M12 3v3.5M15 3v3.5M9 17.5V21M12 17.5V21M15 17.5V21M3 9h3.5M3 12h3.5M3 15h3.5M17.5 9H21M17.5 12H21M17.5 15H21"/>',
    "internaldrive": '<rect x="3" y="7" width="18" height="10" rx="2.5"/><circle cx="17" cy="12" r="1.1" fill="currentColor"/><path d="M6.5 12h5"/>',
    "terminal": '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/><path d="m7 9 3.2 3L7 15M12.5 15H17"/>',
    "power": '<path d="M12 3.5v8.5"/><path d="M7.2 6.6a7.5 7.5 0 1 0 9.6 0"/>',
    "arrow.clockwise": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.8 3.8v4.6h-4.6"/>',
    "printer": '<path d="M6 9V4h12v5"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v6H7z"/><line x1="6" y1="12.5" x2="8" y2="12.5"/><circle cx="17.5" cy="12.5" r="1" fill="currentColor"/>',
    "puzzlepiece": '<path d="M14 4a2 2 0 0 1 2 2v1h2a2 2 0 0 1 2 2v2a2 2 0 0 1 0 4v2a2 2 0 0 1-2 2h-2v1a2 2 0 1 1-4 0v-1h-2a2 2 0 0 1-2-2v-2a2 2 0 1 1 0-4V9a2 2 0 0 1 2-2h2V6a2 2 0 0 1 2-2z"/>',
    "archivebox": '<rect x="3.5" y="4" width="17" height="4.5" rx="1.2"/><path d="M5 8.5v9.3A2.2 2.2 0 0 0 7.2 20h9.6a2.2 2.2 0 0 0 2.2-2.2V8.5M10 12.5h4"/>',
    "slider.horizontal.3": '<path d="M4 7h16M4 12h16M4 17h16"/><circle cx="9" cy="7" r="2" fill="var(--bg)"/><circle cx="15" cy="12" r="2" fill="var(--bg)"/><circle cx="8" cy="17" r="2" fill="var(--bg)"/>',
    "doc.on.clipboard": '<rect x="7" y="6" width="12" height="14" rx="2"/><path d="M5 16V5.8A1.8 1.8 0 0 1 6.8 4H15"/>',
    "square.and.arrow.up": '<path d="M12 15V4M8.5 7.5 12 4l3.5 3.5"/><path d="M6 11v7.2A1.8 1.8 0 0 0 7.8 20h8.4a1.8 1.8 0 0 0 1.8-1.8V11"/>',
    "square.and.arrow.down": '<path d="M12 4v11M8.5 11.5 12 15l3.5-3.5"/><path d="M6 12v6.2A1.8 1.8 0 0 0 7.8 20h8.4a1.8 1.8 0 0 0 1.8-1.8V12"/>',
    "paintpalette": '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-.9 2-1.8 0-.9-.7-1.3-.7-2.2 0-1 .8-1.5 1.8-1.5h1.8a3.6 3.6 0 0 0 3.6-3.6C20.5 6.9 16.7 3.5 12 3.5z"/><circle cx="7.5" cy="11" r="1.1" fill="currentColor"/><circle cx="10" cy="7.3" r="1.1" fill="currentColor"/><circle cx="14.5" cy="7.3" r="1.1" fill="currentColor"/>',
    "checkmark.circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.7 2.7L16.2 9.5"/>',
    "checkmark": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "xmark.circle": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    "xmark": '<path d="M6 6l12 12M18 6 6 18"/>',
    "exclamationmark.triangle": '<path d="M12 4.2 2.8 19.5h18.4z"/><path d="M12 9.5v4.5M12 17h.01"/>',
    "exclamationmark.circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5h.01"/>',
    "info.circle": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8h.01"/>',
    "questionmark.circle": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.1 1-1.1 1.8M12 17h.01"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus.circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "minus.circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "nosign": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    "gearshape": '<circle cx="12" cy="12" r="3"/><path d="M12 2.8 13.9 5l2.9-.7.8 2.9 2.9.8-.7 2.9 2.2 1.9-2.2 1.9.7 2.9-2.9.8-.8 2.9-2.9-.7L12 21.2 10.1 19l-2.9.7-.8-2.9-2.9-.8.7-2.9L2 12l2.2-1.9-.7-2.9 2.9-.8.8-2.9 2.9.7z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
    "clock.arrow.circlepath": '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.2 3.8v4.6h4.6"/><path d="M12 8.5V12l2.5 1.6"/>',
    "hourglass": '<path d="M7 3.5h10M7 20.5h10M8 3.5v3.3c0 1.5.7 2.6 1.9 3.5L12 12l-2.1 1.7C8.7 14.6 8 15.7 8 17.2v3.3M16 3.5v3.3c0 1.5-.7 2.6-1.9 3.5L12 12l2.1 1.7c1.2.9 1.9 2 1.9 3.5v3.3"/>',
    "bolt": '<path d="M13 3 5.5 13.5H12L11 21l7.5-10.5H12z"/>',
    "bell": '<path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "chart.bar": '<path d="M4 20h16M7 16.5v-5M12 16.5V7M17 16.5v-8"/>',
    "chart.line.downtrend": '<path d="M3.5 7.5l5 5 3.5-3.5 8 8"/><path d="M20 13v4h-4"/>',
    "doc.text": '<path d="M7 3.5h7l4.5 4.5v12.5h-11.5z"/><path d="M14 3.5V8h4.5M9.5 12.5h5M9.5 16h5"/>',
    "list.bullet.rectangle": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7.5 9.5h.01M7.5 14.5h.01M10.5 9.5h6M10.5 14.5h6"/>',
    "folder": '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "folder.open": '<path d="M3.5 8a2 2 0 0 1 2-2h3.8l2 2H17a2 2 0 0 1 2 2v1"/><path d="M3.5 11h16.7l-1.7 7H5.2z"/>',
    "tray.full": '<path d="M4 13.5V17a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3.5"/><path d="M4 13.5h4l1.5 2.5h5l1.5-2.5h4"/><path d="M8 6h8M7 9.5h10"/>',
    "shippingbox": '<path d="M12 3.5 4 7.3v9.4l8 3.8 8-3.8V7.3z"/><path d="M4 7.3 12 11l8-3.7M12 11v9.5"/>',
    "map": '<path d="M3.5 6.5 9 4.5l6 2 5.5-2v13l-5.5 2-6-2-5.5 2z"/><path d="M9 4.5v13M15 6.5v13"/>',
    "square.grid.3x3": '<path d="M4 4h16v16H4zM9.3 4v16M14.7 4v16M4 9.3h16M4 14.7h16"/>',
    "square.split.2x1": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M12 5v14"/>',
    "arrow.triangle.branch": '<circle cx="6" cy="18" r="2.2"/><circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="8" r="2.2"/><path d="M6 8.2v7.6M17.8 10.2c-.4 3.2-3 4.3-7.5 4.6"/>',
    "arrow.left.arrow.right": '<path d="M4 8.5h14M14.5 5 18 8.5 14.5 12M20 15.5H6M9.5 12 6 15.5 9.5 19"/>',
    "arrow.right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow.up.right.square": '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M9.5 14.5 15 9M10.5 9H15v4.5"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/>',
    "cable.connector": '<path d="M8 4v4M16 4v4"/><path d="M5.5 8h13v3a6.5 6.5 0 0 1-13 0z"/><path d="M12 17.5V21"/>',
    "envelope": '<rect x="3.5" y="6" width="17" height="12" rx="2"/><path d="m4.5 7.5 7.5 5.5 7.5-5.5"/>',
    "megaphone": '<path d="M4 10v4a1.5 1.5 0 0 0 1.5 1.5H8l7 4V4.5l-7 4H5.5A1.5 1.5 0 0 0 4 10z"/><path d="M18.5 9.5a3.5 3.5 0 0 1 0 5"/>',
    "phone": '<path d="M6.5 3.5h3l1.5 4-2 1.5a10 10 0 0 0 6 6l1.5-2 4 1.5v3a2 2 0 0 1-2.2 2C10.5 19 5 13.5 4.5 5.7a2 2 0 0 1 2-2.2z"/>',
    "mappin": '<path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.2"/>',
    "building.2": '<path d="M3.5 20.5h17M5 20.5V5.5h8v15M13 9.5h6v11"/><path d="M8 8.5h2M8 12h2M8 15.5h2M15.5 13h1.5M15.5 16.5h1.5"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "star": '<path d="m12 3.8 2.5 5.3 5.7.7-4.2 4 1.1 5.7L12 16.7l-5.1 2.8 1.1-5.7-4.2-4 5.7-.7z"/>',
    "eye": '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    "trash": '<path d="M5 6.5h14M9.5 6.5V4.5h5v2M7 6.5l.8 12.2a1.6 1.6 0 0 0 1.6 1.5h5.2a1.6 1.6 0 0 0 1.6-1.5L17 6.5"/>',
    "pencil.and.list.clipboard": '<rect x="5" y="4.5" width="11" height="15" rx="2"/><path d="M8.5 9h4M8.5 12.5h3"/><path d="m19.5 9.5-6 6-.5 2.5 2.5-.5 6-6z"/>',
    "square.and.pencil": '<path d="M11 5.5H6.5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V13"/><path d="m18.5 3.5 2 2-8 8-3 1 1-3z"/>',
    "stethoscope": '<path d="M6 4v5a4 4 0 0 0 8 0V4"/><path d="M10 13v2.5a4.5 4.5 0 0 0 9 0V13"/><circle cx="19" cy="10.5" r="2"/>',
    "cross.case": '<rect x="3.5" y="7" width="17" height="12.5" rx="2"/><path d="M9 7V4.5h6V7M12 10.5v5M9.5 13h5"/>',
    "wrench.and.screwdriver": '<path d="m4 20 6.5-6.5"/><path d="M14.5 4.5a4 4 0 0 0-4.6 5.1l-6 6 2.5 2.5 6-6a4 4 0 0 0 5.1-4.6l-2.3 2.3-2.2-.5-.5-2.2z"/>',
    "thermometer": '<path d="M9.5 14.5V5a2.5 2.5 0 0 1 5 0v9.5a4 4 0 1 1-5 0z"/><path d="M12 10v6"/>',
    "brain": '<path d="M9.5 4.5a3 3 0 0 0-3 3 3 3 0 0 0-2 4.5 3 3 0 0 0 1.5 5 3 3 0 0 0 4.5 2V4.5zM14.5 4.5a3 3 0 0 1 3 3 3 3 0 0 1 2 4.5 3 3 0 0 1-1.5 5 3 3 0 0 1-4.5 2V4.5z"/>',
    "play": '<path d="M7 4.8v14.4L19 12z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "moon.zzz": '<path d="M13 3.5a8.5 8.5 0 1 0 7.5 12.6A8.5 8.5 0 0 1 13 3.5z"/><path d="M16 4h4l-4 4h4"/>',
    "rectangle.portrait.and.arrow.right": '<path d="M13 5.5h-6a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h6"/><path d="M11 12h9.5M17 8.5l3.5 3.5-3.5 3.5"/>',
    "door.left.hand.open": '<path d="M13 3.5 5 5.5v13l8 2z"/><path d="M13 5.5h5.5v13H13M10.5 12h.01"/>',
    "macwindow": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9h17M7 6.8h.01M9.5 6.8h.01"/>',
    "dice": '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="8.5" cy="8.5" r="1.1" fill="currentColor"/><circle cx="15.5" cy="8.5" r="1.1" fill="currentColor"/><circle cx="12" cy="12" r="1.1" fill="currentColor"/><circle cx="8.5" cy="15.5" r="1.1" fill="currentColor"/><circle cx="15.5" cy="15.5" r="1.1" fill="currentColor"/>',
    "sparkles": '<path d="M12 4.5 13.8 10l5.5 1.8-5.5 1.8L12 19.2l-1.8-5.6L4.7 11.8 10.2 10z"/><path d="M19 3.5v3M17.5 5h3"/>',
    "textformat": '<path d="M5 18.5 10 6h1l5 12.5M7 14h7M18 10h3M19.5 8.5v3"/>',
    "flask": '<path d="M10 3.5h4M10.5 3.5v6L5.5 18a1.8 1.8 0 0 0 1.6 2.5h9.8a1.8 1.8 0 0 0 1.6-2.5l-5-8.5v-6"/><path d="M8 15h8"/>',
    "bandage": '<rect x="2.5" y="8.5" width="19" height="7" rx="3.5" transform="rotate(-45 12 12)"/><path d="M11 11h.01M13 13h.01M11 13h.01M13 11h.01"/>',
    "circle.fill": '<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>',
    "circle": '<circle cx="12" cy="12" r="6"/>',
    "square": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "chevron.down": '<path d="m6 9.5 6 6 6-6"/>',
    "chevron.up": '<path d="m6 14.5 6-6 6 6"/>',
    "arrow.up.circle": '<circle cx="12" cy="12" r="9"/><path d="M12 16.5v-9M8.5 11 12 7.5l3.5 3.5"/>',
    "doc.badge.plus": '<path d="M7 3.5h7l4.5 4.5v5"/><path d="M14 3.5V8h4.5"/><path d="M7 3.5v17h6.5"/><path d="M18 15.5v5M15.5 18h5"/>',
    "tablecells": '<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M3.5 9.7h17M3.5 14.3h17M9.2 5v14M14.8 5v14"/>',
    "text.badge.checkmark": '<path d="M4 7h10M4 12h7M4 17h6"/><path d="m13 16 2.5 2.5L20.5 13"/>',
    "checklist": '<path d="m4 7 1.5 1.5L8 6M4 13l1.5 1.5L8 12M4 19l1.5 1.5L8 18M11 7.5h9M11 13.5h9M11 19.5h9"/>',
    "person.crop.rectangle": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="12" cy="10.5" r="2.3"/><path d="M7.5 17.5c.9-2 2.4-3 4.5-3s3.6 1 4.5 3"/>',
    "personalhotspot": '<circle cx="12" cy="12" r="2.2"/><path d="M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 19a10 10 0 0 1 0-14M19 5a10 10 0 0 1 0 14"/>',
    "face.smiling": '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c1 1.3 2.1 2 3.5 2s2.5-.7 3.5-2M9.3 9.8h.01M14.7 9.8h.01"/>',
    "hand.wave": '<path d="M8 12.5V6.8a1.5 1.5 0 0 1 3 0V11M11 10.5V5.5a1.5 1.5 0 0 1 3 0v5.5M14 11V6.8a1.5 1.5 0 0 1 3 0v6.7a6 6 0 0 1-9.5 4.9L4.7 15a1.5 1.5 0 0 1 2.1-2.1L8 14v-1.5"/>',
    "ellipsis.circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>',
    "square.fill.on.square": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M5 16V6a2 2 0 0 1 2-2h10"/>',
    "keyboard": '<rect x="3" y="6.5" width="18" height="11" rx="2"/><path d="M7 10h.01M10.3 10h.01M13.7 10h.01M17 10h.01M7 14h.01M10 14h4M17 14h.01"/>',
}

# ---------------------------------------------------------------- эмодзи → иконка (+ роль цвета: text/accent/success/danger/warning/info)
EMOJI_ICON: dict[str, tuple[str, str]] = {
    "🔍": ("magnifyingglass", "text"), "🔎": ("magnifyingglass", "text"),
    "👤": ("person.crop.circle", "text"), "👥": ("person.2", "text"), "➕": ("plus.circle", "success"),
    "➖": ("minus.circle", "danger"), "🔑": ("key", "warning"), "🔐": ("lock", "text"), "🔒": ("lock", "warning"),
    "🔓": ("lock.open", "success"), "🛡": ("shield", "text"),
    "💻": ("desktopcomputer", "text"), "🖥": ("desktopcomputer", "info"), "🗄": ("server.rack", "text"),
    "📡": ("waveform.path.ecg", "info"), "🌐": ("wifi", "info"), "🔗": ("link", "text"), "🔌": ("cable.connector", "text"),
    "🧠": ("cpu", "text"), "💽": ("internaldrive", "text"), "💾": ("internaldrive", "text"), "🧰": ("wrench.and.screwdriver", "text"),
    "⚙": ("gearshape", "text"), "⏻": ("power", "danger"), "🔄": ("arrow.clockwise", "text"), "↺": ("arrow.clockwise", "text"),
    "🖨": ("printer", "text"), "🗂": ("archivebox", "text"), "📦": ("shippingbox", "text"), "📁": ("folder", "text"),
    "📂": ("folder.open", "text"), "📋": ("doc.on.clipboard", "text"), "📝": ("square.and.pencil", "text"),
    "🧩": ("puzzlepiece", "text"),
    "📤": ("square.and.arrow.up", "text"), "📥": ("square.and.arrow.down", "text"), "📊": ("chart.bar", "text"),
    "📉": ("chart.line.downtrend", "text"), "📜": ("list.bullet.rectangle", "text"), "📄": ("doc.text", "text"),
    "📑": ("tablecells", "text"), "🎨": ("paintpalette", "text"),
    "✅": ("checkmark.circle", "success"), "✔": ("checkmark", "success"), "✓": ("checkmark", "success"),
    "❌": ("xmark.circle", "danger"), "✖": ("xmark", "danger"), "✕": ("xmark", "text"), "⛔": ("nosign", "danger"),
    "🚷": ("nosign", "danger"), "⚠": ("exclamationmark.triangle", "warning"), "🚨": ("exclamationmark.circle", "danger"),
    "ℹ": ("info.circle", "info"), "❓": ("questionmark.circle", "text"),
    "⏳": ("hourglass", "text"), "🕓": ("clock", "text"), "🕒": ("clock", "text"), "⏱": ("clock", "text"),
    "🕘": ("clock.arrow.circlepath", "text"), "⚡": ("bolt", "warning"), "🔔": ("bell", "warning"),
    "🩺": ("stethoscope", "success"), "🩻": ("cross.case", "text"), "🩹": ("bandage", "warning"), "🌡": ("thermometer", "danger"),
    "🧹": ("trash", "text"), "🗑": ("trash", "danger"), "🗺": ("map", "text"), "🧬": ("arrow.triangle.branch", "text"),
    "↔": ("arrow.left.arrow.right", "text"), "➡": ("arrow.right", "text"), "▶": ("play", "success"), "⏸": ("pause", "text"),
    "⏹": ("stop", "danger"), "💤": ("moon.zzz", "info"), "🚪": ("rectangle.portrait.and.arrow.right", "text"),
    "🪟": ("macwindow", "text"), "🎲": ("dice", "text"), "✨": ("sparkles", "warning"), "🔤": ("textformat", "text"),
    "🧪": ("flask", "text"), "📣": ("megaphone", "text"), "📞": ("phone", "text"), "📍": ("mappin", "danger"),
    "🏢": ("building.2", "text"), "📅": ("calendar", "text"), "★": ("star", "warning"), "👁": ("eye", "text"),
    "⬆": ("arrow.up.circle", "info"), "↑": ("arrow.up.circle", "info"), "⚖": ("square.split.2x1", "text"),
    "😴": ("moon.zzz", "info"), "👋": ("hand.wave", "warning"), "🟢": ("circle.fill", "success"),
    "🔴": ("circle.fill", "danger"), "🟡": ("circle.fill", "warning"), "🔵": ("circle.fill", "info"),
    "⚪": ("circle", "text"), "🟥": ("square", "danger"), "▾": ("chevron.down", "text"),
}

_LEAD = re.compile("^(?:<b>)?\\s*([\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u23F0-\u23FF\u2139\u2190-\u21FF\u25BE\u2605\u2611\u2714\u2713\u2716\u2715])\ufe0f?\\s*")


def find_leading(text: str) -> tuple[str, str] | None:
    """(имя иконки, текст без эмодзи) или None, если текст не начинается с известного эмодзи."""
    if not text:
        return None
    m = _LEAD.match(text)
    if not m:
        return None
    key = m.group(1)
    if key not in EMOJI_ICON:
        return None
    rest = text[m.end():]
    if text.startswith("<b>") and not rest.startswith("<b>"):
        rest = "<b>" + rest
    return EMOJI_ICON[key][0], rest


def role_color(role: str) -> str:
    """Цвет иконки по роли из текущей темы."""
    from .widgets import app_palette
    pal = app_palette()
    if role == "accent":
        return pal.title_accent
    if role in ("success", "danger", "warning", "info"):
        return getattr(pal, role)[0]        # цвет текста семантики: читаем и в тёмной, и в светлой теме
    return pal.text


def button_icon_color(btn, role: str) -> str:
    """Контрастный цвет иконки для кнопки.
    Для заливных кнопок (btnPrimary, btnSuccess, btnDanger, btnWarning, btnInfo, btnClose)
    иконка рисуется контрастным цветом текста (#ffffff / on_accent), чтобы не сливаться
    с цветным фоном кнопки. Для обычных кнопок — цвет берётся по роли из палитры."""
    from .widgets import app_palette
    pal = app_palette()
    obj = btn.objectName() if hasattr(btn, "objectName") and callable(btn.objectName) else ""
    if obj == "btnPrimary":
        return pal.on_accent
    if obj in ("btnSuccess", "btnDanger", "btnWarning", "btnInfo", "btnClose"):
        return "#ffffff"
    if obj == "chipBtn" and getattr(btn, "isChecked", lambda: False)():
        return pal.on_accent
    return role_color(role)


def svg(name: str, color: str, size: int = 24) -> bytes:
    body = PATHS.get(name) or PATHS["circle"]
    body = body.replace("var(--bg)", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'{_S} color="{color}">{body}</svg>').encode()


ICON_PX = 22        # размер иконки в кнопках/вкладках/меню
LABEL_PX = 19       # размер иконки в подписях (<img> в rich-text QLabel)

_cache: dict[tuple, QIcon] = {}


def icon(name: str, color: str | None = None, role: str = "text") -> QIcon:
    """QIcon с контурной пиктограммой в цвете темы (кэшируется по имени и цвету)."""
    color = color or role_color(role)
    key = (name, color)
    if key in _cache:
        return _cache[key]
    from PyQt6.QtSvg import QSvgRenderer
    ic = QIcon()
    for px in (16, 18, 20, 24, 32, 48):
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        r = QSvgRenderer(QByteArray(svg(name, color, px)))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p)
        p.end()
        ic.addPixmap(pm)
    _cache[key] = ic
    return ic


def pixmap(name: str, size: int = ICON_PX, color: str | None = None, role: str = "text") -> QPixmap:
    return icon(name, color, role).pixmap(QSize(size, size))


def img_html(name: str, size: int = LABEL_PX, color: str | None = None, role: str = "text") -> str:
    """<img> с SVG для rich-text QLabel."""
    b64 = base64.b64encode(svg(name, color or role_color(role), size)).decode()
    return f'<img src="data:image/svg+xml;base64,{b64}" width="{size}" height="{size}">'


def emoji_role(text: str) -> str:
    m = _LEAD.match(text or "")
    return EMOJI_ICON.get(m.group(1), ("", "text"))[1] if m else "text"


def clear_cache() -> None:
    _cache.clear()


def strip(text: str) -> str:
    """Текст без ведущего эмодзи (для сравнений в тестах и логах)."""
    f = find_leading(text)
    return f[1] if f else text


# ---------------------------------------------------------------- установка: подмена конструкторов Qt
_installed = False


def install() -> None:
    """Включить автоподстановку иконок вместо ведущих эмодзи в QPushButton/QCheckBox/QLabel/QAction/вкладках/
    элементах списков и таблиц. Вызывается один раз при старте приложения (и в тестах через conftest)."""
    global _installed
    if _installed:
        return
    _installed = True
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import (QAbstractButton, QCheckBox, QLabel, QListWidgetItem, QPushButton, QRadioButton,
                                 QTableWidgetItem, QTabWidget, QToolButton)

    def _apply_button(btn) -> None:
        try:
            f = find_leading(btn.text())
        except RuntimeError:
            return
        if not f:
            return
        name, rest = f
        role = emoji_role(btn.text())
        btn._adk_icon = (name, role)
        QAbstractButton.setText(btn, rest.strip())
        c = button_icon_color(btn, role)
        btn.setIcon(icon(name, color=c, role=role))
        btn.setIconSize(QSize(ICON_PX, ICON_PX))
        if not rest.strip():
            btn.setIconSize(QSize(ICON_PX + 2, ICON_PX + 2))

    for cls in (QPushButton, QCheckBox, QRadioButton, QToolButton):
        _orig_init = cls.__init__
        _orig_set = cls.setText
        _orig_obj = cls.setObjectName

        def _init(self, *a, __o=_orig_init, **k):
            __o(self, *a, **k)
            _apply_button(self)

        def _set(self, text, __o=_orig_set):
            __o(self, text)
            _apply_button(self)

        def _set_obj(self, name, __o=_orig_obj):
            __o(self, name)
            meta = getattr(self, "_adk_icon", None)
            if meta:
                c = button_icon_color(self, meta[1])
                self.setIcon(icon(meta[0], color=c, role=meta[1]))
                self.setIconSize(QSize(ICON_PX, ICON_PX))

        cls.__init__ = _init
        cls.setText = _set
        cls.setObjectName = _set_obj

    def _apply_label(lbl) -> None:
        t = lbl.text()
        f = find_leading(t)
        if not f:
            return
        name, rest = f
        role = emoji_role(t)
        rich = lbl.textFormat() == Qt.TextFormat.RichText or "<" in t
        html = f"{img_html(name, LABEL_PX, role=role)}&nbsp;{rest}" if rich else f"{img_html(name, LABEL_PX, role=role)}&nbsp;{_esc(rest)}"
        lbl._adk_icon = (name, role, rest if rich else _esc(rest))
        QLabel.setText(lbl, html)
        lbl.setTextFormat(Qt.TextFormat.RichText)

    _lbl_init, _lbl_set = QLabel.__init__, QLabel.setText

    def _label_init(self, *a, **k):
        _lbl_init(self, *a, **k)
        _apply_label(self)

    def _label_set(self, text):
        self._adk_icon = None           # новый текст без эмодзи — иконки больше нет
        _lbl_set(self, text)
        _apply_label(self)

    QLabel.__init__, QLabel.setText = _label_init, _label_set

    _act_init, _act_set = QAction.__init__, QAction.setText

    def _apply_action(act, text) -> None:
        f = find_leading(text)
        if f:
            name, rest = f
            act._adk_icon = (name, emoji_role(text))
            _act_set(act, rest.strip())
            act.setIcon(icon(name, role=emoji_role(text)))

    def _action_init(self, *a, **k):
        _act_init(self, *a, **k)
        _apply_action(self, self.text())

    def _action_set(self, text):
        _act_set(self, text)
        _apply_action(self, text)

    QAction.__init__, QAction.setText = _action_init, _action_set

    _tab_add, _tab_ins = QTabWidget.addTab, QTabWidget.insertTab

    def _tab_args(args):
        args = list(args)
        for i, x in enumerate(args):
            if isinstance(x, str):
                f = find_leading(x)
                if f:
                    name, rest = f
                    role = emoji_role(x)
                    args[i] = rest.strip()
                    if not any(isinstance(y, QIcon) for y in args):
                        args.insert(i, icon(name, role=role))
                break
        return args

    def _tab_meta(self, args):
        for x in args:
            if isinstance(x, str):
                f = find_leading(x)
                if f:
                    metas = getattr(self, "_adk_tab_icons", None)
                    if metas is None:
                        metas = self._adk_tab_icons = {}
                    metas[f[1].strip()] = (f[0], emoji_role(x))
                break

    def _add_tab(self, *a):
        _tab_meta(self, a)
        return _tab_add(self, *_tab_args(a))

    def _insert_tab(self, *a):
        _tab_meta(self, a)
        return _tab_ins(self, *_tab_args(a))

    QTabWidget.addTab = _add_tab
    QTabWidget.insertTab = _insert_tab
    _tab_set = QTabWidget.setTabText

    def _set_tab_text(self, idx, text):
        f = find_leading(text)
        if f:
            name, rest = f
            self.setTabIcon(idx, icon(name, role=emoji_role(text)))
            text = rest.strip()
        _tab_set(self, idx, text)

    QTabWidget.setTabText = _set_tab_text

    for cls in (QListWidgetItem, QTableWidgetItem):
        _i_init, _i_set = cls.__init__, cls.setText

        def _item_apply(item, text, __set=_i_set):
            f = find_leading(text)
            if f:
                name, rest = f
                item.setIcon(icon(name, role=emoji_role(text)))
                __set(item, rest.strip())

        def _item_init(self, *a, __o=_i_init, __ap=_item_apply, **k):
            __o(self, *a, **k)
            t = self.text()
            if t:
                __ap(self, t)

        def _item_set(self, text, __o=_i_set, __ap=_item_apply):
            __o(self, text)
            __ap(self, text)

        cls.__init__, cls.setText = _item_init, _item_set


def _esc(s: str) -> str:
    import html
    return html.escape(s)


def refresh(root) -> None:
    """После смены темы перекрасить иконки у всех кнопок, вкладок, действий меню и подписей под новым корнем."""
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QAbstractButton, QLabel, QTabWidget
    clear_cache()
    for b in root.findChildren(QAbstractButton):
        meta = getattr(b, "_adk_icon", None)
        if meta:
            c = button_icon_color(b, meta[1])
            b.setIcon(icon(meta[0], color=c, role=meta[1]))
            b.setIconSize(QSize(ICON_PX, ICON_PX))
    for a in root.findChildren(QAction):
        meta = getattr(a, "_adk_icon", None)
        if meta:
            a.setIcon(icon(meta[0], role=meta[1]))
    for t in root.findChildren(QTabWidget):
        metas = getattr(t, "_adk_tab_icons", None) or {}
        for i in range(t.count()):
            meta = metas.get(t.tabText(i))
            if meta:
                t.setTabIcon(i, icon(meta[0], role=meta[1]))
    for lbl in root.findChildren(QLabel):
        meta = getattr(lbl, "_adk_icon", None)
        if meta:
            QLabel.setText(lbl, f"{img_html(meta[0], LABEL_PX, role=meta[1])}&nbsp;{meta[2]}")
