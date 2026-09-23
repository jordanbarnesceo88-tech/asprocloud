#!/usr/bin/env python3
"""
Пользовательские поля: показать, проставить псевдонимы, проверить создание.

Версия 1 — 23.09

    python3 aspro_fields.py                      показать все поля
    python3 aspro_fields.py --set-aliases        показать, какие псевдонимы проставлю
    python3 aspro_fields.py --set-aliases --apply  проставить
    python3 aspro_fields.py --test-create        проверить, создаются ли поля через API

Зачем псевдонимы. Без них поле адресуется как cf_48. Номер привязан к
конкретной записи: пересоздадут поле — номер сменится, и все скрипты
сломаются молча. С псевдонимом адрес становится cf_site_address и
переживает пересоздание.

Только псевдонимы и признак их использования — ни названий, ни типов,
ни значений скрипт не трогает.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1 — 23.09"
API_BASE = os.environ.get("ASPRO_URL", "https://d18e.aspro.cloud").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "aspro_tokens.json")
PAUSE = 1.1
TIMEOUT = 30

# Известные названия -> псевдоним. Остальные транслитерируются.
KNOWN = {
    "номер договора": "contract_number",
    "дата договора": "contract_date",
    "срок окончания": "contract_end_date",
    "срок окончания договора": "contract_end_date",
    "тип договора": "contract_type",
    "статус договора": "contract_status",
    "автопролонгация": "contract_autorenew",
    "дата подписания": "contract_signed_at",
    "адрес объекта": "site_address",
    "технология": "site_technology",
    "характеристика линии доступа": "site_technology",
    "скорость канала": "site_bandwidth",
    "пропускная способность": "site_bandwidth",
    "дата начала оказания услуг": "service_start_date",
    "тип заказа": "order_type",
    "траффик": "site_traffic",
    "трафик": "site_traffic",
    "интерфейс": "site_interface",
    "коннектор": "site_connector",
    "месячная стоимость": "monthly_fee",
    "дата проверки": "check_date",
    "результат проверки": "check_result",
}

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def make_alias(name):
    key = " ".join(str(name).lower().split())
    if key in KNOWN:
        return KNOWN[key]
    out = []
    for ch in key:
        if ch in TRANSLIT:
            out.append(TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        elif ch in " -/":
            out.append("_")
    alias = "".join(out).strip("_")
    while "__" in alias:
        alias = alias.replace("__", "_")
    return alias[:40] or None


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала: aspro_oauth.py auth ...".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = json.load(fh).get("access_token")
    if not t:
        sys.exit("В файле токенов нет access_token.")
    return t


def call(tok, path, data=None, **params):
    url = "{}/api/v1/module/{}".format(API_BASE, path)
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


def fields(tok):
    body = call(tok, "customfields/fields/list", limit=100)
    bad = problem(body)
    if bad:
        sys.exit("Не прочитать список полей: {}".format(bad))
    return (body.get("response") or {}).get("items") or []


def show(items):
    print("\n{:<5} {:<30} {:<22} {:<16} {:<22} {}".format(
        "id", "название", "где", "тип", "псевдоним", "в API"))
    print("-" * 110)
    for f in items:
        where = "{}/{}".format(f.get("module") or "?", f.get("model") or "?")
        use = str(f.get("api_use_alias"))
        print("{:<5} {:<30} {:<22} {:<16} {:<22} {}".format(
            f.get("id"), (f.get("name") or "")[:29], where[:21],
            (f.get("type") or "")[:15], f.get("alias") or "—",
            "да" if use in ("1", "True", "true") else "нет"))


def set_aliases(tok, items, apply):
    print("\nПростановка псевдонимов")
    print("-" * 110)
    done = skipped = failed = 0
    for f in items:
        name, fid = f.get("name") or "", f.get("id")
        current = f.get("alias")
        wanted = make_alias(name)

        if not wanted:
            print("  ? {:<30} не придумать псевдоним — пропуск".format(name[:29]))
            skipped += 1
            continue
        if current == wanted and str(f.get("api_use_alias")) in ("1", "True", "true"):
            print("  = {:<30} уже {}".format(name[:29], wanted))
            skipped += 1
            continue
        if not apply:
            print("  + {:<30} -> {}".format(name[:29], wanted))
            done += 1
            continue

        res = call(tok, "customfields/fields/update/{}".format(fid),
                   data={"alias": wanted, "api_use_alias": 1, "webhook_use_alias": 1})
        bad = problem(res)
        if bad:
            print("  ! {:<30} ОШИБКА: {}".format(name[:29], bad))
            failed += 1
        else:
            print("  + {:<30} -> {}".format(name[:29], wanted))
            done += 1

    print("-" * 110)
    print("Итого: {} проставлено, {} пропущено, {} с ошибкой".format(done, skipped, failed))
    if not apply:
        print("Это был показ. Добавьте --apply, чтобы записать.")


def test_create(tok):
    """Документация утверждает, что поля через API не создаются, но метод
    create у сущности есть. Проверяем на деле и сразу убираем за собой."""
    print("\nПроверка: создаются ли поля через API")
    print("-" * 110)
    payload = {
        "name": "ПРОВЕРКА СОЗДАНИЯ ПОЛЯ",
        "type": "smalltext",
        "module": "st",
        "model": "project",
        "alias": "probe_field_delete_me",
        "api_use_alias": 1,
        "active": 1,
    }
    res = call(tok, "customfields/fields/create", data=payload)
    bad = problem(res)
    if bad:
        print("  создать не удалось: {}".format(bad))
        print("  Вывод: поля заводятся только в интерфейсе, документация права.")
        return

    new_id = (res.get("response") or {}).get("id")
    print("  поле создано, id {}".format(new_id))
    print("  Вывод: поля МОЖНО заводить через API, вопреки документации.")

    res = call(tok, "customfields/fields/delete/{}".format(new_id))
    bad = problem(res)
    print("  уборка: {}".format("не удалось — уберите id {} вручную".format(new_id)
                                if bad else "поле удалено"))


def main():
    ap = argparse.ArgumentParser(description="Пользовательские поля Aspro.Cloud")
    ap.add_argument("--set-aliases", action="store_true", help="проставить псевдонимы")
    ap.add_argument("--test-create", action="store_true",
                    help="проверить, создаются ли поля через API")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    args = ap.parse_args()

    tok = token()
    print("Версия : {}".format(VERSION))
    print("Аккаунт: {}".format(API_BASE))

    items = fields(tok)
    print("Полей  : {}".format(len(items)))
    show(items)

    if args.set_aliases:
        set_aliases(tok, items, args.apply)
    if args.test_create:
        test_create(tok)


if __name__ == "__main__":
    main()
