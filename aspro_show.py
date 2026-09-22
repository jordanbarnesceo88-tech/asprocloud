#!/usr/bin/env python3
"""
Aspro.Cloud: показать содержимое справочников аккаунта.

Не считает записи, а выводит их названия — чтобы видеть, что уже настроено
в системе и от чего отталкиваться. Только чтение.

    python3 aspro_show.py              # все справочники
    python3 aspro_show.py этапы        # только совпавшие по названию

Токен берётся из aspro_tokens.json (aspro_oauth.py auth ...).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("ASPRO_URL", "https://d18e.aspro.cloud").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "aspro_tokens.json")
PAUSE = 1.1
TIMEOUT = 30

# (заголовок, модуль, сущность, поля для показа)
TABLES = [
    ("Организации",                  "fin", "organization",     ["name", "name_legal"]),
    ("Расчётные счета",              "fin", "bank_account",     ["name", "account_number"]),
    ("Направления деятельности",     "fin", "business_line",    ["name"]),
    ("Воронки продаж",               "crm", "pipeline",         ["name"]),
    ("Стадии воронки",               "crm", "pipeline_stage",   ["name", "pipeline_id"]),
    ("Причины отказа",               "crm", "loss_reason",      ["name"]),
    ("Источники сделок",             "crm", "source",           ["name"]),
    ("Типы контрагентов",            "crm", "account_category", ["name"]),
    ("Сферы деятельности",           "crm", "industry",         ["name"]),
    ("Доски проектов",               "st",  "project_types",    ["name"]),
    ("Этапы проектов",               "st",  "stages",           ["name", "project_type_id", "project_id"]),
    ("Портфели проектов",            "st",  "portfolio",        ["name"]),
    ("Рабочие процессы задач",       "task", "workflows",       ["name"]),
    ("Этапы рабочих процессов задач", "task", "stages",         ["name", "workflow_id", "task_status"]),
    ("Статьи учёта",                 "fin", "categories",       ["name", "type"]),
    ("Шаблоны КП",                   "fin", "estimate_template", ["name"]),
    ("Печатные формы актов",         "finacts", "templates",    ["name"]),
    ("Статусы актов",                "finacts", "status",       ["name"]),
    ("Прайс-листы",                  "products", "pricelist",   ["name"]),
    ("Единицы измерения",            "products", "units",       ["name"]),
    ("Отделы",                       "orgchart", "department",  ["name"]),
    ("Календари",                    "calendar", "calendar",    ["name"]),
    ("Пользователи",                 "core", "user",            ["name", "username", "role_admin"]),
    ("Пользовательские поля",        "customfields", "fields",  ["title", "type", "alias"]),
]


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала: aspro_oauth.py auth ...".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = json.load(fh).get("access_token")
    if not t:
        sys.exit("В файле токенов нет access_token.")
    return t


def get_list(tok, module, entity, limit=100):
    url = "{}/api/v1/module/{}/{}/list?{}".format(
        API_BASE, module, entity, urllib.parse.urlencode({"limit": limit}))
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer {}".format(tok), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return None, "HTTP {}".format(e.code)
    except Exception as e:
        return None, "{}: {}".format(type(e).__name__, e)

    if isinstance(body, dict) and "error" in body:
        err = body["error"]
        if isinstance(err, dict):
            return None, "{}|{}".format(err.get("error_code"), err.get("error_msg"))
        return None, str(err)
    return (body.get("response") or {}).get("items") or [], None


def show(tok, title, module, entity, fields):
    items, problem = get_list(tok, module, entity)
    print("\n{}  ({}/{})".format(title, module, entity))
    print("-" * 72)
    if problem:
        print("  не прочитать: {}".format(problem))
        return
    if not items:
        print("  пусто — ни одной записи")
        return
    for it in items:
        parts = []
        for f in fields:
            v = it.get(f)
            if v not in (None, "", "0"):
                parts.append("{}={}".format(f, v) if f != fields[0] else str(v))
        print("  id {:<8} {}".format(it.get("id"), "  ".join(parts)))


def main():
    needle = " ".join(sys.argv[1:]).strip().lower()
    tok = token()

    print("Аккаунт: {}".format(API_BASE))
    if needle:
        print("Фильтр : «{}»".format(needle))

    shown = 0
    for title, module, entity, fields in TABLES:
        if needle and needle not in title.lower() and needle not in entity.lower():
            continue
        show(tok, title, module, entity, fields)
        shown += 1
        time.sleep(PAUSE)

    if not shown:
        print("\nНичего не совпало. Доступные разделы:")
        for title, _, _, _ in TABLES:
            print("  -", title)


if __name__ == "__main__":
    main()
