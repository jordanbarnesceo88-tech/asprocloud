#!/usr/bin/env python3
"""
Показать одну запись целиком, со всеми заполненными полями.

Версия 1 — 23.09

    python3 aspro_get.py st projects BS-016     # найти по названию
    python3 aspro_get.py st projects 27         # взять по номеру
    python3 aspro_get.py crm lead 16
    python3 aspro_get.py crm account 8703011346

Показывает только непустые поля, отдельным блоком — пользовательские.
Только чтение.
"""

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
TIMEOUT = 30
PAUSE = 1.1

EMPTY = (None, "", "0", 0, "0000-00-00", "0000-00-00 00:00:00")


def token():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала: aspro_oauth.py auth ...".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = json.load(fh).get("access_token")
    if not t:
        sys.exit("В файле токенов нет access_token.")
    return t


def call(tok, path, **params):
    url = "{}/api/v1/module/{}".format(API_BASE, path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer {}".format(tok), "Accept": "application/json"})
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


def find_by_text(tok, module, entity, needle):
    """Ищет запись по подстроке в любом строковом поле."""
    body = call(tok, "{}/{}/list".format(module, entity), limit=100, search=needle)
    bad = problem(body)
    if bad:
        print("  поиск не сработал: {} — беру полный список".format(bad))
        body = call(tok, "{}/{}/list".format(module, entity), limit=100)
        if problem(body):
            return []
    items = (body.get("response") or {}).get("items") or []
    low = needle.lower()
    hits = [i for i in items
            if any(low in str(v).lower() for v in i.values() if isinstance(v, str))]
    return hits or items


def dump(record, customfields):
    plain = {k: v for k, v in record.items()
             if not k.startswith("cf_") and v not in EMPTY}
    cf = {k: v for k, v in record.items() if k.startswith("cf_") and v not in EMPTY}

    width = max((len(k) for k in plain), default=10)
    print("\nЗаполненные поля ({} из {}):".format(len(plain), len(record)))
    print("-" * 72)
    for k in sorted(plain):
        print("  {:<{w}}  {}".format(k, str(plain[k])[:70], w=width))

    if customfields:
        print("\nПользовательские поля:")
        print("-" * 72)
        for f in customfields:
            value = f.get("value")
            mark = " " if value in EMPTY else "*"
            print("  {} cf_{:<6} {:<28} {:<14} {}".format(
                mark, f.get("id"), (f.get("title") or "")[:27],
                f.get("alias") or "без псевдонима", str(value)[:30]))
        filled = sum(1 for f in customfields if f.get("value") not in EMPTY)
        print("\n  заполнено {} из {} (звёздочкой отмечены непустые)".format(
            filled, len(customfields)))
    elif cf:
        print("\nПользовательские поля (без расшифровки):")
        print("-" * 72)
        for k in sorted(cf):
            print("  {:<14} {}".format(k, str(cf[k])[:60]))


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__.strip())
    module, entity, needle = sys.argv[1], sys.argv[2], " ".join(sys.argv[3:])
    tok = token()

    print("Версия : {}".format(VERSION))
    print("Аккаунт: {}".format(API_BASE))
    print("Ищу    : {}/{} — «{}»".format(module, entity, needle))

    record = None
    if needle.isdigit():
        body = call(tok, "{}/{}/get/{}".format(module, entity, needle), customfields=1)
        bad = problem(body)
        if bad:
            print("  по номеру не нашлось ({}), пробую поиском".format(bad))
        else:
            record = body.get("response")

    if record is None:
        hits = find_by_text(tok, module, entity, needle)
        if not hits:
            sys.exit("Ничего не найдено.")
        if len(hits) > 1:
            print("\nНайдено несколько — уточните номером:")
            for h in hits[:20]:
                print("  id {:<8} {}".format(h.get("id"), h.get("name") or ""))
            return
        rec_id = hits[0].get("id")
        body = call(tok, "{}/{}/get/{}".format(module, entity, rec_id), customfields=1)
        bad = problem(body)
        if bad:
            sys.exit("Не прочитать запись {}: {}".format(rec_id, bad))
        record = body.get("response")

    if not isinstance(record, dict):
        sys.exit("Ответ не похож на запись: {}".format(str(record)[:200]))

    print("\nid {} — {}".format(record.get("id"), record.get("name") or "без названия"))
    dump(record, record.get("customfields") or [])


if __name__ == "__main__":
    main()
