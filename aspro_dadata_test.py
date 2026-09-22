#!/usr/bin/env python3
"""
Проверка: подтягивает ли Aspro данные из ЕГРЮЛ (DaData) при создании
контрагента ЧЕРЕЗ API, а не через форму в интерфейсе.

Это эксперимент, а не настройка. От его исхода зависит, что нужно
готовить для импорта: полный Excel со всеми реквизитами или только
список ИНН.

    python3 aspro_dadata_test.py                    # сухой прогон
    python3 aspro_dadata_test.py --apply            # создать и посмотреть
    python3 aspro_dadata_test.py --cleanup 12 13    # удалить созданное

ВНИМАНИЕ: --apply создаёт записи в боевом аккаунте. Скрипт печатает id
созданных контрагентов, чтобы их можно было убрать через --cleanup.
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

INN_LIST = ["9704057666", "8706000342"]

# Поля, которые должна была бы заполнить DaData из ЕГРЮЛ.
WATCH = ["name", "name_legal", "name_legal_full", "VAT", "VAT1", "VAT2",
         "phone", "web", "bank_details", "industry_id",
         "shipping_country", "shipping_state", "shipping_city", "shipping_zip",
         "shipping_address_line_1", "shipping_address_line_2",
         "billing_country", "billing_city", "billing_address_line_1"]


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала: aspro_oauth.py auth ...".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = json.load(fh).get("access_token")
    if not t:
        sys.exit("В файле токенов нет access_token.")
    return t


def request(tok, method_path, data=None):
    url = "{}/api/v1/module/crm/account/{}".format(API_BASE, method_path)
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


def problem_of(body):
    if isinstance(body, dict) and "error" in body:
        e = body["error"]
        if isinstance(e, dict):
            return "{}|{}".format(e.get("error_code"), e.get("error_msg"))
        detail = body.get("details") or body.get("description") or ""
        return "{} {}".format(e, json.dumps(detail, ensure_ascii=False)
                              if isinstance(detail, dict) else detail)
    if isinstance(body, dict) and "network_error" in body:
        return body["network_error"]
    return None


def try_create(tok, inn):
    """Сначала пробуем только ИНН. Если API требует название — добавляем
    временную заглушку и смотрим, перезапишет ли её ЕГРЮЛ."""
    attempts = [
        ("только ИНН", {"type": 1, "VAT": inn}),
        ("ИНН + заглушка названия", {"type": 1, "VAT": inn,
                                     "name": "ПРОВЕРКА ИНН {}".format(inn)}),
    ]
    for label, payload in attempts:
        code, body = request(tok, "create", data=payload)
        time.sleep(PAUSE)
        bad = problem_of(body)
        if not bad:
            new_id = (body.get("response") or {}).get("id")
            print("    создано ({}) -> id {}".format(label, new_id))
            return new_id, label
        print("    {} -> отказ: {}".format(label, bad))
    return None, None


def inspect(tok, rec_id, placeholder_used):
    code, body = request(tok, "get/{}".format(rec_id))
    time.sleep(PAUSE)
    bad = problem_of(body)
    if bad:
        print("    прочитать карточку не удалось: {}".format(bad))
        return
    rec = body.get("response") or {}
    filled = {k: rec.get(k) for k in WATCH
              if rec.get(k) not in (None, "", "0", 0)}

    print("    заполнено полей из списка наблюдения: {} из {}".format(
        len(filled), len(WATCH)))
    for k, v in filled.items():
        print("      {:<26} {}".format(k, str(v)[:60]))

    name = str(rec.get("name") or "")
    if placeholder_used and name.startswith("ПРОВЕРКА ИНН"):
        print("    ВЫВОД: название осталось заглушкой — ЕГРЮЛ карточку не заполнил.")
    elif len(filled) <= 2:
        print("    ВЫВОД: кроме ИНН почти ничего не появилось — обогащения нет.")
    else:
        print("    ВЫВОД: карточка заполнена из справочника — достаточно списка ИНН.")


def cleanup(tok, ids):
    print("Удаление тестовых контрагентов\n")
    for rec_id in ids:
        code, body = request(tok, "delete/{}".format(rec_id))
        time.sleep(PAUSE)
        bad = problem_of(body)
        print("  id {:<8} {}".format(rec_id, "ошибка: " + bad if bad else "удалён"))


def main():
    ap = argparse.ArgumentParser(description="Проверка обогащения из ЕГРЮЛ через API")
    ap.add_argument("--apply", action="store_true", help="реально создать записи")
    ap.add_argument("--cleanup", nargs="*", metavar="ID",
                    help="удалить контрагентов с этими id и выйти")
    ap.add_argument("--inn", nargs="*", default=INN_LIST, help="список ИНН")
    args = ap.parse_args()

    tok = token()

    if args.cleanup is not None:
        if not args.cleanup:
            sys.exit("Укажите id: --cleanup 12 13")
        cleanup(tok, args.cleanup)
        return

    print("Аккаунт: {}".format(API_BASE))
    print("Режим  : {}\n".format(
        "ПРИМЕНЕНИЕ — записи будут созданы" if args.apply
        else "сухой прогон — ничего не создаётся"))

    if not args.apply:
        for inn in args.inn:
            print("  ИНН {} — будет создан контрагент type=1, затем прочитан".format(inn))
        print("\nЭто сухой прогон. Запустите с --apply, чтобы провести проверку.")
        return

    created = []
    for inn in args.inn:
        print("  ИНН {}".format(inn))
        rec_id, label = try_create(tok, inn)
        if rec_id:
            created.append(str(rec_id))
            inspect(tok, rec_id, label and "заглушка" in label)
        print()

    if created:
        print("Созданные записи: {}".format(" ".join(created)))
        print("Убрать их: python3 {} --cleanup {}".format(
            os.path.basename(__file__), " ".join(created)))


if __name__ == "__main__":
    main()
