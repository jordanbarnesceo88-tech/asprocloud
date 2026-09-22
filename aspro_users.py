#!/usr/bin/env python3
"""
Заведение пользователей Aspro.Cloud из простого списка.

Версия 1 — 22.09

Читает users.csv рядом со скриптом. Разделитель — точка с запятой или
запятая, первая строка может быть заголовком (распознаётся по слову
"email"). Колонки, в любом порядке из этих названий:

    email        обязательно
    фамилия      last_name
    имя          first_name
    отчество     second_name
    должность    position
    телефон      phone_mobile
    админ        1 — сделать администратором портала

Минимальный файл — один столбец с адресами:

    email
    ivanov@nordline.ru
    petrova@nordline.ru

    python3 aspro_users.py            # сухой прогон
    python3 aspro_users.py --apply    # завести

Уже заведённых пропускает: сверяется по адресу. Повторный запуск
безопасен. Пользователей не удаляет — это делается в интерфейсе.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1 — 22.09"
API_BASE = os.environ.get("ASPRO_URL", "https://d18e.aspro.cloud").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "aspro_tokens.json")
CSV_FILE = os.path.join(HERE, "users.csv")
PAUSE = 1.1
TIMEOUT = 30

# как называется колонка -> поле API
COLUMNS = {
    "email": "email", "почта": "email", "адрес": "email",
    "фамилия": "last_name", "last_name": "last_name",
    "имя": "first_name", "first_name": "first_name",
    "отчество": "second_name", "second_name": "second_name",
    "должность": "position", "position": "position",
    "телефон": "phone_mobile", "phone": "phone_mobile",
    "админ": "role_admin", "администратор": "role_admin",
}


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала: aspro_oauth.py auth ...".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = json.load(fh).get("access_token")
    if not t:
        sys.exit("В файле токенов нет access_token.")
    return t


def call(tok, module, entity, method, data=None, **params):
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
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "HTTP {}".format(e.code), "description": raw[:200]}
    except Exception as e:
        return {"error": "{}: {}".format(type(e).__name__, e)}
    finally:
        time.sleep(PAUSE)


def problem(body):
    if not isinstance(body, dict) or "error" not in body:
        return None
    e = body["error"]
    head = "{}|{}".format(e.get("error_code"), e.get("error_msg")) \
        if isinstance(e, dict) else str(e)
    detail = body.get("details") or body.get("description") or ""
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    return "{} {}".format(head, detail).strip()


def read_people():
    if not os.path.exists(CSV_FILE):
        sys.exit("Нет {}.\nСоздайте файл: первая строка «email», дальше адреса "
                 "по одному в строке.".format(CSV_FILE))

    with open(CSV_FILE, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    if not text.strip():
        sys.exit("Файл {} пуст.".format(CSV_FILE))

    delimiter = ";" if text.count(";") >= text.count(",") else ","
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        sys.exit("В файле нет строк с данными.")

    header = [c.strip().lower() for c in rows[0]]
    if any("email" in c or "почт" in c for c in header):
        mapping = [COLUMNS.get(c) for c in header]
        body_rows = rows[1:]
    else:
        mapping = ["email"]                      # одна колонка без заголовка
        body_rows = rows

    if "email" not in [m for m in mapping if m]:
        sys.exit("В заголовке нет колонки с адресом почты.")

    people = []
    for row in body_rows:
        person = {}
        for i, field in enumerate(mapping):
            if not field or i >= len(row):
                continue
            value = row[i].strip()
            if value:
                person[field] = value
        if person.get("email"):
            people.append(person)
    return people


def main():
    ap = argparse.ArgumentParser(description="Заведение пользователей Aspro.Cloud")
    ap.add_argument("--apply", action="store_true", help="реально завести")
    args = ap.parse_args()

    tok = token()
    people = read_people()

    body = call(tok, "core", "user", "list", limit=100)
    bad = problem(body)
    if bad:
        sys.exit("Не прочитать список пользователей: {}".format(bad))
    existing = {str(u.get("username", "")).strip().lower(): u.get("id")
                for u in (body.get("response") or {}).get("items") or []}

    print("Версия  : {}".format(VERSION))
    print("Аккаунт : {}".format(API_BASE))
    print("Из файла: {} человек".format(len(people)))
    print("В системе уже: {}".format(len(existing)))
    print("Режим   : {}\n".format(
        "ПРИМЕНЕНИЕ — пользователи будут заведены" if args.apply
        else "сухой прогон — ничего не создаётся"))

    added = skipped = failed = 0
    for person in people:
        email = person["email"]
        who = " ".join(filter(None, [person.get("last_name"),
                                     person.get("first_name")])) or email

        if email.lower() in existing:
            print("  = {:<34} уже заведён (id {})".format(who, existing[email.lower()]))
            skipped += 1
            continue

        payload = {k: v for k, v in person.items()}
        payload.setdefault("role_login", 1)
        if str(payload.get("role_admin", "")).strip() not in ("1", "да", "yes"):
            payload.pop("role_admin", None)
        else:
            payload["role_admin"] = 1

        if not args.apply:
            print("  + {:<34} будет заведён ({})".format(who, email))
            added += 1
            continue

        res = call(tok, "core", "user", "create", data=payload)
        bad = problem(res)
        if bad:
            print("  ! {:<34} ОШИБКА: {}".format(who, bad))
            failed += 1
            continue
        new_id = (res.get("response") or {}).get("id")
        print("  + {:<34} заведён (id {})".format(who, new_id))
        added += 1

    print("\nИтого: {} новых, {} пропущено, {} с ошибкой".format(added, skipped, failed))
    if not args.apply:
        print("Это был сухой прогон. Запустите с --apply.")
    elif added:
        print("Приглашения система рассылает сама — проверьте почту сотрудников.")


if __name__ == "__main__":
    main()
