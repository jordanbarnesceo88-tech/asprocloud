#!/usr/bin/env python3
"""
Разведка аккаунта Aspro.Cloud. Только чтение — ничего не создаёт и не меняет.

Отвечает на вопросы дня 1:
  * какие модули реально отвечают на текущем тарифе;
  * что уже заведено в аккаунте (объёмы по каждой сущности);
  * есть ли объект договора;
  * какие пользовательские поля уже созданы.

Запуск:
    export ASPRO_URL='https://d18e.aspro.cloud'
    export ASPRO_API_KEY='...'
    python3 aspro_recon.py

Результат: отчёт в консоль + aspro_recon.json рядом со скриптом.

На бесплатном тарифе лимит — 1 запрос в секунду, поэтому скрипт намеренно
медленный (пауза 1.1 с между запросами) и сам отступает при 429.
Полный проход занимает около минуты.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAUSE = 1.1          # пауза между запросами, с (тариф фри = 1 rps)
TIMEOUT = 30
MAX_RETRY_429 = 3

# (модуль, сущность, человекочитаемое имя)
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
    ("fin", "commitment", "Финансовые обязательства"),

    ("finacts", "acts", "Акты"),
    ("finacts", "templates", "Печатные формы актов"),
    ("finacts", "status", "Статусы актов"),
    ("finacts", "advance_invoices", "Авансовые счета-фактуры"),

    ("timetracker", "timesheets", "Time sheets"),
    ("timetracker", "timelogs", "Тайм-логи"),
    ("timetracker", "billing_entities_list", "Ставки пользователей"),

    ("products", "product", "Товары"),
    ("products", "category", "Категории товаров"),
    ("products", "units", "Единицы измерения"),
    ("products", "pricelist", "Прайс-листы"),

    ("agile", "projects", "Agile-проекты"),
    ("agile", "issues", "Agile-задачи"),

    ("customfields", "fields", "Пользовательские поля"),
    ("customfields", "fieldsets", "Наборы полей"),

    ("knowledgebase", "knowledgebases", "Базы знаний"),
    ("company", "absences", "Отсутствия"),
    ("orgchart", "department", "Отделы"),
    ("salary", "employee", "Сотрудники (зарплата)"),
    ("businessprocess", "process", "Бизнес-процессы"),
    ("businessprocess", "instance", "Экземпляры процессов"),
    ("calendar", "calendar", "Календари"),
    ("im", "thread", "Чаты"),
    ("telephony", "telephony", "Телефонии"),
    ("asset", "asset", "Имущество"),
    ("apar", "apar", "Обязательства"),
    ("loans", "loan", "Кредиты"),
]

# Поиск объекта договора: этих путей нет в спецификации API,
# ответ 404 ожидаем и сам по себе информативен.
CONTRACT_HUNT = [
    ("fin", "contract"),
    ("crm", "contract"),
    ("st", "contract"),
    ("contracts", "contract"),
    ("fin", "contracts"),
    ("crm", "agreement"),
]


def mask(key):
    return key[:4] + "…" + key[-7:] if len(key) > 12 else "…"


def call(base, key, module, entity, method="list", **params):
    """Один GET-запрос. Возвращает (http_код, распарсенный_json_или_текст, ошибка)."""
    params.setdefault("limit", 1)
    params["api_key"] = key
    url = "{}/api/v1/module/{}/{}/{}?{}".format(
        base.rstrip("/"), module, entity, method, urllib.parse.urlencode(params)
    )
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
        except Exception as e:                      # сеть, DNS, TLS
            return 0, None, "{}: {}".format(type(e).__name__, e)
    return 0, None, "429 не отступил"


def classify(code, body, err):
    """Превращает ответ в короткий вердикт."""
    if err:
        return "СЕТЬ", err
    if code == 200 and isinstance(body, dict):
        if "response" in body:
            resp = body["response"]
            if isinstance(resp, dict) and "total" in resp:
                return "ОК", int(resp.get("total") or 0)
            return "ОК", "—"
        if "error" in body:
            e = body["error"]
            msg = e.get("error_msg") if isinstance(e, dict) else e
            return "ОТКАЗ", msg
    if code in (401, 403):
        return "НЕТ ДОСТУПА", code
    if code == 404:
        return "НЕТ ТАКОГО", code
    if code == 429:
        return "ЛИМИТ", code
    return "HTTP {}".format(code), str(body)[:120]


def main():
    base = os.environ.get("ASPRO_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    key = os.environ.get("ASPRO_API_KEY") or (sys.argv[2] if len(sys.argv) > 2 else "")
    if not base or not key:
        sys.exit("Задайте ASPRO_URL и ASPRO_API_KEY (переменными окружения или аргументами).")

    print("Аккаунт : {}".format(base))
    print("Ключ    : {}".format(mask(key)))
    print("Пауза   : {} с между запросами (лимит бесплатного тарифа — 1 rps)".format(PAUSE))
    print("Запросов: {}\n".format(len(PROBES) + len(CONTRACT_HUNT) + 1))

    report = {"account": base, "probes": [], "contract_hunt": [], "custom_fields": []}

    # --- 1. Кто мы ---
    code, body, err = call(base, key, "core", "user", method="get")
    verdict, detail = classify(code, body, err)
    if verdict == "ОК" and isinstance(body, dict):
        u = body.get("response", {})
        who = "{} (id {}, admin={})".format(
            u.get("name") or u.get("username"), u.get("id"), u.get("role_admin")
        )
        print("Ключ действует от имени: {}\n".format(who))
        report["identity"] = u
    else:
        print("!! Не удалось определить владельца ключа: {} {}\n".format(verdict, detail))
        report["identity_error"] = {"verdict": verdict, "detail": str(detail)}
        if verdict == "СЕТЬ":
            print("Сеть не пускает до аккаунта — дальше идти смысла нет.")
            sys.exit(1)
    time.sleep(PAUSE)

    # --- 2. Модули и объёмы ---
    print("{:<14} {:<24} {:<28} {:<14} {}".format(
        "МОДУЛЬ", "СУЩНОСТЬ", "ЧТО ЭТО", "СТАТУС", "ЗАПИСЕЙ / ПРИЧИНА"))
    print("-" * 104)
    for module, entity, title in PROBES:
        code, body, err = call(base, key, module, entity)
        verdict, detail = classify(code, body, err)
        print("{:<14} {:<24} {:<28} {:<14} {}".format(
            module, entity, title[:27], verdict, detail))
        report["probes"].append({
            "module": module, "entity": entity, "title": title,
            "http": code, "verdict": verdict, "detail": str(detail),
        })
        time.sleep(PAUSE)

    # --- 3. Охота на договор ---
    print("\nПоиск объекта договора (в спецификации API его нет):")
    for module, entity in CONTRACT_HUNT:
        code, body, err = call(base, key, module, entity)
        verdict, detail = classify(code, body, err)
        flag = "  <-- НАЙДЕНО" if verdict == "ОК" else ""
        print("  {}/{:<12} {:<14} {}{}".format(module, entity, verdict, detail, flag))
        report["contract_hunt"].append({
            "module": module, "entity": entity,
            "http": code, "verdict": verdict, "detail": str(detail),
        })
        time.sleep(PAUSE)

    # --- 4. Уже созданные пользовательские поля ---
    print("\nПользовательские поля, уже заведённые в аккаунте:")
    code, body, err = call(base, key, "customfields", "fields", limit=100)
    if code == 200 and isinstance(body, dict) and "response" in body:
        items = body["response"].get("items") or []
        if not items:
            print("  (нет ни одного)")
        for f in items:
            print("  cf_{:<8} {:<12} {:<22} {}".format(
                f.get("id"), f.get("type", ""), (f.get("alias") or "—"),
                (f.get("title") or f.get("name") or "")))
        report["custom_fields"] = items
    else:
        print("  не прочитать: {} {}".format(*classify(code, body, err)))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aspro_recon.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\nПолный отчёт сохранён: {}".format(out))
    print("Пришлите его — по нему соберу скрипты настройки под реальный состав аккаунта.")


if __name__ == "__main__":
    main()
