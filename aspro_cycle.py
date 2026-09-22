#!/usr/bin/env python3
"""
Сквозная проверка цикла: контрагент → сделка → КП → счёт → проект → акт.

Это главная проверка пилота. Критерий успеха №1 требует провести три
сделки полным циклом; критерий неуспеха №4 срабатывает, если на цикл
не хватает функциональности. Скрипт выясняет это на выдуманной сделке,
пока настоящие ещё не пошли.

    python3 aspro_cycle.py                 # сухой прогон
    python3 aspro_cycle.py --apply         # провести цикл
    python3 aspro_cycle.py --cleanup       # убрать созданное

Созданное записывается в aspro_cycle_state.json, оттуда же читается при
удалении — чистка идёт в обратном порядке.

Скрипт не останавливается на первой ошибке: важно увидеть, какие шаги
цикла проходят, а какие нет. В конце печатается сводка.
"""

import argparse
import datetime
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
STATE_FILE = os.path.join(HERE, "aspro_cycle_state.json")
PAUSE = 1.1
TIMEOUT = 30

MARK = "ПРОВЕРКА ЦИКЛА"          # метка тестовых записей
TODAY = datetime.date.today()
SUM_NET = 100000.0               # сумма без налога, для наглядности

# Какие справочники ищем по названию (точное совпадение)
WANT = {
    "pipeline":      "Каналы связи",
    "stage":         "Запрос по почте",
    "board":         "Абонентское обслуживание",
    "board_stage":   "Подключение",
    "business_line": "Предоставление каналов связи",
    "act_template":  "Aкт выполненных работ (Rus)",
    "act_status":    "Создан",
    "unit":          "Час",
    "category":      "Оказание услуг",
}


# ─────────────────────────────────────────────────────────────── транспорт

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
    """Текст ошибки. Для validation error важны details — в них сервер
    называет поле, которое не прошло проверку."""
    if not isinstance(body, dict) or "error" not in body:
        return None
    e = body["error"]
    head = "{}|{}".format(e.get("error_code"), e.get("error_msg")) \
        if isinstance(e, dict) else str(e)
    detail = body.get("details") or body.get("description") or ""
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    return "{} {}".format(head, detail).strip()


def find_id(tok, module, entity, name, field="name"):
    body = call(tok, module, entity, "list", limit=100)
    bad = problem(body)
    if bad:
        return None, bad
    for it in (body.get("response") or {}).get("items") or []:
        if str(it.get(field, "")).strip() == name:
            return it.get("id"), None
    return None, "не найдено «{}»".format(name)


# ─────────────────────────────────────────────────────────────── состояние

class Run:
    def __init__(self, apply):
        self.apply = apply
        self.created = []       # [(module, entity, id, что это)]
        self.steps = []         # [(шаг, статус, подробность)]

    def note(self, step, status, detail=""):
        self.steps.append((step, status, detail))
        sign = {"ОК": "+", "ПРОПУСК": "·", "ОШИБКА": "!"}.get(status, " ")
        print("  {} {:<34} {}".format(sign, step, detail))

    def make(self, tok, module, entity, payload, step, what, minimal=None):
        """minimal — урезанный набор полей. Если полный отвергнут, пробуем
        его: так видно, ломает ли запрос одно из необязательных полей."""
        if not self.apply:
            self.note(step, "ПРОПУСК", "будет создано")
            return None
        body = call(tok, module, entity, "create", data=payload)
        bad = problem(body)
        if bad and minimal:
            self.note(step, "ОШИБКА", "{} — пробую минимальный набор".format(bad))
            body = call(tok, module, entity, "create", data=minimal)
            bad2 = problem(body)
            if not bad2:
                extra = sorted(set(payload) - set(minimal))
                new_id = (body.get("response") or {}).get("id")
                self.created.append([module, entity, new_id, what])
                self.note(step + " (урезанный)", "ОК",
                          "id {} — мешало одно из: {}".format(new_id, ", ".join(extra)))
                return new_id
            self.note(step + " (урезанный)", "ОШИБКА", bad2)
            return None
        if bad:
            self.note(step, "ОШИБКА", bad)
            return None
        new_id = (body.get("response") or {}).get("id")
        self.created.append([module, entity, new_id, what])
        self.note(step, "ОК", "id {}".format(new_id))
        return new_id

    def save(self):
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"created": self.created,
                       "at": datetime.datetime.now().isoformat(timespec="seconds")},
                      fh, ensure_ascii=False, indent=2)

    def summary(self):
        print("\nСВОДКА ПО ЦИКЛУ")
        print("-" * 72)
        width = max(len(s[0]) for s in self.steps) if self.steps else 20
        for step, status, detail in self.steps:
            print("  {:<{w}}  {:<8} {}".format(step, status, detail, w=width))
        ok = sum(1 for s in self.steps if s[1] == "ОК")
        bad = sum(1 for s in self.steps if s[1] == "ОШИБКА")
        print("-" * 72)
        if not self.apply:
            print("  Сухой прогон. Запустите с --apply.")
        elif bad == 0:
            print("  Цикл проходит целиком: {} шагов без ошибок.".format(ok))
            print("  Критерий неуспеха №4 снят — функциональности хватает.")
        else:
            print("  Пройдено {}, сломалось {}. Разрывы видны выше.".format(ok, bad))
            print("  Эти шаги в реальных сделках придётся делать руками.")


# ─────────────────────────────────────────────────────────────── цикл

def run_cycle(tok, me_id, org_id, run):
    ids = {}
    print("\nСправочники")
    for key, name in WANT.items():
        module, entity, field = {
            "pipeline":      ("crm", "pipeline", "name"),
            "stage":         ("crm", "pipeline_stage", "name"),
            "board":         ("st", "project_types", "name"),
            "board_stage":   ("st", "stages", "name"),
            "business_line": ("fin", "business_line", "name"),
            "act_template":  ("finacts", "templates", "name"),
            "act_status":    ("finacts", "status", "name"),
            "unit":          ("products", "units", "name"),
            "category":      ("fin", "categories", "name"),
        }[key]
        found, bad = find_id(tok, module, entity, name, field)
        ids[key] = found
        print("  {:<16} {:<34} {}".format(
            key, name, "id {}".format(found) if found else "НЕ НАЙДЕНО ({})".format(bad)))

    print("\nЦикл")

    # 1. Контрагент
    account_id = run.make(tok, "crm", "account", {
        "type": 1,
        "name": "{} — заказчик".format(MARK),
        "VAT": "7700000000",
        "account_category_id": 1,
        "industry_id": 1,
        "owner_id": me_id,
    }, "1. Контрагент", "тестовый контрагент")

    # 2. Сделка
    lead_id = run.make(tok, "crm", "lead", {
        "name": "{} — канал связи".format(MARK),
        "budget": SUM_NET,
        "pipeline_id": ids.get("pipeline") or 1,
        "pipeline_stage_id": ids.get("stage") or 1,
        "assignee_id": me_id,
        "active": 1,
        "start_date": TODAY.isoformat(),
        "deadline": (TODAY + datetime.timedelta(days=30)).isoformat(),
        "contact_company": "{} — заказчик".format(MARK),
    }, "2. Сделка", "тестовая сделка")

    # 2a. Привязка сделки к контрагенту
    if run.apply and lead_id and account_id:
        body = call(tok, "crm", "lead_accounts", "create",
                    data={"lead_id": lead_id, "account_id": account_id,
                          "account_type": 1})
        bad = problem(body)
        if bad:
            run.note("2a. Сделка ↔ контрагент", "ОШИБКА", bad)
        else:
            rid = (body.get("response") or {}).get("id")
            run.created.append(["crm", "lead_accounts", rid, "связь сделки и контрагента"])
            run.note("2a. Сделка ↔ контрагент", "ОК", "id {}".format(rid))
    else:
        run.note("2a. Сделка ↔ контрагент", "ПРОПУСК", "нужны оба объекта")

    # 3. КП
    estimate_id = run.make(tok, "fin", "estimate", {
        "customer_id": account_id or 0,
        "customer_name": "{} — заказчик".format(MARK),
        "org_id": org_id,
        "assignee_id": me_id,
        "invoice_date": TODAY.isoformat(),
        "expire_date": (TODAY + datetime.timedelta(days=14)).isoformat(),
        "sub_total": SUM_NET,
        "total": SUM_NET,
        "module": "crm", "model": "leads", "model_id": lead_id or 0,
    }, "3. КП", "тестовое предложение")

    if estimate_id:
        run.make(tok, "fin", "estimate_item", {
            "estimate_id": estimate_id,
            "name": "Абонентская плата за канал связи",
            "quantity": 1, "unit_id": ids.get("unit") or 1,
            "unit_price": SUM_NET, "total": SUM_NET, "ordering": 1,
        }, "3a. Позиция КП", "позиция предложения")
    else:
        run.note("3a. Позиция КП", "ПРОПУСК", "нет предложения")

    # 4. Счёт
    invoice_id = run.make(tok, "fin", "invoice", {
        "customer_id": account_id or 0,
        "org_id": org_id,
        "assignee_id": me_id,
        "invoice_date": TODAY.isoformat(),
        "due_date": (TODAY + datetime.timedelta(days=10)).isoformat(),
        "sub_total": SUM_NET,
        "total": SUM_NET,
        "status_id": 10,
        "category_id": ids.get("category") or 0,
        "estimate_id": estimate_id or 0,
        "manager_id": me_id,
        "module": "crm", "model": "leads", "model_id": lead_id or 0,
    }, "4. Счёт", "тестовый счёт", minimal={
        "customer_id": account_id or 0,
        "org_id": org_id,
        "invoice_date": TODAY.isoformat(),
        "total": SUM_NET,
    })

    if invoice_id:
        run.make(tok, "fin", "invoice_item", {
            "invoice_id": invoice_id,
            "name": "Абонентская плата за канал связи",
            "quantity": 1, "unit_id": ids.get("unit") or 1,
            "unit_price": SUM_NET, "total": SUM_NET, "ordering": 1,
        }, "4a. Позиция счёта", "позиция счёта")
    else:
        run.note("4a. Позиция счёта", "ПРОПУСК", "нет счёта")

    # 5. Проект — режим финансов 20, иначе план-факт не соберётся
    project_id = run.make(tok, "st", "projects", {
        "name": "{} — подключение канала".format(MARK),
        "manager_id": me_id,
        "customer_id": account_id or 0,
        "crm_lead_id": lead_id or 0,
        "project_type_id": ids.get("board") or 0,
        "stage_id": ids.get("board_stage") or 0,
        "startdate": TODAY.isoformat(),
        "enddate": (TODAY + datetime.timedelta(days=60)).isoformat(),
        "billing_type": 20,
        "priority": 2,
    }, "5. Проект (billing_type=20)", "тестовый проект")

    # 5a. Плановая выручка — работает только при billing_type=20
    if project_id:
        run.make(tok, "st", "project_money_stage", {
            "project_id": project_id,
            "name": "Оплата за первый месяц",
            "total": SUM_NET,
            "org_id": org_id,
            "manager_id": me_id,
            "plan_paid_date": (TODAY + datetime.timedelta(days=30)).isoformat(),
            "ordering": 1,
        }, "5a. Плановая выручка", "плановый этап оплаты")
    else:
        run.note("5a. Плановая выручка", "ПРОПУСК", "нет проекта")

    # 6. Акт
    act_id = run.make(tok, "finacts", "acts", {
        "org_id": org_id,
        "crm_account_id": account_id or 0,
        "business_line_id": ids.get("business_line") or 0,
        "manager_id": me_id,
        "date": TODAY.isoformat(),
        "sub_total": SUM_NET,
        "total": SUM_NET,
        "status_id": ids.get("act_status") or 2,
        "template_id": ids.get("act_template") or 1,
    }, "6. Акт", "тестовый акт")

    if act_id:
        run.make(tok, "finacts", "items", {
            "act_id": act_id,
            "name": "Абонентская плата за канал связи",
            "quantity": 1, "unit_id": ids.get("unit") or 1,
            "unit_price": SUM_NET, "total": SUM_NET, "ordering": 1,
        }, "6a. Позиция акта", "позиция акта")

        if project_id:
            run.make(tok, "finacts", "relations", {
                "act_id": act_id, "module": "st",
                "model": "project", "model_id": project_id,
            }, "6b. Акт ↔ проект", "связь акта и проекта")
        else:
            run.note("6b. Акт ↔ проект", "ПРОПУСК", "нет проекта")
    else:
        run.note("6a. Позиция акта", "ПРОПУСК", "нет акта")
        run.note("6b. Акт ↔ проект", "ПРОПУСК", "нет акта")


def cleanup(tok):
    if not os.path.exists(STATE_FILE):
        sys.exit("Нет {} — нечего убирать.".format(STATE_FILE))
    with open(STATE_FILE, encoding="utf-8") as fh:
        created = json.load(fh).get("created") or []
    if not created:
        sys.exit("В файле состояния пусто.")

    print("Удаление в обратном порядке\n")
    left = []
    for module, entity, rec_id, what in reversed(created):
        body = call(tok, module, entity, "delete/{}".format(rec_id))
        bad = problem(body)
        print("  {:<28} id {:<8} {}".format(
            what, rec_id, "ошибка: " + bad if bad else "удалено"))
        if bad:
            left.append([module, entity, rec_id, what])

    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"created": list(reversed(left))}, fh, ensure_ascii=False, indent=2)
    print("\nОсталось неудалённым: {}".format(len(left)))


def main():
    ap = argparse.ArgumentParser(description="Сквозная проверка цикла в Aspro.Cloud")
    ap.add_argument("--apply", action="store_true", help="реально провести цикл")
    ap.add_argument("--cleanup", action="store_true", help="убрать созданное и выйти")
    args = ap.parse_args()

    tok = token()
    if args.cleanup:
        cleanup(tok)
        return

    body = call(tok, "core", "user", "get")
    bad = problem(body)
    if bad:
        sys.exit("Токен не работает: {}\nОбновите: aspro_oauth.py refresh ...".format(bad))
    me = body.get("response") or {}
    me_id = me.get("id")

    org_id, org_err = find_id(tok, "fin", "organization", "Норд Лайн")
    if not org_id:
        print("Организация «Норд Лайн» не найдена ({}), беру id 1".format(org_err))
        org_id = 1

    print("Аккаунт : {}".format(API_BASE))
    print("От имени: {} (id {})".format(me.get("name") or me.get("username"), me_id))
    print("Режим   : {}".format(
        "ПРИМЕНЕНИЕ — будут созданы тестовые записи" if args.apply
        else "сухой прогон — ничего не создаётся"))
    print("Метка   : записи называются «{} …» — их легко найти и убрать".format(MARK))

    run = Run(args.apply)
    run_cycle(tok, me_id, org_id, run)
    if args.apply:
        run.save()
    run.summary()

    if args.apply and run.created:
        print("\nСоздано записей: {}. Убрать всё: python3 {} --cleanup".format(
            len(run.created), os.path.basename(__file__)))


if __name__ == "__main__":
    main()
