"""Privacy-risk bot: daily Moscow digest, manual commands and optional web UI."""

import html
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as datetime_time, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import db

SERVICE = "bot"
HERE = Path(__file__).resolve().parent
SEND_EVERY_SECONDS = 60
LATEST = 3
MOSCOW = ZoneInfo("Europe/Moscow")

COMMANDS = [
    {"command": "start", "description": "подписаться на находки"},
    {"command": "status", "description": "состояние обновления новостей"},
    {"command": "latest", "description": "последние новости и материалы"},
    {"command": "stop", "description": "отписаться от новостей"},
]


def setting(name):
    return os.environ.get(name, "").strip()


def page_url():
    if setting("ENABLE_MINI_APP") != "1":
        return ""
    """Address of the Mini App: set by hand or taken from the platform (Railway, Coolify)."""
    url = (
        setting("MINI_APP_URL")
        or setting("RAILWAY_PUBLIC_DOMAIN")
        or setting("COOLIFY_URL")
    )
    if not url:
        return ""
    url = url.split(",")[0].strip()  # Coolify lists several domains separated by commas
    if not url.startswith("http"):
        url = f"https://{url}"
    return url.rstrip("/") + "/"


# ---------- Telegram ----------


def api(method, payload=None, timeout=20):
    base = setting("TELEGRAM_API") or "https://api.telegram.org"
    request = urllib.request.Request(
        f"{base}/bot{setting('TELEGRAM_TOKEN')}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["result"]


def say(chat_id, text, button=True):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if button and page_url():
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "Открыть ленту", "web_app": {"url": page_url()}}]
            ]
        }
    api("sendMessage", payload)


def finding_lines(rows, new=False, now=None):
    # `new` is retained for callers but cannot override publication freshness.
    now = now or datetime.now(timezone.utc)
    kinds={'news':'Новость','article':'Разъяснение','guide':'Материал','review':'Обзор рисков','document':'Документ'}
    lines=[]
    for row in rows:
        summary, entity, url, source = row[:4]
        published, kind, benefit = row[4:7] if len(row)>=7 else (None,'article','')
        risk, audience = row[7:9] if len(row)>=9 else ('','')
        if isinstance(published,str):
            try:
                published=datetime.fromisoformat(published.replace('Z','+00:00'))
            except ValueError:
                published=None
        if published is not None and not isinstance(published,datetime):
            published=datetime.combine(published,datetime_time.min,MOSCOW)
        if published is not None and published.tzinfo is None:
            published=published.replace(tzinfo=MOSCOW)
        fresh=kind=='news' and published is not None and 0 <= (now-published).total_seconds() <= 72*3600
        label=('НОВОЕ · ' if fresh else '')+kinds.get(kind,'Материал')
        line='<b>'+label+'</b>\n• <a href="'+html.escape(url,quote=True)+'">'+html.escape(summary[:900])+'</a>\n'+html.escape(source)
        if entity:
            line+=' · '+html.escape(entity[:160])
        if risk:
            line+='\nЧто произошло / риск: '+html.escape(risk[:650])
        if audience:
            line+='\nКого касается: '+html.escape(audience[:250])
        if benefit:
            line+='\nЧто проверить: '+html.escape(benefit[:450])
        line+='\nДата публикации: '+(published.astimezone(MOSCOW).strftime('%d.%m.%Y') if published else 'не указана')
        lines.append(line)
    return lines


def batches(rows, size=LATEST):
    return [rows[index:index+size] for index in range(0,len(rows),size)]


def setup(conn):
    api("setMyCommands", {"commands": COMMANDS})
    # Clear an old web-app menu: news are delivered in the chat.
    api("setChatMenuButton", {"menu_button": {"type": "commands"}})
    me = api("getMe")
    db.log(SERVICE, f"@{me['username']}: правовые новости, доставка в чат")


def send_source_events(conn):
    rows = conn.execute("""SELECT e.id,s.chat_id,e.body FROM source_events e
        CROSS JOIN subscribers s WHERE e.created_at >= s.started_at
        AND NOT EXISTS (SELECT 1 FROM source_event_deliveries d
            WHERE d.event_id=e.id AND d.chat_id=s.chat_id)
        ORDER BY e.id,s.chat_id""").fetchall()
    blocked = set()
    for event_id,chat_id,body in rows:
        if chat_id in blocked:
            continue
        try:
            say(chat_id,body,button=False)
            conn.execute("""INSERT INTO source_event_deliveries(event_id,chat_id)
                VALUES (%s,%s) ON CONFLICT DO NOTHING""",(event_id,chat_id))
        except urllib.error.HTTPError as error:
            if error.code == 403:
                conn.execute("DELETE FROM subscribers WHERE chat_id=%s",(chat_id,))
            blocked.add(chat_id)
            db.log(SERVICE,f"Уведомление подписчику не доставлено, код {error.code}")


def send_new(conn, now=None):
    from bot.delivery import send_daily
    return send_daily(conn,say,finding_lines,now=now)


def send_weekly_digest(conn, now=None):
    """Retired: all automatic materials use the single daily window."""
    return


def status_text(conn):
    lines = []
    for service, name in (
        ("collector", "Сбор"),
        ("worker", "Разбор"),
    ):
        run = conn.execute(
            "SELECT started_at, status, note FROM runs WHERE service = %s ORDER BY id DESC LIMIT 1",
            (service,),
        ).fetchone()
        if run:
            state = {"finished": "завершен", "failed": "упал"}.get(run[1], "идет")
            lines.append(
                f"{name}: {run[0].astimezone(timezone.utc):%d.%m %H:%M} UTC, {state}. {html.escape(db.redact(run[2] or ''))}"
            )
        else:
            lines.append(f"{name}: еще не было")
    findings, subscribers = conn.execute(
        "SELECT (SELECT count(*) FROM legal_findings), (SELECT count(*) FROM subscribers)"
    ).fetchone()
    lines.append(f"\nВсего находок {findings}, подписчиков {subscribers}.")
    active,errors=conn.execute("SELECT count(*),count(*) FILTER (WHERE last_error IS NOT NULL) FROM sources WHERE active").fetchone()
    lines.append(f'Источников активно: {active}, с ошибками последней проверки: {errors}.')
    checks=conn.execute("""SELECT s.name,c.discovered,c.checked,c.accepted,c.errors FROM source_checks c
        JOIN sources s ON s.id=c.source_id
        WHERE c.run_id=(SELECT max(run_id) FROM source_checks) ORDER BY s.name""").fetchall()
    for name,discovered,checked,accepted,failed in checks:
        lines.append(f'{html.escape(name)}: найдено ссылок {discovered}, прочитано {checked}, подошло {accepted}, ошибок {failed}.')
    last=conn.execute('SELECT max(sent_at) FROM daily_digest_items WHERE sent_at IS NOT NULL').fetchone()[0]
    lines.append('Последняя реальная доставка: '+(last.astimezone(MOSCOW).strftime('%d.%m %H:%M МСК') if last else 'ещё не было по новой схеме'))
    state=conn.execute('SELECT note FROM digest_state WHERE id=1').fetchone()
    if state:
        lines.append('Проверка доставки: '+html.escape(db.redact(state[0])))
    from bot.delivery import next_window, delivery_day
    current=datetime.now(timezone.utc)
    if delivery_day(current):
        lines.append('Сейчас окно доставки: до 13:15 МСК')
    else:
        lines.append('Ближайшая подборка: '+next_window(current).strftime('%d.%m %H:%M МСК'))
    return "\n".join(lines)


def handle(conn, message):
    chat = message["chat"]
    if chat.get("type") != "private":
        return
    words = (message.get("text") or "").split()
    command = words[0].split("@")[0] if words else ""
    if command == "/start":
        # Delivery needs chat_id only. Do not collect names or usernames.
        name = ""
        conn.execute(
            "INSERT INTO subscribers (chat_id, name) VALUES (%s, %s) ON CONFLICT (chat_id) DO NOTHING",
            (chat["id"], name),
        )
        text = "Вы подписаны на правовые риски, приватность и безопасность онлайн-опросов при работе из России. Подборка ежедневно в 13:00 МСК, до пяти материалов; при задержке — до 13:15, позже только на следующий день. Пустых подборок нет. «НОВОЕ» — только новости с подтверждённой датой не старше 72 часов. Методологию не присылаем."
        text += "\n\n/latest — последние публикации. /status — состояние обновлений. /stop — отписаться."
        say(chat["id"], text)
        db.log(SERVICE, "подписка активирована")
    elif command == "/stop":
        conn.execute("DELETE FROM subscribers WHERE chat_id=%s",(chat["id"],))
        say(chat["id"],"Вы отписались от новостей. /start — подписаться снова.",button=False)
    elif command == "/latest":
        rows = conn.execute("""SELECT f.summary,f.entity,m.url,s.name,m.published_at,m.content_kind,m.usefulness,m.risk_excerpt,m.audience FROM legal_findings f
            JOIN materials m ON m.id=f.material_id JOIN sources s ON s.id=m.source_id
            ORDER BY f.created_at DESC,f.id DESC LIMIT 3""").fetchall()
        text = "<b>Правовые риски: последние материалы</b>\n\n" + "\n\n".join(finding_lines(rows)) if rows else "Подходящих материалов пока нет. /status — состояние сбора и источников."
        say(chat["id"],text,button=False)
    elif command == "/status":
        say(chat["id"], status_text(conn))
    else:
        say(chat["id"], "Команды: /start — подписка, /latest — публикации, /status — обновления, /stop — отписка.")


def poll_forever():
    """Long polling: asks Telegram for new messages and answers them."""
    offset = None
    while True:
        try:
            updates = api(
                "getUpdates",
                {"timeout": 30, "offset": offset, "allowed_updates": ["message"]},
                timeout=40,
            )
            if updates:
                with db.connect() as conn:
                    for update in updates:
                        offset = update["update_id"] + 1
                        if "message" in update:
                            handle(conn, update["message"])
        except urllib.error.HTTPError as error:
            if error.code == 409:
                # Telegram gives updates to one program at a time
                db.log(SERVICE, "409: этот токен уже слушает другая программа, бот ждет, пока она остановится")
            else:
                db.log(SERVICE, f"ошибка связи с Telegram: ответ {error.code}")
            time.sleep(5)
        except Exception as error:
            db.log(SERVICE, f"ошибка связи с Telegram: {error}")
            time.sleep(5)


def send_forever():
    while True:
        try:
            with db.connect() as conn:
                send_new(conn)
        except Exception as error:
            db.log(SERVICE, f"рассылка не прошла: {error}")
        time.sleep(SEND_EVERY_SECONDS)


# ---------- Mini App page ----------


def iso(moment):
    return (
        moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if moment
        else None
    )


def page_data(conn):
    fields = (
        "id",
        "material_id",
        "summary",
        "reason",
        "title",
        "url",
        "date",
        "run_id",
        "source_id",
        "source",
        "source_url",
        "text",
        "sent_at",
        "model",
    )
    findings = []
    for row in conn.execute(
        "SELECT f.id, m.id, f.summary, f.entity, m.title, m.url, coalesce(m.published_at, m.collected_at), m.run_id,"
        " s.id, s.name, s.url, left(m.text, 600), f.sent_at, f.model"
        " FROM legal_findings f JOIN materials m ON m.id = f.material_id JOIN sources s ON s.id = m.source_id"
        " ORDER BY f.id DESC LIMIT 300"
    ):
        finding = dict(zip(fields, row))
        finding["date"], finding["sent_at"] = (
            iso(finding["date"]),
            iso(finding["sent_at"]),
        )
        findings.append(finding)

    run = conn.execute(
        "SELECT id, finished_at, status, items FROM runs WHERE service = 'collector' AND status <> 'started'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    run_info, sources = None, []
    if run:
        statuses = dict(
            conn.execute(
                "SELECT status, count(*) FROM materials WHERE run_id = %s GROUP BY status",
                (run[0],),
            ).fetchall()
        )
        added = sum(statuses.values())
        kept = conn.execute(
            "SELECT count(*) FROM legal_findings f JOIN materials m ON m.id = f.material_id WHERE m.run_id = %s",
            (run[0],),
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT count(*) FROM legal_findings f JOIN materials m ON m.id = f.material_id"
            " WHERE m.run_id = %s AND f.sent_at IS NOT NULL",
            (run[0],),
        ).fetchone()[0]
        run_info = {
            "id": run[0],
            "finished_at": iso(run[1]),
            "status": run[2],
            "added": added,
            "processed": statuses.get("done", 0)
            + statuses.get("skipped", 0)
            + statuses.get("failed", 0),
            "kept": kept,
            "skipped": statuses.get("skipped", 0),
            "failed": statuses.get("failed", 0),
            "waiting": statuses.get("new", 0),
            "sent": sent,
        }
        sources = [
            {"name": name, "new": new}
            for name, new in conn.execute(
                "SELECT s.name, count(m.id) FROM sources s"
                " LEFT JOIN materials m ON m.source_id = s.id AND m.run_id = %s WHERE s.active GROUP BY s.id ORDER BY s.id",
                (run[0],),
            )
        ]
    totals = dict(
        zip(
            ("sources", "materials", "findings", "runs", "subscribers"),
            conn.execute(
                "SELECT (SELECT count(*) FROM sources WHERE active), (SELECT count(*) FROM materials),"
                " (SELECT count(*) FROM legal_findings), (SELECT count(*) FROM runs), (SELECT count(*) FROM subscribers)"
            ).fetchone(),
        )
    )
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "totals": totals,
        "run": run_info,
        "sources": sources,
        "findings": findings,
    }


def render_page(conn):
    page = (
        (HERE / "template.html")
        .read_text(encoding="utf-8")
        .replace("/*BRAND*/", (HERE / "brand.css").read_text(encoding="utf-8"))
    )
    payload = json.dumps(page_data(conn), ensure_ascii=False).replace("</", "<\\/")
    return page.replace("/*DATA*/null/*END*/", payload)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/" and setting("ENABLE_MINI_APP") != "1":
                # The HTTP listener is needed by hosting, not a public database UI.
                body, status, kind = b"bot service is running", 200, "text/plain"
            elif path not in ("/", "/health"):
                body, status, kind = b"not found", 404, "text/plain"
            else:
                with db.connect() as conn:
                    if path == "/health":
                        conn.execute("SELECT 1")
                        body, status, kind = b"ok", 200, "text/plain"
                    else:
                        body, status, kind = render_page(conn).encode("utf-8"), 200, "text/html; charset=utf-8"
        except Exception as error:
            db.log(SERVICE, f"страница не собралась: {error}")
            body, status, kind = b"database unavailable", 503, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    with db.connect() as conn:
        db.init_schema(conn)
        if setting("TELEGRAM_TOKEN"):
            try:
                setup(conn)
            except Exception as error:
                db.log(SERVICE, f"не настроил бота: {error}")
    if setting("TELEGRAM_TOKEN"):
        threading.Thread(target=poll_forever, daemon=True).start()
        threading.Thread(target=send_forever, daemon=True).start()
    else:
        db.log(
            SERVICE,
            "переменная TELEGRAM_TOKEN пустая: бот молчит, работает только лента",
        )
    port = int(os.environ.get("PORT", "8080"))
    db.log(SERVICE, f"лента открыта на порту {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
