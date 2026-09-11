"""Строит отдельную базу pc_mapping.db на N ПК (по умолчанию 1500) с принтерами — для замера скорости поиска.

    python tests/bench/make_bench_db.py [--pcs 1500] [--out extras/data/bench/pc_mapping.db]

Ничего в коде программы не трогает: это обычный файл SQLite той же схемы, что делает `adk.db.init_db`.
Содержимое (реалистичное, но вымышленное — домен example.local, сети 10.0.x.x):
  * pc_inventory   — N ПК: имя WS-0001…, IP по подсетям 10.0.(1..6).x, ~85 % «в сети», пользователь у ~90 %,
                     характеристики, last_logon;
  * pc_mapping / permanent_mapping — привязка логин → ПК (у ~20 % ПК — постоянная);
  * pc_printers    — ~N×1,3 записей: 40 сетевых моделей (общие на несколько ПК, IP 10.0.9.x), USB-принтеры,
                     виртуальные (PDF/XPS/OneNote/Fax) — они должны отфильтровываться;
  * audit_cache    — «архив»: 2–3 старых ПК на пользователя (режим «Архивы»);
  * pc_software    — по 40–80 программ на ПК (для «У кого установлено» и сравнения);
  * search_history, suggest — 300 прошлых запросов (подсказки);
  * notes, audit_log — немного записей.
Одновременно пишется bench_users.json — те же логины/ФИО, чтобы заглушка AD в бенче отвечала согласованно.
"""
import argparse, json, os, random, sqlite3, sys  # noqa: E401
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

SURNAMES = ("Иванов Петров Сидоров Смирнов Кузнецов Попов Васильев Соколов Михайлов Новиков Фёдоров Морозов Волков Алексеев Лебедев "
            "Семёнов Егоров Павлов Козлов Степанов Николаев Орлов Андреев Макаров Никитин Захаров Зайцев Соловьёв Борисов Яковлев "
            "Григорьев Романов Воробьёв Сергеев Кузьмин Фролов Александров Дмитриев Королёв Гусев Киселёв Ильин Максимов Поляков "
            "Сорокин Виноградов Ковалёв Белов Медведев Антонов Тарасов Жуков Баранов Филиппов Комаров Давыдов Беляев Герасимов "
            "Богданов Осипов Сидорчук Мельник Ткаченко Бондаренко Шевченко Коваленко Кравченко Олейник Шевчук Полищук Ткачук "
            "Савченко Бондарь Марченко Руденко Мороз Лысенко Петренко Клименко").split()
NAMES_M = "Александр Дмитрий Максим Сергей Андрей Алексей Артём Илья Кирилл Михаил Никита Матвей Роман Егор Арсений Иван Денис Евгений Даниил Тимофей Владислав Игорь Владимир Павел Руслан Марк Константин Тимур Олег Ярослав".split()
NAMES_F = "Анастасия Мария Дарья Анна Елизавета Полина Виктория Екатерина Софья Александра Валерия Вероника Арина Алиса Ксения Милана Варвара Кристина Диана Юлия Ольга Татьяна Наталья Ирина Светлана Елена Марина Людмила Галина Надежда".split()
PATR_M = "Александрович Дмитриевич Сергеевич Андреевич Алексеевич Иванович Михайлович Николаевич Петрович Владимирович Олегович Игоревич Юрьевич Павлович Викторович".split()
PATR_F = [p[:-2] + "на" for p in PATR_M]
DEPTS = ("Бухгалтерия", "ИТ-отдел", "Отдел продаж", "Отдел кадров", "Юридический отдел", "Склад", "Производство", "Отдел закупок",
         "Маркетинг", "Служба безопасности", "Приёмная", "Отдел логистики", "Планово-экономический отдел", "Конструкторское бюро")
TITLES = ("Специалист", "Ведущий специалист", "Старший специалист", "Менеджер", "Руководитель группы", "Начальник отдела", "Инженер",
          "Бухгалтер", "Главный бухгалтер", "Кладовщик", "Оператор", "Юрисконсульт", "Секретарь", "Системный администратор", "Экономист")
COMPANIES = ("ООО «Пример»", "ООО «Пример-Сервис»", "АО «Пример-Инвест»")
NET_PRINTERS = [("HP LaserJet Pro M404dn", 12), ("HP LaserJet M428fdw", 8), ("Kyocera ECOSYS P3145dn", 10), ("Kyocera ECOSYS M2540dn", 7),
                ("Canon i-SENSYS LBP226dw", 6), ("Xerox VersaLink B405", 5), ("Brother HL-L6200DW", 6), ("Pantum P3300DN", 9),
                ("Ricoh SP 330DN", 4), ("Epson WorkForce Pro WF-M5299", 3), ("Kyocera TASKalfa 3253ci", 5), ("Canon imageRUNNER 2425", 5),
                ("Xerox WorkCentre 3345", 4), ("HP Color LaserJet Pro M454dn", 6)]
USB_PRINTERS = ["Canon LBP6030", "HP LaserJet 1018", "HP LaserJet P1102", "Samsung ML-2160", "Brother HL-1110R", "Epson L3150",
                "Pantum P2500W", "Xerox Phaser 3020", "Canon MF3010", "Kyocera FS-1040"]
VIRTUAL = [("Microsoft Print to PDF", "PORTPROMPT:"), ("Microsoft XPS Document Writer", "PORTPROMPT:"), ("OneNote (Desktop)", "nul:"),
           ("Fax", "SHRFAX:"), ("Adobe PDF", "Documents\\*.pdf")]
SOFTWARE = ["Microsoft Office Professional Plus 2019", "Google Chrome", "Mozilla Firefox", "7-Zip 23.01", "Adobe Acrobat Reader DC",
            "Kaspersky Endpoint Security", "1C:Предприятие 8.3", "КриптоПро CSP", "Microsoft Teams", "Zoom", "Notepad++", "VLC media player",
            "Microsoft Edge", "Microsoft Visual C++ 2015-2022 Redistributable", "Java 8 Update 381", "Python 3.11", "Git", "PuTTY",
            "WinRAR", "TeamViewer", "RMS Агент", "Консультант Плюс", "Гарант", "СБИС", "Контур.Экстерн", "Яндекс.Браузер", "Telegram Desktop",
            "Skype", "Microsoft OneDrive", "Microsoft .NET Runtime 6.0", "Microsoft .NET Runtime 8.0", "Autodesk AutoCAD 2022",
            "КОМПАС-3D v21", "Adobe Photoshop 2023", "CorelDRAW 2021", "SolidWorks 2022", "Cisco AnyConnect", "OpenVPN", "FortiClient",
            "Dr.Web Security Space", "LibreOffice 7.6", "Foxit Reader", "Paint.NET", "GIMP", "Audacity", "OBS Studio", "HandBrake",
            "Total Commander", "FAR Manager", "WinSCP", "FileZilla", "Wireshark", "Nmap", "Postman", "Visual Studio Code", "PyCharm Community",
            "IntelliJ IDEA", "Docker Desktop", "VirtualBox", "VMware Workstation", "Microsoft SQL Server Management Studio", "DBeaver",
            "pgAdmin 4", "Microsoft Power BI Desktop", "Tableau Reader", "Camtasia", "Snagit", "Greenshot", "ShareX", "Lightshot", "Everything",
            "CCleaner", "Speccy", "CrystalDiskInfo", "HWiNFO", "AIDA64", "MSI Afterburner", "Steam", "Discord", "Spotify", "iTunes"]
CPUS = ["Intel Core i5-10400", "Intel Core i5-12400", "Intel Core i3-10100", "Intel Core i7-11700", "AMD Ryzen 5 5600G", "AMD Ryzen 5 3400G",
        "Intel Core i5-8400", "Intel Pentium Gold G6400", "AMD Ryzen 7 5700G", "Intel Core i5-13400"]


def translit(s: str) -> str:
    t = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l",
         "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh",
         "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    return "".join(t.get(ch, ch) for ch in s.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pcs", type=int, default=1500)
    ap.add_argument("--out", default=os.path.join("extras", "data", "bench", "pc_mapping.db"))
    ap.add_argument("--seed", type=int, default=20260911)
    a = ap.parse_args()
    random.seed(a.seed)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    if os.path.exists(a.out):
        os.remove(a.out)
    for ext in ("-wal", "-shm"):
        if os.path.exists(a.out + ext):
            os.remove(a.out + ext)

    from adk import db as adk_db
    from adk.config import settings
    settings.db_path = a.out
    adk_db.init_db()

    now = datetime(2026, 9, 11, 9, 0, 0)
    users, seen_logins = [], set()
    n_users = int(a.pcs * 0.9)
    for i in range(n_users):
        female = random.random() < 0.45
        sn = random.choice(SURNAMES) + ("а" if female else "")
        nm = random.choice(NAMES_F if female else NAMES_M)
        pt = random.choice(PATR_F if female else PATR_M)
        base = translit(sn)
        login = base
        k = 1
        while login in seen_logins:
            login = f"{base}_{translit(nm)[0]}" if k == 1 else f"{base}_{translit(nm)[0]}{k}"
            k += 1
        seen_logins.add(login)
        users.append({"login": login, "sn": sn, "givenName": nm, "displayName": f"{sn} {nm} {pt}", "department": random.choice(DEPTS),
                      "title": random.choice(TITLES), "company": random.choice(COMPANIES), "mail": f"{login}@example.local",
                      "telephoneNumber": str(random.randint(2550000, 2559999)), "ipPhone": str(random.randint(4000, 4999)),
                      "physicalDeliveryOfficeName": f"каб. {random.randint(101, 520)}", "disabled": random.random() < 0.06})

    pcs = []
    ip_used = set()
    for i in range(1, a.pcs + 1):
        name = f"WS-{i:04d}"
        while True:
            ip = f"10.0.{random.randint(1, 8)}.{random.randint(10, 250)}"
            if ip not in ip_used:
                ip_used.add(ip)
                break
        user = users[i - 1]["login"] if i - 1 < n_users else ""
        online = random.random() < 0.85
        last_logon = (now - timedelta(days=random.randint(0, 40), hours=random.randint(0, 9))).strftime("%Y-%m-%d %H:%M:%S")
        cpu = random.choice(CPUS)
        specs = f"{cpu} · ОЗУ {random.choice((8, 16, 32))} ГБ · SSD {random.choice((240, 256, 480, 512, 1000))} ГБ · Windows {random.choice(('10 Pro', '11 Pro'))}"
        pcs.append((name, ip, online, user, last_logon, specs))

    conn = sqlite3.connect(a.out)
    conn.execute("PRAGMA journal_mode=WAL")
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany("INSERT INTO pc_inventory (computer_name, ip_address, is_online, current_user, last_checked, specs, last_logon, last_seen_online) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     [(n, ip, int(on), u, ts, sp, ll, ts if on else (now - timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d %H:%M:%S"))
                      for n, ip, on, u, ll, sp in pcs])
    conn.executemany("INSERT INTO pc_mapping (login, computer_name) VALUES (?,?)", [(u, n) for n, ip, on, u, ll, sp in pcs if u])
    conn.executemany("INSERT INTO permanent_mapping (login, computer_name, ip_address, last_updated) VALUES (?,?,?,?)",
                     [(u, n, ip, ts) for n, ip, on, u, ll, sp in pcs if u and random.random() < 0.2])
    # архив: старые ПК пользователей
    audit = []
    by_login = {u["login"]: u for u in users}
    for n, ip, on, u, ll, sp in pcs:
        if u and random.random() < 0.6:
            for _ in range(random.randint(1, 3)):
                old = f"WS-{random.randint(1, a.pcs):04d}"
                if old != n:
                    when = (now - timedelta(days=random.randint(60, 900))).strftime("%Y-%m-%d %H:%M:%S")
                    audit.append((u, old, f"10.0.{random.randint(1, 8)}.{random.randint(10, 250)}", by_login[u]["displayName"], when,
                                  "", f"\\\\srv\\audit\\{old}_{u}_{len(audit)}.txt", 0.0))
    conn.executemany("INSERT INTO audit_cache (login, computer_name, ip_address, full_name, timestamp, raw_data, file_path, file_mtime) "
                     "VALUES (?,?,?,?,?,?,?,?)", audit)

    # принтеры: сетевые общие (на группу ПК), USB, виртуальные
    net_pool = []
    for model, count in NET_PRINTERS:
        for k in range(count):
            net_pool.append((model, f"10.0.9.{len(net_pool) + 10}"))
    printers = []
    for n, ip, on, u, ll, sp in pcs:
        rows = []
        for _ in range(random.choice((1, 1, 1, 2))):
            model, pip = random.choice(net_pool)
            rows.append((n, model, f"IP_{pip}", "network", pip, 0))
        if random.random() < 0.18:
            rows.append((n, random.choice(USB_PRINTERS), "USB001", "usb", "", 0))
        if random.random() < 0.10:
            rows.append((n, "HP LaserJet 1020 (общий)", "\\\\PRINTSRV\\HP1020", "shared", "", 0))
        for vn, vp in random.sample(VIRTUAL, k=random.randint(0, 3)):
            rows.append((n, vn, vp, "virtual", "", 0))
        # по умолчанию — первый реальный
        real = [r for r in rows if r[3] != "virtual"]
        if real:
            i0 = rows.index(real[0])
            rows[i0] = rows[i0][:5] + (1,)
        uniq = {}
        for r in rows:
            uniq[(r[0], r[1])] = r
        printers.extend(uniq.values())
    conn.executemany("INSERT OR REPLACE INTO pc_printers (computer_name, name, port, kind, ip_address, is_default, updated) VALUES (?,?,?,?,?,?,?)",
                     [r + (ts,) for r in printers])

    soft = []
    for n, ip, on, u, ll, sp in pcs:
        for name in random.sample(SOFTWARE, k=random.randint(40, min(80, len(SOFTWARE)))):
            soft.append((n, name, f"{random.randint(1, 24)}.{random.randint(0, 9)}.{random.randint(0, 999)}", "", "", ts))
    conn.executemany("INSERT INTO pc_software (computer_name, name, version, publisher, installed, ts) VALUES (?,?,?,?,?,?)", soft)

    hist = [random.choice(users)["sn"] for _ in range(150)] + [p[0] for p in random.sample(pcs, min(100, len(pcs)))] + [p[1] for p in random.sample(pcs, min(50, len(pcs)))]
    conn.executemany("INSERT INTO search_history (query, user_login, timestamp, query_key) VALUES (?,?,?,?)",
                     [(q, "CORP\\admin", (now - timedelta(minutes=i * 7)).strftime("%Y-%m-%d %H:%M:%S"), q.lower()) for i, q in enumerate(hist)])
    terms = {}
    for q in hist + [u["login"] for u in users] + [p[0] for p in pcs]:
        terms[q] = terms.get(q, 0) + 1
    conn.executemany("INSERT OR REPLACE INTO suggest (term, hits, ts) VALUES (?,?,?)", [(t, h, ts) for t, h in terms.items()])
    conn.executemany("INSERT INTO notes (subject, kind, text, admin, ts) VALUES (?,?,?,?,?)",
                     [(random.choice(users)["login"], "user", "Просил заменить мышь", "CORP\\admin", ts) for _ in range(120)])
    conn.executemany("INSERT INTO audit_log (ts, admin, action, target, details) VALUES (?,?,?,?,?)",
                     [(ts, "CORP\\admin", random.choice(("reset_password", "unlock", "disable_user")), random.choice(users)["login"], "") for _ in range(400)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    with open(os.path.join(os.path.dirname(a.out), "bench_users.json"), "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False)
    stat = {}
    c = sqlite3.connect(a.out)
    for t in ("pc_inventory", "pc_mapping", "permanent_mapping", "audit_cache", "pc_printers", "pc_software", "search_history", "suggest", "notes", "audit_log"):
        stat[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    c.close()
    print(f"{a.out}: {os.path.getsize(a.out) / 1e6:.1f} МБ")
    for t, n in stat.items():
        print(f"  {t:18} {n:>7}")
    print(f"  пользователей AD (bench_users.json): {len(users)}")


if __name__ == "__main__":
    main()
