#!/usr/bin/env python3
"""
Aspro.Cloud: авторизация по OAuth 2.0 и разведка аккаунта поверх неё.

Обходной путь для случая, когда серверные API-ключи не работают
(ошибка "api key not loaded"), а регистрация внешних приложений доступна.

По документации сервер авторизации — общий для всех аккаунтов
(my.aspro.cloud), а сами вызовы API идут на домен вашего аккаунта.

Шаг 1. Получить токен:

    python3 aspro_oauth.py auth --client-id ID --client-secret SECRET \
        --redirect-uri http://localhost:8765/callback

Откроется ссылка для подтверждения. Если redirect указывает на localhost,
скрипт сам поймает код. Если нет — после одобрения скопируйте адрес из
адресной строки браузера и вставьте в скрипт по запросу.

Токены лягут в aspro_tokens.json рядом со скриптом.
Срок жизни: access — 2 дня, refresh — 6 месяцев.

Шаг 2. Снять картину аккаунта:

    python3 aspro_oauth.py recon

Шаг 3. Обновить протухший токен:

    python3 aspro_oauth.py refresh --client-id ID --client-secret SECRET

Только чтение — ничего не создаёт и не меняет.
"""

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_BASE = os.environ.get("ASPRO_AUTH_URL", "https://my.aspro.cloud").rstrip("/")
API_BASE = os.environ.get("ASPRO_URL", "https://d18e.aspro.cloud").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "aspro_tokens.json")

PAUSE = 1.1
TIMEOUT = 30

PROBES = [
    ("core", "user", "Пользователи"),
    ("crm", "account", "Контрагенты"),
    ("crm", "lead", "Сделки"),
    ("crm", "pipeline", "Воронки"),
    ("crm", "pipeline_stage", "Стадии воронки"),
    ("crm", "loss_reason", "Причины отказа"),
    ("crm", "source", "Источники сделок"),
    ("st", "projects", "Проекты"),
    ("st", "project_types", "Доски проектов"),
    ("st", "stages", "Этапы проектов"),
    ("st", "portfolio", "Портфели"),
    ("st", "project_money_stage", "Плановая выручка"),
    ("st", "project_expense", "Плановые затраты"),
    ("task", "tasks", "Задачи"),
    ("task", "workflows", "Рабочие процессы задач"),
    ("task", "stages", "Этапы рабочих процессов"),
    ("fin", "organization", "Организации"),
    ("fin", "bank_account", "Счета организации"),
    ("fin", "business_line", "Направления деятельности"),
    ("fin", "categories", "Статьи учёта"),
    ("fin", "invoice", "Счета"),
    ("fin", "estimate", "Предложения (КП)"),
    ("fin", "estimate_template", "Шаблоны КП"),
    ("fin", "recurring_invoice", "Регулярные счета"),
    ("fin", "plan_money", "Плановые деньги"),
    ("finacts", "acts", "Акты"),
    ("finacts", "templates", "Печатные формы актов"),
    ("timetracker", "timesheets", "Time sheets"),
    ("timetracker", "timelogs", "Тайм-логи"),
    ("products", "product", "Товары"),
    ("products", "units", "Единицы измерения"),
    ("products", "pricelist", "Прайс-листы"),
    ("agile", "projects", "Agile-проекты"),
    ("customfields", "fields", "Пользовательские поля"),
    ("knowledgebase", "knowledgebases", "Базы знаний"),
    ("company", "absences", "Отсутствия"),
    ("orgchart", "department", "Отделы"),
    ("businessprocess", "process", "Бизнес-процессы"),
    ("calendar", "calendar", "Календари"),
]

CONTRACT_HUNT = [
    ("fin", "contract"), ("crm", "contract"), ("st", "contract"),
    ("contracts", "contract"), ("fin", "contracts"), ("crm", "agreement"),
]


# --------------------------------------------------------------- HTTP helpers

def post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:400]}
    except Exception as e:
        return 0, {"network_error": "{}: {}".format(type(e).__name__, e)}


def api_get(token, module, entity, method="list", **params):
    params.setdefault("limit", 1)
    url = "{}/api/v1/module/{}/{}/{}?{}".format(
        API_BASE, module, entity, method, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer {}".format(token),
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw), None
            except json.JSONDecodeError:
                return r.status, raw[:300], "не JSON"
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), None
        except json.JSONDecodeError:
            return e.code, raw[:300], None
    except Exception as e:
        return 0, None, "{}: {}".format(type(e).__name__, e)


def classify(code, body, err):
    if err:
        return "СЕТЬ", err
    if isinstance(body, dict) and "error" in body:
        e = body["error"]
        if isinstance(e, dict):
            return "ОТКАЗ", "{}|{}".format(e.get("error_code"), e.get("error_msg"))
        return "ОТКАЗ", str(e)
    if code == 200 and isinstance(body, dict) and "response" in body:
        resp = body["response"]
        if isinstance(resp, dict) and "total" in resp:
            return "ОК", int(resp.get("total") or 0)
        return "ОК", "—"
    if code == 401:
        return "ТОКЕН", "401 — истёк или отозван, нужен refresh"
    if code == 404:
        return "НЕТ ТАКОГО", code
    if code == 429:
        return "ЛИМИТ", code
    return "HTTP {}".format(code), str(body)[:120]


# ------------------------------------------------------------------ local catch

class _Catcher(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.result = {k: v[0] for k, v in q.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _Catcher.result
        self.wfile.write((
            "<h2>{}</h2><p>Можно закрыть вкладку и вернуться в терминал.</p>"
        ).format("Код получен" if ok else "Код не пришёл").encode("utf-8"))

    def log_message(self, *a):
        pass


def catch_code_locally(redirect_uri, timeout=300):
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 80
    srv = HTTPServer(("127.0.0.1", port), _Catcher)
    srv.timeout = timeout
    print("Жду ответа на {} (до {} с)…".format(redirect_uri, timeout))
    srv.handle_request()
    srv.server_close()
    return _Catcher.result


def parse_pasted_url(raw):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(raw.strip()).query)
    return {k: v[0] for k, v in q.items()}


# ---------------------------------------------------------------------- actions

def save_tokens(payload):
    payload["_saved_at"] = int(time.time())
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("Токены сохранены: {}".format(TOKEN_FILE))


def load_tokens():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Нет {}. Сначала выполните режим auth.".format(TOKEN_FILE))
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def exchange_code(args, code):
    """Меняет код на токен. Документация называет grant_type 'code';
    если сервер не принимает, пробуем стандартное 'authorization_code'."""
    for grant in ("code", "authorization_code"):
        status, body = post_form(AUTH_BASE + "/oauth2/access_token", {
            "grant_type": grant,
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "code": code,
            "redirect_uri": args.redirect_uri,
        })
        if status == 200 and "access_token" in body:
            print("Токен получен (grant_type={}).".format(grant))
            return body
        print("  grant_type={} -> HTTP {} {}".format(
            grant, status, json.dumps(body, ensure_ascii=False)[:200]))
    return None


def do_auth(args):
    state = secrets.token_urlsafe(16)
    url = "{}/oauth2/authorize?{}".format(AUTH_BASE, urllib.parse.urlencode({
        "client_id": args.client_id,
        "state": state,
        "redirect_uri": args.redirect_uri,
    }))
    print("\nОткройте ссылку и подтвердите доступ:\n\n{}\n".format(url))
    try:
        webbrowser.open(url)
    except Exception:
        pass

    parsed = urllib.parse.urlparse(args.redirect_uri)
    local = parsed.hostname in ("localhost", "127.0.0.1")

    if local:
        got = catch_code_locally(args.redirect_uri)
    else:
        print("Redirect ведёт не на localhost.")
        print("После подтверждения скопируйте АДРЕС из адресной строки целиком.")
        got = parse_pasted_url(input("Вставьте адрес сюда: "))

    if "error" in got:
        sys.exit("Сервер вернул ошибку: {} — {}".format(
            got.get("error"), got.get("error_description", "")))
    if got.get("state") and got["state"] != state:
        sys.exit("state не совпал — запрос мог быть подменён. Прерываю.")
    code = got.get("code")
    if not code:
        sys.exit("Кода авторизации нет в ответе: {}".format(got))

    print("Код получен, меняю на токен…")
    tokens = exchange_code(args, code)
    if not tokens:
        sys.exit("Обменять код на токен не удалось — текст ответа выше.")
    save_tokens(tokens)
    print("\nТеперь: python3 {} recon".format(os.path.basename(__file__)))


def do_refresh(args):
    t = load_tokens()
    status, body = post_form(AUTH_BASE + "/oauth2/refresh_token", {
        "grant_type": "refresh_token",
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "refresh_token": t.get("refresh_token", ""),
    })
    if status == 200 and "access_token" in body:
        save_tokens(body)
        print("Токен обновлён. Обратите внимание: refresh_token тоже сменился.")
    else:
        sys.exit("Обновить не удалось: HTTP {} {}".format(
            status, json.dumps(body, ensure_ascii=False)[:300]))


def do_recon(_args):
    token = load_tokens().get("access_token")
    if not token:
        sys.exit("В файле токенов нет access_token.")

    report = {"account": API_BASE, "auth": "oauth2",
              "probes": [], "contract_hunt": [], "custom_fields": []}

    print("Аккаунт: {}\nАвторизация: OAuth 2.0 Bearer\n".format(API_BASE))

    code, body, err = api_get(token, "core", "user", method="get")
    verdict, detail = classify(code, body, err)
    if verdict == "ОК" and isinstance(body, dict):
        u = body.get("response", {})
        print("Токен действует от имени: {} (id {}, admin={})\n".format(
            u.get("name") or u.get("username"), u.get("id"), u.get("role_admin")))
        report["identity"] = u
    else:
        print("Не удалось определить владельца токена: {} {}\n".format(verdict, detail))
        report["identity_error"] = {"verdict": verdict, "detail": str(detail)}
        if verdict in ("СЕТЬ", "ТОКЕН"):
            sys.exit("Дальше идти смысла нет.")
    time.sleep(PAUSE)

    print("{:<14} {:<22} {:<26} {:<12} {}".format(
        "МОДУЛЬ", "СУЩНОСТЬ", "ЧТО ЭТО", "СТАТУС", "ЗАПИСЕЙ / ПРИЧИНА"))
    print("-" * 100)
    for module, entity, title in PROBES:
        code, body, err = api_get(token, module, entity)
        verdict, detail = classify(code, body, err)
        print("{:<14} {:<22} {:<26} {:<12} {}".format(
            module, entity, title[:25], verdict, detail))
        report["probes"].append({"module": module, "entity": entity, "title": title,
                                 "http": code, "verdict": verdict, "detail": str(detail)})
        time.sleep(PAUSE)

    print("\nПоиск объекта договора (в спецификации API его нет):")
    for module, entity in CONTRACT_HUNT:
        code, body, err = api_get(token, module, entity)
        verdict, detail = classify(code, body, err)
        print("  {}/{:<12} {:<12} {}{}".format(
            module, entity, verdict, detail, "   <-- НАЙДЕНО" if verdict == "ОК" else ""))
        report["contract_hunt"].append({"module": module, "entity": entity,
                                        "verdict": verdict, "detail": str(detail)})
        time.sleep(PAUSE)

    print("\nПользовательские поля, уже заведённые в аккаунте:")
    code, body, err = api_get(token, "customfields", "fields", limit=100)
    if code == 200 and isinstance(body, dict) and "response" in body:
        items = body["response"].get("items") or []
        if not items:
            print("  (нет ни одного)")
        for f in items:
            print("  cf_{:<8} {:<12} {:<20} {}".format(
                f.get("id"), f.get("type", ""), f.get("alias") or "—",
                f.get("title") or f.get("name") or ""))
        report["custom_fields"] = items

    out = os.path.join(HERE, "aspro_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\nОтчёт сохранён: {}\nПришлите его — по нему соберу настройку.".format(out))


def main():
    p = argparse.ArgumentParser(description="Aspro.Cloud через OAuth 2.0")
    sub = p.add_subparsers(dest="mode", required=True)

    a = sub.add_parser("auth", help="получить токен")
    a.add_argument("--client-id", required=True)
    a.add_argument("--client-secret", required=True)
    a.add_argument("--redirect-uri", default="http://localhost:8765/callback")
    a.set_defaults(func=do_auth)

    r = sub.add_parser("refresh", help="обновить токен")
    r.add_argument("--client-id", required=True)
    r.add_argument("--client-secret", required=True)
    r.set_defaults(func=do_refresh)

    c = sub.add_parser("recon", help="снять картину аккаунта")
    c.set_defaults(func=do_recon)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
