#!/usr/bin/env python3
"""
Aspro.Cloud: сборка скелета аккаунта Норд Лайн поверх OAuth-токена.

По умолчанию НИЧЕГО НЕ ПИШЕТ — показывает, что будет сделано.
Чтобы применить, добавьте --apply.

    python3 aspro_setup.py            # сухой прогон
    python3 aspro_setup.py --apply    # применить

Ничего не удаляет. Существующие стадии переименовывает по порядку,
лишние гасит (active=0), недостающие создаёт. Повторный запуск
дублей не создаёт.

Токен берётся из aspro_tokens.json (aspro_oauth.py auth ...).
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

# ═════════════════════════════════════════════════════════════════════════
#  ЧТО НАСТРАИВАЕМ. Правьте здесь — код трогать не нужно.
# ═════════════════════════════════════════════════════════════════════════

# Организация уже есть (id 1, «Моя компания») — переименовываем.
ORGANIZATION = {"id": 1, "name": "Норд Лайн"}

# Направления деятельности Норд Лайн.
BUSINESS_LINES = [
    "Предоставление каналов связи",
    "Монтажные работы",
]

# Воронки продаж.
# Воронка id 1 уже есть («Разработка под ключ») с пятью стадиями —
# переиспользуем её под каналы связи, чтобы не плодить лишнее.
PIPELINES = [
    {
        "id": 1,                                  # существующая, переименуем
        "name": "Каналы связи",
        "description": "Абонентская модель: от запроса до предоставления услуг",
        "stages": [
            "Запрос по почте",
            "Просчёт технической возможности",
            "КП",
            "Согласование КП",
            "Договор",
            "Согласование договора",
            "Подписание",
            "Предоставление услуг",
        ],
    },
    {
        "id": None,                               # создаём новую
        "name": "Монтажные работы",
        "description": "Разовые работы: от запроса до сдачи",
        "stages": [
            "Запрос",
            "Просчёт финансовой модели",
            "КП",
            "Согласование КП",
            "Договор",
            "Согласование договора",
            "Подписание",
            "Выполнение работ",
        ],
    },
]

# Доски проектов. Этапы — предложение, проверьте названия.
PROJECT_BOARDS = [
    {
        "name": "Абонентское обслуживание",
        "description": "Проекты по каналам связи, продолжающиеся во времени",
        "stages": ["Подключение", "Активное обслуживание", "Пролонгация", "Закрыт"],
    },
    {
        "name": "Монтажные работы",
        "description": "Разовые проекты с фиксированным объёмом",
        "stages": ["Подготовка", "Монтаж", "Сдача работ", "Закрыт"],
    },
]

# ═════════════════════════════════════════════════════════════════════════


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
    if isinstance(body, dict) and "error" in body:
        e = body["error"]
        if isinstance(e, dict):
            return "{}|{}".format(e.get("error_code"), e.get("error_msg"))
        return "{} {}".format(e, body.get("details") or body.get("description") or "")
    if isinstance(body, dict) and "network_error" in body:
        return body["network_error"]
    return None


def listing(tok, module, entity, **params):
    code, body = request(tok, module, entity, "list", limit=100, **params)
    time.sleep(PAUSE)
    problem = fail(body)
    if problem:
        print("    ! список {}/{} недоступен: {}".format(module, entity, problem))
        return None
    return (body.get("response") or {}).get("items") or []


def create(tok, module, entity, payload, label, apply, indent="  "):
    if not apply:
        print("{}+ {:<34} будет создано".format(indent, label))
        return None
    code, body = request(tok, module, entity, "create", data=payload)
    time.sleep(PAUSE)
    problem = fail(body)
    if problem:
        print("{}! {:<34} ОШИБКА: {}".format(indent, label, problem))
        return None
    new_id = (body.get("response") or {}).get("id")
    print("{}+ {:<34} создано (id {})".format(indent, label, new_id))
    return new_id


def update(tok, module, entity, rec_id, payload, label, apply, indent="  "):
    if not apply:
        print("{}~ {:<34} будет изменено (id {})".format(indent, label, rec_id))
        return True
    code, body = request(tok, module, entity, "update/{}".format(rec_id), data=payload)
    time.sleep(PAUSE)
    problem = fail(body)
    if problem:
        print("{}! {:<34} ОШИБКА: {}".format(indent, label, problem))
        return False
    print("{}~ {:<34} изменено (id {})".format(indent, label, rec_id))
    return True


# ─────────────────────────────────────────────────────────────── шаги

def step_organization(tok, apply):
    print("\n1. Организация  (fin/organization)")
    items = listing(tok, "fin", "organization")
    if items is None:
        return
    current = next((i for i in items if str(i.get("id")) == str(ORGANIZATION["id"])), None)
    if not current:
        print("    ! организация id {} не найдена".format(ORGANIZATION["id"]))
        return
    if str(current.get("name", "")).strip() == ORGANIZATION["name"]:
        print("  = {:<34} уже называется так".format(ORGANIZATION["name"]))
        return
    update(tok, "fin", "organization", ORGANIZATION["id"],
           {"name": ORGANIZATION["name"]},
           "{} <- {}".format(ORGANIZATION["name"], current.get("name")), apply)


def step_business_lines(tok, apply):
    print("\n2. Направления деятельности  (fin/business_line)")
    items = listing(tok, "fin", "business_line")
    if items is None:
        return
    have = {str(i.get("name", "")).strip(): i.get("id") for i in items}
    for n, name in enumerate(BUSINESS_LINES, start=1):
        if name in have:
            print("  = {:<34} уже есть (id {})".format(name, have[name]))
            continue
        create(tok, "fin", "business_line",
               {"name": name, "active": 1, "ordering": n}, name, apply)


def sync_stages(tok, module, entity, parent_field, parent_id, wanted, apply):
    """Приводит набор стадий к желаемому, ничего не удаляя.

    Существующие переименовывает по порядку, недостающие создаёт,
    лишние гасит (active=0)."""
    existing = listing(tok, module, entity, **{"filter[{}]".format(parent_field): parent_id})
    if existing is None:
        existing = []
    existing.sort(key=lambda i: (int(i.get("ordering") or 0), int(i.get("id") or 0)))

    for n, name in enumerate(wanted):
        if n < len(existing):
            cur = existing[n]
            if str(cur.get("name", "")).strip() == name:
                print("    = {:<32} уже на месте (id {})".format(name, cur.get("id")))
            else:
                update(tok, module, entity, cur.get("id"),
                       {"name": name, "ordering": n + 1, "active": 1},
                       "{} <- {}".format(name, cur.get("name")), apply, indent="    ")
        else:
            payload = {"name": name, "ordering": n + 1, "active": 1,
                       parent_field: parent_id}
            if entity == "stages" and module == "st":
                payload["fullname"] = name
            create(tok, module, entity, payload, name, apply, indent="    ")

    for extra in existing[len(wanted):]:
        update(tok, module, entity, extra.get("id"), {"active": 0},
               "погасить «{}»".format(extra.get("name")), apply, indent="    ")


def step_pipelines(tok, apply):
    print("\n3. Воронки продаж  (crm/pipeline, crm/pipeline_stage)")
    items = listing(tok, "crm", "pipeline")
    if items is None:
        return
    have_by_id = {str(i.get("id")): i for i in items}
    have_by_name = {str(i.get("name", "")).strip(): i.get("id") for i in items}

    for n, pl in enumerate(PIPELINES, start=1):
        pid = pl["id"]
        payload = {"name": pl["name"], "description": pl["description"], "ordering": n}

        if pid is not None and str(pid) in have_by_id:
            cur = have_by_id[str(pid)]
            if str(cur.get("name", "")).strip() == pl["name"]:
                print("  = {:<34} уже называется так (id {})".format(pl["name"], pid))
            else:
                update(tok, "crm", "pipeline", pid, payload,
                       "{} <- {}".format(pl["name"], cur.get("name")), apply)
        elif pl["name"] in have_by_name:
            pid = have_by_name[pl["name"]]
            print("  = {:<34} уже есть (id {})".format(pl["name"], pid))
        else:
            pid = create(tok, "crm", "pipeline", payload, pl["name"], apply)
            if pid is None and apply:
                continue
            if pid is None:
                print("    стадии появятся после создания воронки:")
                for s in pl["stages"]:
                    print("      + {}".format(s))
                continue

        sync_stages(tok, "crm", "pipeline_stage", "pipeline_id", pid, pl["stages"], apply)


def step_project_boards(tok, apply):
    print("\n4. Доски проектов и этапы  (st/project_types, st/stages)")
    items = listing(tok, "st", "project_types")
    if items is None:
        return
    have = {str(i.get("name", "")).strip(): i.get("id") for i in items}

    for n, board in enumerate(PROJECT_BOARDS, start=1):
        if board["name"] in have:
            bid = have[board["name"]]
            print("  = {:<34} уже есть (id {})".format(board["name"], bid))
        else:
            bid = create(tok, "st", "project_types",
                         {"name": board["name"], "description": board["description"],
                          "is_active": 1, "ordering": n}, board["name"], apply)
        if bid is None:
            if not apply:
                print("    этапы появятся после создания доски:")
                for s in board["stages"]:
                    print("      + {}".format(s))
            continue
        sync_stages(tok, "st", "stages", "project_type_id", bid, board["stages"], apply)


def main():
    ap = argparse.ArgumentParser(description="Сборка скелета аккаунта Норд Лайн")
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
        "ПРИМЕНЕНИЕ — изменения будут записаны" if args.apply
        else "сухой прогон — ничего не меняется"))
    print("\nОбозначения:  =  уже так   +  создать   ~  изменить   !  ошибка")

    step_organization(tok, args.apply)
    step_business_lines(tok, args.apply)
    step_pipelines(tok, args.apply)
    step_project_boards(tok, args.apply)

    print("\nГотово.")
    if not args.apply:
        print("Это был сухой прогон. Проверьте названия выше и запустите с --apply.")


if __name__ == "__main__":
    main()
