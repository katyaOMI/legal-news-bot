"""Persistent daily batches. No Telegram calls outside the Moscow window."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo('Europe/Moscow')
LIMIT = 5


def clock():
    return datetime.now(timezone.utc)


def delivery_day(now):
    local = now.astimezone(MOSCOW)
    return local.date() if (13, 0) <= (local.hour, local.minute) < (13, 15) else None


def next_window(now):
    local = now.astimezone(MOSCOW)
    day = local.date() + timedelta(days=int((local.hour, local.minute) >= (13, 15)))
    return datetime.combine(day, time(13), MOSCOW)


def note(conn, text):
    conn.execute('''INSERT INTO digest_state(id,note) VALUES (1,%s)
        ON CONFLICT(id) DO UPDATE SET note=EXCLUDED.note,checked_at=now()''',(text,))


def send_daily(conn, send, render, now=None):
    # A real clock is re-read before every outbound part, including slow retries.
    current = (lambda: now) if now is not None else clock
    day = delivery_day(current())
    if day is None:
        return
    if not conn.execute('SELECT pg_try_advisory_lock(4210)').fetchone()[0]:
        return
    try:
        busy = conn.execute('''SELECT EXISTS(SELECT 1 FROM materials m JOIN sources s ON s.id=m.source_id
            WHERE s.active AND s.kind IN ('pravo','legal_rss','survey_web')
            AND (m.status='new' OR m.evaluation_version<>'privacy-risk-v2'))
            OR EXISTS(SELECT 1 FROM runs WHERE service='collector' AND status IN ('started','running')
            AND started_at>now()-interval '30 minutes')''').fetchone()[0]
        if busy:
            note(conn,'Ожидание завершения сбора или переоценки очереди')
            return
        total = 0
        for (chat_id,) in conn.execute('SELECT chat_id FROM subscribers').fetchall():
            if delivery_day(current()) != day:
                break
            existing = conn.execute('SELECT status FROM daily_digest_runs WHERE chat_id=%s AND delivery_date=%s',
                                    (chat_id,day)).fetchone()
            if existing and existing[0] == 'completed':
                continue
            if not existing:
                candidates = conn.execute('''SELECT f.id FROM legal_findings f
                    JOIN materials m ON m.id=f.material_id JOIN sources s ON s.id=m.source_id
                    JOIN subscribers sub ON sub.chat_id=%s
                    WHERE f.created_at>=sub.started_at AND NOT EXISTS(SELECT 1 FROM finding_deliveries d
                        WHERE d.finding_id=f.id AND d.chat_id=sub.chat_id)
                    ORDER BY m.risk_priority DESC,(m.content_kind='news') DESC,(s.kind='pravo') DESC,
                             m.published_at DESC NULLS LAST,f.id LIMIT 5''',
                    (chat_id,)).fetchall()
                if not candidates:
                    continue  # New material may arrive later inside the same window.
                with conn.transaction():
                    conn.execute('INSERT INTO daily_digest_runs(chat_id,delivery_date) VALUES (%s,%s)',(chat_id,day))
                    for position,(finding_id,) in enumerate(candidates):
                        conn.execute('''INSERT INTO daily_digest_items(chat_id,delivery_date,finding_id,position)
                            VALUES (%s,%s,%s,%s)''',(chat_id,day,finding_id,position))
            pending = conn.execute('''SELECT finding_id FROM daily_digest_items
                WHERE chat_id=%s AND delivery_date=%s AND sent_at IS NULL AND NOT cancelled ORDER BY position''',
                (chat_id,day)).fetchall()
            for (finding_id,) in pending:
                if delivery_day(current()) != day:
                    return
                row = conn.execute('''SELECT f.summary,f.entity,m.url,s.name,m.published_at,m.content_kind,
                    m.usefulness,m.risk_excerpt,m.audience FROM legal_findings f
                    JOIN materials m ON m.id=f.material_id JOIN sources s ON s.id=m.source_id
                    WHERE f.id=%s AND NOT EXISTS(SELECT 1 FROM finding_deliveries d
                        WHERE d.finding_id=f.id AND d.chat_id=%s)''',(finding_id,chat_id)).fetchone()
                if not row:
                    conn.execute('''UPDATE daily_digest_items SET cancelled=TRUE
                        WHERE chat_id=%s AND delivery_date=%s AND finding_id=%s''',(chat_id,day,finding_id))
                    continue
                # Every card is a separately acknowledged part (at most five/day).
                text = '<b>Правовые риски и безопасность опросов</b>\n\n' + render([row],now=current())[0]
                if delivery_day(current()) != day:
                    return
                try:
                    send(chat_id,text,button=False)
                except Exception as error:
                    note(conn,'Ошибка доставки: повтор возможен только до 13:15 МСК')
                    # A failed recipient must not block the other subscribers.
                    import urllib.error
                    if isinstance(error,urllib.error.HTTPError) and error.code == 403:
                        conn.execute('DELETE FROM subscribers WHERE chat_id=%s',(chat_id,))
                    break
                with conn.transaction():
                    conn.execute('INSERT INTO finding_deliveries(finding_id,chat_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                                 (finding_id,chat_id))
                    conn.execute('UPDATE findings SET sent_at=coalesce(sent_at,now()) WHERE id=%s',(finding_id,))
                    conn.execute('''UPDATE daily_digest_items SET sent_at=now()
                        WHERE chat_id=%s AND delivery_date=%s AND finding_id=%s''',(chat_id,day,finding_id))
                total += 1
            # A deleted subscriber (403) has already cascaded its daily records.
            remains = conn.execute('''SELECT count(*) FROM daily_digest_items
                WHERE chat_id=%s AND delivery_date=%s AND sent_at IS NULL AND NOT cancelled''',(chat_id,day)).fetchone()[0]
            if remains == 0:
                conn.execute('''UPDATE daily_digest_runs SET status='completed',completed_at=now()
                    WHERE chat_id=%s AND delivery_date=%s''',(chat_id,day))
        if total:
            note(conn,'Доставлено карточек за проход: '+str(total))
        elif not conn.execute('SELECT EXISTS(SELECT 1 FROM daily_digest_runs WHERE delivery_date=%s)',(day,)).fetchone()[0]:
            note(conn,'Подходящих недоставленных материалов нет; пустая подборка не отправлена')
    finally:
        conn.execute('SELECT pg_advisory_unlock(4210)')
