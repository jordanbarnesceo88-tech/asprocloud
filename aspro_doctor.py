#!/usr/bin/env python3
"""
Aspro.Cloud: диагностика ключей + разведка аккаунта за один запуск.

Фаза 1 — проверяет переданные ключи и добавляет контрольные образцы,
         чтобы понять, что означает ответ сервера.
Фаза 2 — если хоть один ключ рабочий, снимает полную картину аккаунта:
         какие модули живы, что уже заведено, есть ли объект договора,
         какие пользовательские поля созданы.

Ничего не создаёт и не меняет — только чтение.

Запуск:
    python3 aspro_doctor.py КЛЮЧ1 [КЛЮЧ2 ...]

Адрес аккаунта берётся из ASPRO_URL, по умолчанию https://d18e.aspro.cloud

Результат: отчёт в консоль + aspro_report.json рядом со скриптом.
Тариф Фри держит 1 запрос в секунду, поэтому скрипт намеренно медленный.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("ASPRO_URL", "https://d18e.aspro.cloud").rstrip("/")
PAUSE = 1.1
TIMEOUT = 30
MAX_RETRY_429 = 3

# Контрольные ключи. Заведомо несуществующие — нужны, чтобы понять,
# отличает ли сервер "ключа нет" от "ключ есть, но не активирован".
CONTROLS = [
    ("A" * 32 + "_172733", "верный формат, выдуманное тело, ваш аккаунт"),
    ("A" * 32 + "_999999", "верный формат, выдуманное тело, чужой аккаунт"),
    ("notakey", "заведомый мусор"),
]

PROBES = [
    ("core", "user", "Пользователи"),
    ("crm", "account", "Контрагенты"),
    ("crm", "lead", "Сделки"),
    ("crm", "pipeline", "Воронки"),
    ("crm", "pipeline_stage", "Стадии воронки"),
    ("crm", "loss_reason", "Причины отказа"),
    ("crm", "source", "Источники сделок"),
    ("crm", "account_category", "Типы контрагентов"),
    ("st", "projects", "Проекты"),
    ("st", "project_types", "Доски проектов"),
    ("st", "stages", "Этапы проектов"),
    ("st", "stage_checkitems", "Чек-листы этапов"),
    ("st", "portfolio", "Портфели"),
    ("st", "project_money_stage", "Плановая выручка"),
    ("st", "project_expense", "Плановые затраты"),
    ("task", "tasks", "Задачи"),
    ("task", "workflows", "Рабочие процессы задач"),
    ("task", "stages", "Этапы рабочих процессов"),
    ("task", "lists", "Списки задач"),
    ("fin", "organization", "Организации"),
    ("fin", "bank_account", "Счета организации"),
    ("fin", "business_line", "Направления деятельности"),
    ("fin", "categories", "Статьи учёта"),
    ("fin", "invoice", "Счета"),
    ("fin", "estimate", "Предложения (КП)"),
    ("fin", "estimate_template", "Шаблоны КП"),
    ("fin", "recurring_invoice", "Регулярные счета"),
    ("fin", "plan_money", "Плановые деньги"),
    ("fin", "transaction", "Транзакции"),
    ("finacts", "acts", "Акты"),
    ("finacts", "templates", "Печатные формы актов"),
    ("finacts", "status", "Статусы актов"),
    ("timetracker", "timesheets", "Time sheets"),
    ("timetracker", "timelogs", "Тайм-логи"),
    ("products", "product", "Товары"),
    ("products", "units", "Единицы измерения"),
    ("products", "pricelist", "Прайс-листы"),
    ("agile", "projects", "Agile-проекты"),
    ("agile", "issues", "Agile-задачи"),
    ("customfields", "fields", "Пользовательские поля"),
    ("knowledgebase", "knowledgebases", "Базы знаний"),
    ("company", "absences", "Отсутствия"),
    ("orgchart", "department", "Отделы"),
    ("salary", "employee", "Сотрудники (зарплата)"),
    ("businessprocess", "process", "Бизнес-процессы"),
    ("calendar", "calendar", "Календари"),
    ("im", "thread", "Чаты"),
    ("telephony", "telephony", "Телефонии"),
]

CONTRACT_HUNT = [
    ("fin", "contract"), ("crm", "contract"), ("st", "contract"),
    ("contracts", "contract"), ("fin", "contracts"), ("crm", "agreement"),
]


def mask(k):
    return k[:6] + "…" + k[-7:] if len(k) > 16 else k


def call(key, module, entity, method="list", **params):
    params.setdefault("limit", 1)
    params["api_key"] = key
    url = "{}/api/v1/module/{}/{}/{}?{}".format(
        BASE, module, entity, method, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(MAX_RETRY_429 + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read().decode("utf-8", "replace")
                try:
                    return r.status, json.loads(raw), None
                except json.JSONDecodeError:
                    return r.status, raw[:300], "не JSON"
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if e.code == 429 and attempt < MAX_RETRY_429:
                time.sleep(2 ** attempt * 2)
                continue
            try:
                return e.code, json.loads(raw), None
            except json.JSONDecodeError:
                return e.code, raw[:300], None
        except Exception as e:
            return 0, None, "{}: {}".format(type(e).__name__, e)
    return 0, None, "429 не отступил"


def err_text(body):
    """Достаёт текст ошибки из тела ответа, если он там есть."""
    if isinstance(body, dict) and "error" in body:
        e = body["error"]
        if isinstance(e, dict):
            return "{}|{}".format(e.get("error_code"), e.get("error_msg"))
        return str(e)
    return None


def classify(code, body, err):
    if err:
        return "СЕТЬ", err
    msg = err_text(body)
    if msg:
        return "ОТКАЗ", msg
    if code == 200 and isinstance(body, dict) and "response" in body:
        resp = body["response"]
        if isinstance(resp, dict) and "total" in resp:
            return "ОК", int(resp.get("total") or 0)
        return "ОК", "—"
    if code in (401, 403):
        return "НЕТ ДОСТУПА", code
    if code == 404:
        return "НЕТ ТАКОГО", code
    if code == 429:
        return "ЛИМИТ", code
    return "HTTP {}".format(code), str(body)[:120]


def phase1(keys, report):
    print("ФАЗА 1. Проверка ключей на {}\n".format(BASE))
    print("{:<26} {:<46} {}".format("КЛЮЧ", "ОТВЕТ СЕРВЕРА", "ПОМЕТКА"))
    print("-" * 104)

    working = []
    real_msgs, ctrl_msgs = set(), set()

    for key in keys:
        code, body, err = call(key, "core", "user", method="get")
        verdict, detail = classify(code, body, err)
        print("{:<26} {:<46} {}".format(mask(key), "{} {}".format(verdict, detail)[:45], "ваш ключ"))
        report["keys"].append({"key": mask(key), "verdict": verdict, "detail": str(detail)})
        if verdict == "ОК":
            working.append(key)
        else:
            real_msgs.add(str(detail))
        if err:
            print("\nСеть не пускает до аккаунта. Запустите скрипт там, где есть доступ.")
            return None
        time.sleep(PAUSE)

    for key, note in CONTROLS:
        code, body, err = call(key, "core", "user", method="get")
        verdict, detail = classify(code, body, err)
        print("{:<26} {:<46} {}".format(mask(key), "{} {}".format(verdict, detail)[:45], note))
        report["controls"].append({"key": mask(key), "note": note,
                                   "verdict": verdict, "detail": str(detail)})
        ctrl_msgs.add(str(detail))
        time.sleep(PAUSE)

    print("\nВЫВОД ФАЗЫ 1")
    if working:
        print("  Рабочий ключ найден. Перехожу к разведке аккаунта.")
    elif real_msgs and real_msgs == ctrl_msgs:
        print("  Ваши ключи отвечают ровно так же, как выдуманные.")
        print("  Сервер их просто не находит: для него они не существуют.")
        print("  Значит дело не в правах ключа, а в том, что ключи не попадают")
        print("  в базу — уровень аккаунта или тарифа. Следующий шаг — поддержка.")
    elif real_msgs:
        print("  Ваши ключи отвечают ИНАЧЕ, чем выдуманные:")
        print("    ваши      : {}".format(" / ".join(sorted(real_msgs))))
        print("    контрольные: {}".format(" / ".join(sorted(ctrl_msgs))))
        print("  Значит ключи в системе есть, но не активированы — смотрите")
        print("  права на модули в карточке ключа и ограничения по тарифу.")
    report["phase1_working"] = [mask(k) for k in working]
    return working


def phase2(key, report):
    print("\n\nФАЗА 2. Разведка аккаунта ключом {}\n".format(mask(key)))

    code, body, err = call(key, "core", "user", method="get")
    if code == 200 and isinstance(body, dict) and "response" in body:
        u = body["response"]
        print("Ключ действует от имени: {} (id {}, admin={})\n".format(
            u.get("name") or u.get("username"), u.get("id"), u.get("role_admin")))
        report["identity"] = u
    time.sleep(PAUSE)

    print("{:<14} {:<22} {:<26} {:<12} {}".format(
        "МОДУЛЬ", "СУЩНОСТЬ", "ЧТО ЭТО", "СТАТУС", "ЗАПИСЕЙ / ПРИЧИНА"))
    print("-" * 100)
    for module, entity, title in PROBES:
        code, body, err = call(key, module, entity)
        verdict, detail = classify(code, body, err)
        print("{:<14} {:<22} {:<26} {:<12} {}".format(
            module, entity, title[:25], verdict, detail))
        report["probes"].append({"module": module, "entity": entity, "title": title,
                                 "http": code, "verdict": verdict, "detail": str(detail)})
        time.sleep(PAUSE)

    print("\nПоиск объекта договора (в спецификации API его нет):")
    for module, entity in CONTRACT_HUNT:
        code, body, err = call(key, module, entity)
        verdict, detail = classify(code, body, err)
        print("  {}/{:<12} {:<12} {}{}".format(
            module, entity, verdict, detail, "   <-- НАЙДЕНО" if verdict == "ОК" else ""))
        report["contract_hunt"].append({"module": module, "entity": entity,
                                        "verdict": verdict, "detail": str(detail)})
        time.sleep(PAUSE)

    print("\nПользовательские поля, уже заведённые в аккаунте:")
    code, body, err = call(key, "customfields", "fields", limit=100)
    if code == 200 and isinstance(body, dict) and "response" in body:
        items = body["response"].get("items") or []
        if not items:
            print("  (нет ни одного)")
        for f in items:
            print("  cf_{:<8} {:<12} {:<20} {}".format(
                f.get("id"), f.get("type", ""), f.get("alias") or "—",
                f.get("title") or f.get("name") or ""))
        report["custom_fields"] = items
    else:
        print("  не прочитать: {} {}".format(*classify(code, body, err)))


def main():
    keys = [k.strip() for k in sys.argv[1:] if k.strip()]
    if not keys:
        sys.exit("Передайте ключи аргументами: python3 aspro_doctor.py КЛЮЧ1 [КЛЮЧ2 ...]")

    report = {"account": BASE, "keys": [], "controls": [],
              "probes": [], "contract_hunt": [], "custom_fields": []}

    total = len(keys) + len(CONTROLS)
    print("Аккаунт : {}".format(BASE))
    print("Ключей  : {} + {} контрольных".format(len(keys), len(CONTROLS)))
    print("Пауза   : {} с между запросами (лимит тарифа — 1 rps)".format(PAUSE))
    print("Время   : ~{} с на фазу 1, ещё ~{} с на фазу 2\n".format(
        int(total * PAUSE) + 1, int((len(PROBES) + len(CONTRACT_HUNT) + 2) * PAUSE)))

    working = phase1(keys, report)
    if working:
        phase2(working[0], report)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aspro_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\nОтчёт сохранён: {}".format(out))
    print("Пришлите его целиком — по нему соберу настройку.")


if __name__ == "__main__":
    main()
