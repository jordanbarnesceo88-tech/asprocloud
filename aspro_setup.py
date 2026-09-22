#!/usr/bin/env python3
"""
Aspro.Cloud: сборка структурного скелета аккаунта поверх OAuth-токена.

По умолчанию НИЧЕГО НЕ ПИШЕТ — показывает, что будет создано.
Чтобы применить, добавьте --apply.

    python3 aspro_setup.py            # сухой прогон, только показать
    python3 aspro_setup.py --apply    # создать

Идемпотентен: перед созданием сверяется со списком по названию,
существующие записи пропускает. Повторный запуск дублей не наплодит.

Токен берётся из aspro_tokens.json (режим auth в aspro_oauth.py).
Если access-токен протух, обновите его: aspro_oauth.py refresh ...
"""

import argparse
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

# ─────────────────────────────────────────────────────────────────────────
#  ЧТО СОЗДАЁМ. Правьте здесь — скрипт трогать не нужно.
# ─────────────────────────────────────────────────────────────────────────

# Направления деятельности. Взяты из брифинга по настройке.
# Экономическая безопасность выведена из периметра решением заказчика.
BUSINESS_LINES = [
    "ЧОП",
    "Экспертная приёмка",
    "Оператор связи",
    "ИТ-проекты",
]

# Доска проектов и её этапы.
# ВНИМАНИЕ: это предложение, а не ваши согласованные этапы.
# Проверьте названия перед запуском с --apply.
PROJECT_BOARD = {
    "name": "Основная доска",
    "description": "Единая доска проектов на период пилота",
    "stages": [
        "Инициация",
        "В работе",
        "Сдача заказчику",
        "Закрыт",
    ],
}

# ─────────────────────────────────────────────────────────────────────────


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала: aspro_oauth.py auth ...".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = json.load(fh).get("access_token")
    if not t:
        sys.exit("В файле токенов нет access_token.")
    return t


def request(tok, module, entity, method, data=None, **params):
    url = "{}/api/v1/module/{}/{}/{}".format(API_BASE, module, entity, method)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer {}".format(tok), "Accept": "application/json"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"network_error": "{}: {}".format(type(e).__name__, e)}


def fail(body):
    """Возвращает текст ошибки, если ответ её содержит."""
    if isinstance(body, dict) and "error" in body:
        e = body["error"]
        if isinstance(e, dict):
            return "{}|{}".format(e.get("error_code"), e.get("error_msg"))
        return "{} {}".format(e, body.get("details") or body.get("description") or "")
    if isinstance(body, dict) and "network_error" in body:
        return body["network_error"]
    return None


def existing(tok, module, entity, **params):
    """Возвращает {название: id} уже заведённых записей."""
    code, body = request(tok, module, entity, "list", limit=100, **params)
    time.sleep(PAUSE)
    problem = fail(body)
    if problem:
        print("    ! не прочитать список {}/{}: {}".format(module, entity, problem))
        return None
    items = (body.get("response") or {}).get("items") or []
    return {str(i.get("name", "")).strip(): i.get("id") for i in items}


def ensure(tok, module, entity, name, payload, apply, found, indent="  "):
    """Создаёт запись, если записи с таким названием ещё нет."""
    if found is None:
        print("{}? {:<28} пропуск — список недоступен".format(indent, name))
        return None
    if name in found:
        print("{}= {:<28} уже есть (id {})".format(indent, name, found[name]))
        return found[name]
    if not apply:
        print("{}+ {:<28} будет создано".format(indent, name))
        return None
    code, body = request(tok, module, entity, "create", data=payload)
    time.sleep(PAUSE)
    problem = fail(body)
    if problem:
        print("{}! {:<28} ОШИБКА: {}".format(indent, name, problem))
        return None
    new_id = (body.get("response") or {}).get("id")
    print("{}+ {:<28} создано (id {})".format(indent, name, new_id))
    return new_id


def step_business_lines(tok, apply):
    print("\n1. Направления деятельности  (fin/business_line)")
    found = existing(tok, "fin", "business_line")
    for n, name in enumerate(BUSINESS_LINES, start=1):
        ensure(tok, "fin", "business_line", name,
               {"name": name, "active": 1, "ordering": n}, apply, found)


def step_project_board(tok, apply):
    print("\n2. Доска проектов и этапы  (st/project_types, st/stages)")
    board = PROJECT_BOARD
    found = existing(tok, "st", "project_types")
    board_id = ensure(tok, "st", "project_types", board["name"],
                      {"name": board["name"], "description": board["description"],
                       "is_active": 1, "ordering": 1}, apply, found)

    if board_id is None:
        if apply:
            print("    доска не создана — этапы пропущены")
        else:
            print("    этапы будут созданы после доски:")
            for n, s in enumerate(board["stages"], start=1):
                print("      + {:<26} будет создано".format(s))
        return

    stages = existing(tok, "st", "stages", **{"filter[project_type_id]": board_id})
    for n, s in enumerate(board["stages"], start=1):
        ensure(tok, "st", "stages", s,
               {"name": s, "fullname": s, "project_type_id": board_id, "ordering": n},
               apply, stages, indent="    ")


def main():
    ap = argparse.ArgumentParser(description="Сборка скелета аккаунта Aspro.Cloud")
    ap.add_argument("--apply", action="store_true",
                    help="применить изменения (без флага — только показать)")
    args = ap.parse_args()

    tok = token()

    code, body = request(tok, "core", "user", "get")
    problem = fail(body)
    if problem:
        sys.exit("Токен не работает: {}\nОбновите: aspro_oauth.py refresh ...".format(problem))
    me = body.get("response", {})
    time.sleep(PAUSE)

    print("Аккаунт : {}".format(API_BASE))
    print("От имени: {} (id {}, admin={})".format(
        me.get("name") or me.get("username"), me.get("id"), me.get("role_admin")))
    print("Режим   : {}".format(
        "ПРИМЕНЕНИЕ — записи будут созданы" if args.apply
        else "сухой прогон — ничего не меняется"))
    print("\nОбозначения:  =  уже есть    +  создать    !  ошибка")

    step_business_lines(tok, args.apply)
    step_project_board(tok, args.apply)

    print("\nГотово.")
    if not args.apply:
        print("Это был сухой прогон. Проверьте названия выше и запустите с --apply.")


if __name__ == "__main__":
    main()
