"""Генерация PDF-отчёта о тестировании (docs/TEST_REPORT.pdf). Запуск: python docs/make_report.py

Отчёт короткий и «живой»: версия берётся из adk/__init__.py, все числа — из логов docs/test-logs/
(pyflakes.txt, pytest.txt, e2e_round1..3.txt, stability.txt). Ничего не захардкожено, кроме описаний.
"""
import os
import re
from collections import Counter
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "docs", "test-logs")
OUT = os.path.join(ROOT, "docs", "TEST_REPORT.pdf")

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DejaVu", f"{FONT_DIR}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", f"{FONT_DIR}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Mono", f"{FONT_DIR}/DejaVuSansMono.ttf"))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold")

ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="DejaVu", fontSize=9.5, leading=13.5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11.5, textColor=colors.HexColor("#475569"))
H1 = ParagraphStyle("h1", parent=BODY, fontName="DejaVu-Bold", fontSize=16, leading=21, spaceBefore=8, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=BODY, fontName="DejaVu-Bold", fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4,
                    textColor=colors.HexColor("#1e3a8a"))
TITLE = ParagraphStyle("title", parent=H1, fontSize=24, leading=30, alignment=TA_CENTER, spaceAfter=4)
SUB = ParagraphStyle("sub", parent=BODY, fontSize=11, alignment=TA_CENTER, textColor=colors.HexColor("#475569"))
MONO = ParagraphStyle("mono", parent=BODY, fontName="DejaVu-Mono", fontSize=7.4, leading=9.6,
                      backColor=colors.HexColor("#f1f5f9"), borderPadding=4, leftIndent=2)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.4, leading=11)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="DejaVu-Bold")
BIG = ParagraphStyle("big", parent=BODY, fontName="DejaVu-Bold", fontSize=20, leading=24, alignment=TA_CENTER,
                     textColor=colors.HexColor("#15803d"))
BIGCAP = ParagraphStyle("bigcap", parent=SMALL, alignment=TA_CENTER)

# Что покрывает каждый файл тестов — единственное «ручное» описание в отчёте.
SUITES = [
    ("test_core.py", "Ядро: MD4 (RFC 1320), БД, миграции и защита БД, разбор ping, LDAP-фильтры и paging, нормализация логинов/имён ПК, DPAPI"),
    ("test_gui.py", "GUI-smoke: главное окно и каждый диалог с заглушкой LDAP (FakeConn), горячие клавиши, фоновые потоки без крэшей"),
    ("test_accounts.py", "Учётные записи и поиск: два состояния учётки + причина, полное ФИО, карточка, смена пароля, принтер по IP, дашборд"),
    ("test_ping.py", "Окно «Пинг»: график, лента журнала и фильтры, лимит строк, офлайн-ПК, маркер ожидания, шрифт"),
    ("test_health.py", "«Здоровье ПК»: health/S.M.A.R.T., журнал ошибок, карта диска (treemap), «что можно почистить»"),
    ("test_network.py", "Сеть: DHCP, свободный IP и карта подсети, живой опрос принтеров без записи в БД"),
    ("test_fleet.py", "Парк ПК: «Внимание», WoL, массовый пинг, ПО/входы, сравнение ПК, шаблоны, опись, CLI/сервер, роли, безопасность"),
    ("test_tools.py", "Инструменты: заметки, таймлайн, подсказки, плагины, обновления, экспорт, хоткей/тема/i18n, массовые операции"),
    ("test_ui.py", "Внешний вид: заголовок окна, бейджи, столбцы и их ширина, стили таблиц/чипов/скроллбаров, выбор цвета"),
    ("test_learning.py", "Учебный файл: простые примеры «дано → действие → проверка» для тех, кто начинает писать тесты"),
]

# Ключевые дефекты, которые нашли автотесты за всё время (не аудит кода). Коротко — что было и какой тест держит.
KEY_BUGS = [
    ["Критич.", "Плавающий segfault при закрытии окна раньше фонового потока", "Потоки без Qt-родителя + реестр живых воркеров", "test_dialog_closed_before_background_thread_finishes_does_not_crash"],
    ["Критич.", "Второй поиск подряд падал с RuntimeError (объект удалён)", "Отмена поиска через реестр потоков", "test_second_search_does_not_raise"],
    ["Критич.", "Свободный IP не запускался: self.start затирал QThread.start()", "Переименовано в start_host", "test_free_ip_worker_start_not_shadowed"],
    ["Высокая", "Вход по UPN невозможен: NTLM получал DOMAIN\\user@domain", "qualify_user понимает UPN и голый логин", "test_qualify_user_upn_and_bare"],
    ["Высокая", "Поиск по IP «10.0.2» находил 10.0.20.x и 110.0.2.x", "Совпадение по границе октета", "test_ip_prefix_matching_is_octet_aware"],
    ["Средняя", "Группы с запятой в имени (CN=Smith\\, John) шли не по тому DN", "Разбор по неэкранированной запятой", "test_dn_to_cn_escaped_comma"],
    ["Средняя", "Усечение выдачи (лимит 40) было молчаливым", "Запрос limit+1 и статус «показаны первые N»", "test_search_worker_reports_truncation"],
    ["Средняя", "ОЗУ «8388608 ГБ»: сборщик писал байты, код ждал мегабайты", "Порог байты/МБ", "test_specs_ram_in_bytes_and_printers"],
    ["Средняя", "Бейдж учётки спорил с инспектором (разные слова о состоянии)", "Единая функция account_badge: два состояния + причина", "test_account_badge_has_two_states_only"],
    ["Средняя", "Легенда карты диска наезжала на таблицы под ней", "Высота treemap растёт с легендой, сплиттер", "test_treemap_min_height_grows_with_legend"],
    ["Средняя", "«Почистить» считалось отдельно и расходилось с картой; на D: показывалась «Корзина»", "Выборка из того же обхода, только найденные пути", "test_health_dialog_hogs_tab_follows_map_volume"],
    ["Низкая", "Сводка «Чаще всего за сутки» не совпадала с показанными событиями", "Счётчики считаются по строкам таблицы", "test_health_errors_tab"],
]

NOT_COVERED = [
    "Живой bind по LDAPS/NTLM/Kerberos к реальному контроллеру домена, политика паролей и <i>unicodePwd</i>.",
    "PowerShell-сборщики (CIM/WMI, S.M.A.R.T., журналы, обход дисков) — на Linux ветка честно сообщает «только на Windows».",
    "Настоящие DPAPI, <i>shutdown /m</i>, <i>mmc</i>, RMS-viewer, Exchange-экспорт PST, SMTP-рассылка.",
    "SQLite на SMB-шаре под одновременной работой нескольких администраторов.",
]

CHECKLIST = [
    "Вход NTLM/SSO и по UPN на тестовой OU; создание учётной записи и смена пароля по LDAPS.",
    "Сканер на 5–10 ПК: сверить pc_inventory с фактом, проверить host_pattern в config.ini.",
    "«Запомнить пароль»: cred_cache.json не содержит открытого текста, повторный вход без запроса.",
    "«Здоровье ПК» на включённой и выключенной машине; карта диска и «Почистить» на томе D:; «Диск» на ПК с двумя томами.",
    "Пинг ПК не в сети: красный индикатор, 100 % потерь, маркер ожидания на графике.",
    "Роль readonly-группы: действия с AD скрыты, остальное доступно; массовые операции — на 2–3 тестовых УЗ.",
]

GREEN = colors.HexColor("#15803d")


def P(t, st=BODY):
    return Paragraph(t, st)


def table(rows, widths, font=CELL, header=True):
    data = [[c if not isinstance(c, str) else Paragraph(c, CELLB if (header and r == 0) else font) for c in row]
            for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")))
    for r in range(1, len(rows)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f8fafc")))
    t.setStyle(TableStyle(style))
    return t


def ok_cell(ok_n, n):
    good = ok_n == n and n > 0
    return Paragraph(f'<font color="{"#15803d" if good else "#b91c1c"}"><b>{ok_n}/{n}</b></font>', CELL)


def read(name, default=""):
    try:
        with open(os.path.join(LOGS, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return default


def version():
    m = re.search(r'__version__\s*=\s*"([^"]+)"', open(os.path.join(ROOT, "adk", "__init__.py"), encoding="utf-8").read())
    return m.group(1) if m else "?"


def parse_e2e(text):
    """→ [(section, n_ok, n_total)] — по заголовкам «=== X. … ===» и строкам PASS/FAIL."""
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^=== (.+) ===$", line)
        if m:
            if m.group(1) == "ИТОГ":
                break
            cur = [m.group(1), 0, 0]
            out.append(cur)
            continue
        m = re.match(r"^(PASS|FAIL) ", line)
        if m and cur:
            cur[2] += 1
            cur[1] += m.group(1) == "PASS"
    return out


def parse_pytest(text):
    m = re.search(r"(\d+) passed", text)
    passed = int(m.group(1)) if m else 0
    failed = int(re.search(r"(\d+) failed", text).group(1)) if re.search(r"(\d+) failed", text) else 0
    per_file = Counter(re.findall(r"^tests/(test_\w+\.py)::.+ PASSED", text, re.M))
    if passed and not per_file:
        raise SystemExit("docs/test-logs/pytest.txt записан без -v: нет строк «tests/test_x.py::test_y PASSED», "
                         "таблица по файлам была бы пустой. Перепишите лог: pytest -v tests > docs/test-logs/pytest.txt")
    if per_file and sum(per_file.values()) != passed:
        raise SystemExit(f"pytest.txt: {passed} passed, а по файлам насчитано {sum(per_file.values())} — лог неполный")
    return passed, failed, per_file


def parse_stability(text):
    runs = re.findall(r"^run \d+: (\d+) passed", text, re.M)
    m = re.search(r"crashes: (\d+)/(\d+)", text)
    if m:
        return f"{int(m.group(2)) - int(m.group(1))}/{m.group(2)} прогонов без сбоев"
    return f"{len(runs)}/{len(runs)} прогонов без сбоев" if runs else "—"


def footer_for(ver):
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(20 * mm, 12 * mm, f"ADK {ver} — отчёт о тестировании")
        canvas.drawRightString(190 * mm, 12 * mm, f"стр. {doc.page}")
        canvas.restoreState()
    return footer


def main():
    ver = version()
    pyflakes = read("pyflakes.txt").strip()
    n_flakes = len([l for l in pyflakes.splitlines() if l.strip()])
    passed, failed, per_file = parse_pytest(read("pytest.txt"))
    rounds = [("E2E раунд 1 — сценарий пользователя", "tests/e2e_scenario.py", parse_e2e(read("e2e_round1.txt"))),
              ("E2E раунд 2 — операции и данные", "tests/e2e_round2.py", parse_e2e(read("e2e_round2.txt"))),
              ("E2E раунд 3 — принтеры, поиск, производительность", "tests/e2e_round3.py", parse_e2e(read("e2e_round3.txt")))]
    stab = parse_stability(read("stability.txt"))
    e2e_ok = sum(ok for _, _, secs in rounds for _, ok, _ in secs)
    e2e_n = sum(n for _, _, secs in rounds for _, _, n in secs)

    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=f"ADK {ver} — отчёт о тестировании", author="ADK QA")
    F = []

    # ---------------------------------------------------------------- 1. титул + итог на одной странице
    F += [Spacer(1, 18 * mm), P(f"ADK {ver} — Active Directory Kit", TITLE), P("Отчёт о тестировании", TITLE),
          P(f"{date.today():%d.%m.%Y} · сборка портфолио · песочница Linux, Qt offscreen", SUB), Spacer(1, 10 * mm)]
    totals = Table([[P(str(passed), BIG), P(f"{e2e_ok}/{e2e_n}", BIG), P(str(n_flakes), BIG), P(stab.split()[0], BIG)],
                    [P("автотестов pytest", BIGCAP), P("сквозных E2E-проверок", BIGCAP), P("замечаний pyflakes", BIGCAP),
                     P("стабильность GUI-набора", BIGCAP)]], colWidths=[43 * mm] * 4)
    totals.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
                                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                                ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, 1), (-1, 1), 10)]))
    F += [totals, Spacer(1, 8 * mm)]
    F += [table([
        ["Уровень", "Что проверяется", "Результат"],
        ["Статический анализ", "py_compile + pyflakes по adk/ и tests/",
         Paragraph(f'<font color="{"#15803d" if n_flakes == 0 else "#b91c1c"}"><b>{n_flakes} замечаний</b></font>', CELL)],
        ["pytest (модульные + GUI)", f"{len(per_file)} файлов, сгруппированы по областям (ядро, учётки, пинг, здоровье, сеть, парк, инструменты, UI)", ok_cell(passed, passed + failed)],
        *[[title, script, ok_cell(sum(ok for _, ok, _ in secs), sum(n for _, _, n in secs))] for title, script, secs in rounds],
        ["Стабильность", "Весь набор pytest несколько раз подряд (ловит плавающие крэши Qt-потоков)", stab],
    ], [40 * mm, 100 * mm, 34 * mm])]
    F += [Spacer(1, 6), P("Как читать: все цифры выше берутся из логов <font face='DejaVu-Mono'>docs/test-logs/</font> при генерации "
                          "отчёта; версия — из <font face='DejaVu-Mono'>adk/__init__.py</font>. Раздел 5 — команды для повторения.", SMALL)]

    # ---------------------------------------------------------------- 2. методика (коротко)
    F += [PageBreak(), P("1. Как устроено тестирование", H1)]
    F += [P("Реального контроллера домена, Windows и PowerShell в песочнице нет, поэтому <b>подменяются только внешние границы</b> — "
            "LDAP-соединение, сеть, pywin32 и subprocess. Весь код приложения (Qt-окна, потоки, SQLite, разбор данных, формирование "
            "команд) выполняется по-настоящему.", BODY)]
    F += [table([
        ["Граница", "Чем подменена", "Что это позволяет проверить"],
        ["ldap3.Connection", "FakeConn / RichConn — ведут журнал search/add/modify/delete, эмулируют paging-cookie",
         "Точные LDAP-фильтры, порядок операций при создании УЗ и откат, экранирование, обход лимита 1000 записей"],
        ["Сеть", "Фиксированный IP/статус; для окна пинга — поддельный PingWorker с заданной последовательностью ответов",
         "Сортировка «онлайн первыми», инспектор, разбор вывода ping, поведение при таймаутах"],
        ["subprocess / pywin32", "Перехват argv без запуска; обратимая заглушка DPAPI",
         "Аргументы shutdown/mmc/RMS формируются корректно; пароль не лежит в файле открытым текстом"],
        ["Подтверждения", "MessageBox всегда отвечает «Да»", "Сценарии не блокируются; проверяется, что действие реально выполнилось"],
    ], [30 * mm, 70 * mm, 74 * mm])]
    F += [P("Чего эта методика не покрывает", H2)]
    F += [Paragraph(f"• {t}", ParagraphStyle("b", parent=BODY, leftIndent=10, firstLineIndent=-8)) for t in NOT_COVERED]

    # ---------------------------------------------------------------- 3. набор pytest по файлам
    F += [P("2. Набор pytest по файлам", H1)]
    rows = [["Файл", "Что покрывает", "Тестов"]]
    for name, desc in SUITES:
        if per_file.get(name):
            rows.append([f"<font face='DejaVu-Mono'>{name}</font>", desc, str(per_file[name])])
    for name, cnt in sorted(per_file.items()):
        if name not in dict(SUITES):
            rows.append([f"<font face='DejaVu-Mono'>{name}</font>", "—", str(cnt)])
    rows.append(["<b>Итого</b>", "", f"<b>{passed}</b>"])
    F += [table(rows, [34 * mm, 122 * mm, 18 * mm])]
    if failed:
        F += [Spacer(1, 4), P(f'<font color="#b91c1c"><b>Внимание: {failed} тест(ов) упало — см. docs/test-logs/pytest.txt.</b></font>', BODY)]

    # ---------------------------------------------------------------- 4. E2E по разделам
    F += [PageBreak(), P("3. Сквозные сценарии (E2E)", H1)]
    F += [P("Скрипты запускают приложение целиком и работают как пользователь: набирают запрос по буквам, ждут фоновые потоки, "
            "кликают по строкам и кнопкам, затем проверяют, что реально ушло в LDAP, в subprocess и в БД.", BODY)]
    for title, script, secs in rounds:
        n_ok, n = sum(ok for _, ok, _ in secs), sum(x for _, _, x in secs)
        rows = [["Раздел", "Пройдено"]] + [[name, ok_cell(ok, x)] for name, ok, x in secs]
        F += [KeepTogether([P(f"{title} — {n_ok}/{n} · <font face='DejaVu-Mono'>{script}</font>", H2), table(rows, [140 * mm, 34 * mm])])]

    # ---------------------------------------------------------------- 5. ключевые найденные дефекты
    F += [PageBreak(), P("4. Что нашли автотесты (ключевое)", H1)]
    F += [P("Дефекты ниже обнаружены проверками из этого отчёта, а не чтением кода; каждый закрыт регрессионным тестом. "
            "Полная история изменений — в CHANGELOG.md.", BODY)]
    F += [table([["Уровень", "Симптом", "Исправление", "Тест-страж"]] +
                [[lvl, sym, fix, f"<font face='DejaVu-Mono' size='7'>{test.replace('_', '_ ')}</font>"] for lvl, sym, fix, test in KEY_BUGS],
                [18 * mm, 62 * mm, 48 * mm, 46 * mm], font=ParagraphStyle("c8", parent=CELL, fontSize=7.8, leading=10))]

    # ---------------------------------------------------------------- 6. воспроизведение + чек-лист
    F += [P("5. Как воспроизвести", H1)]
    F += [Preformatted(
        "pip install -r requirements-dev.txt\n"
        f"pyflakes adk tests                                        # {n_flakes} замечаний\n"
        f"QT_QPA_PLATFORM=offscreen pytest -v                       # {passed} passed\n"
        + "".join(f"QT_QPA_PLATFORM=offscreen python {script:<28}# {sum(ok for _, ok, _ in secs)}/{sum(n for _, _, n in secs)}\n"
                  for _, script, secs in rounds)
        + "for i in $(seq 5); do pytest -q || break; done            # стабильность\n"
        "python docs/make_report.py                                # этот PDF\n", MONO)]
    F += [P("Логи прогонов лежат в <font face='DejaVu-Mono'>docs/test-logs/</font>; CI (<font face='DejaVu-Mono'>.github/workflows/tests.yml</font>) "
            "гоняет то же самое на Python 3.11/3.12.", SMALL)]
    F += [P("6. Чек-лист перед боевым запуском", H1)]
    F += [Paragraph(f"☐ {t}", ParagraphStyle("c", parent=BODY, leftIndent=12, firstLineIndent=-10, spaceAfter=2)) for t in CHECKLIST]

    doc.build(F, onFirstPage=footer_for(ver), onLaterPages=footer_for(ver))
    print("written", OUT)


if __name__ == "__main__":
    main()
