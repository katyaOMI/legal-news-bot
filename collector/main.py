"""Daily official publications, with persistent source health and metadata history."""
import hashlib
import json
from datetime import datetime, timezone, timedelta

import db
from legal_news.core import MOSCOW, publications, today

from collector.feeds import read_rss, relevant_topics, dedup_key
from collector.survey import read_source, sources as survey_sources

SERVICE = 'collector'


def health_event(conn, source_id, kind, body):
    conn.execute('INSERT INTO source_events(source_id,kind,body) VALUES (%s,%s,%s)',
                 (source_id, kind, body))


def year_start(now=None):
    now = now or datetime.now(timezone.utc)
    local_year = now.astimezone(MOSCOW).year
    return datetime(local_year, 1, 1, tzinfo=MOSCOW).astimezone(timezone.utc)


def is_backfill(item, monitoring_started_at):
    value = item.get('published_at')
    if not value:
        return False
    published = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published < monitoring_started_at


def main():
    with db.connect() as conn:
        db.init_schema(conn)
        # Avoid overlapping scheduled collections and duplicate state transitions.
        if not conn.execute('SELECT pg_try_advisory_lock(4208)').fetchone()[0]:
            db.log(SERVICE, 'Предыдущий сбор ещё работает')
            return
        catalog = {source['url']: source for source in survey_sources()}
        # Do not let retired generic blogs remain active in an existing database.
        for (old_id, old_url) in conn.execute("SELECT id,url FROM sources WHERE kind='survey_web'").fetchall():
            if old_url not in catalog:
                conn.execute('UPDATE sources SET active=FALSE WHERE id=%s',(old_id,))
        for source in catalog.values():
            conn.execute("INSERT INTO sources(name,kind,url) VALUES (%s,'survey_web',%s) ON CONFLICT(name) DO UPDATE SET url=EXCLUDED.url,active=TRUE",
                         (source['name'],source['url']))
        run_id = db.start_run(conn, SERVICE)
        total, failures, partial_sources = 0, [], []
        try:
            sources = conn.execute('''SELECT id,name,kind,url,monitoring_started_at,last_success_at,last_error,
                    archive_backfill_completed_at
                FROM sources WHERE active''').fetchall()
            if not sources:
                raise RuntimeError('Нет активных источников')
            for source_id, name, kind, url, started, success, previous_error, backfill_completed in sources:
                try:
                    result = None
                    if kind == 'pravo':
                        start = (year_start() if not backfill_completed else (success or started)).astimezone(MOSCOW).date().isoformat()
                        items = publications(start, today())
                    elif kind == 'legal_rss':
                        # One-time current-year backfill, then one-day overlap for edits.
                        since = year_start() if not backfill_completed else (max(started, success-timedelta(days=1)) if success else started)
                        items = read_rss(url,since)
                    elif kind == 'survey_web':
                        if url not in catalog:
                            raise ValueError('Источник отсутствует в каталоге')
                        known = {row[0] for row in conn.execute("""SELECT url FROM source_pages
                            WHERE source_id=%s AND last_checked_at > now() -
                              CASE WHEN last_error IS NULL THEN interval '7 days' ELSE interval '6 hours' END""",
                            (source_id,)).fetchall()}
                        result = read_source(catalog[url],known=known)
                        items = result['items']
                    else:
                        raise ValueError('Неизвестный источник: ' + kind)
                    new = 0
                    with conn.transaction():
                        if result is not None:
                            for checked in result['checked']:
                                conn.execute("""INSERT INTO source_pages(source_id,url,last_error) VALUES (%s,%s,%s)
                                    ON CONFLICT(source_id,url) DO UPDATE SET last_checked_at=now(),last_error=EXCLUDED.last_error""",
                                    (source_id,checked['url'],checked['error']))
                            conn.execute("""INSERT INTO source_checks(run_id,source_id,discovered,checked,accepted,errors)
                                VALUES (%s,%s,%s,%s,%s,%s)""",(run_id,source_id,result['discovered'],len(result['checked']),len(items),len(result['errors'])))
                        for item in items:
                            backfill = False if kind == 'survey_web' else is_backfill(item, started)
                            topics = item.get('topics') if kind == 'survey_web' else relevant_topics(item['title'],item.get('text',''))
                            if not topics:
                                continue
                            row = conn.execute('''INSERT INTO materials(source_id,run_id,url,title,text,published_at,dedup_key,is_backfill,content_kind,usefulness)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
                                (source_id,run_id,item['url'],item['title'],
                                 item.get('text',''),item['published_at'],item.get('dedup_key') or dedup_key(item['title']),backfill,
                                 item.get('content_kind','news'),item.get('usefulness',''))).fetchone()
                            if row:
                                material_id = row[0]
                                new += 1
                            else:
                                material_id = conn.execute('SELECT id FROM materials WHERE url=%s OR dedup_key=%s ORDER BY id LIMIT 1', (item['url'],item.get('dedup_key') or dedup_key(item['title']))).fetchone()[0]
                                if backfill:
                                    conn.execute('UPDATE materials SET is_backfill=TRUE WHERE id=%s',(material_id,))
                                if kind == 'survey_web':
                                    conn.execute("""UPDATE materials SET title=%s,text=%s,published_at=%s,content_kind=%s,usefulness=%s,status='new'
                                        WHERE id=%s AND (title IS DISTINCT FROM %s OR text IS DISTINCT FROM %s OR published_at IS DISTINCT FROM %s)""",
                                        (item['title'],item['text'],item['published_at'],item['content_kind'],item['usefulness'],material_id,item['title'],item['text'],item['published_at']))
                            conn.execute('INSERT INTO material_mentions(material_id,source_id,url) VALUES (%s,%s,%s) ON CONFLICT(source_id,url) DO NOTHING',(material_id,source_id,item['url']))
                            metadata = json.dumps(item['metadata'],ensure_ascii=False,sort_keys=True)
                            conn.execute('''INSERT INTO material_snapshots(material_id,digest,metadata)
                                VALUES (%s,%s,%s::jsonb) ON CONFLICT(material_id,digest) DO NOTHING''',
                                (material_id,hashlib.sha256(metadata.encode()).hexdigest(),metadata))
                        source_error = 'Не загружено страниц: '+str(len(result['errors'])) if result is not None and result['errors'] else None
                        if result is not None and not result['checked'] and previous_error and not source_error:
                            source_error = previous_error
                        if previous_error and not source_error and kind != 'survey_web':
                            health_event(conn,source_id,'recovery',f'{name}: доступ восстановлен, обновление новостей возобновилось.')
                        if result is None or any(row['error'] is None for row in result['checked']) or not result['errors']:
                            conn.execute('UPDATE sources SET last_success_at=now(),last_error=%s WHERE id=%s', (source_error,source_id))
                        else:
                            conn.execute('UPDATE sources SET last_error=%s WHERE id=%s',(source_error,source_id))
                        if not backfill_completed:
                            conn.execute('UPDATE sources SET archive_backfill_completed_at=now() WHERE id=%s',(source_id,))
                    total += new
                    if result is not None and source_error:
                        partial_sources.append(name)
                    db.progress_run(conn,SERVICE,run_id,total,f'{name}: проверено {len(items)}, новых {new}')
                except Exception as error:
                    (partial_sources if kind == 'survey_web' else failures).append(name)
                    with conn.transaction():
                        if not previous_error and kind != 'survey_web':
                            health_event(conn,source_id,'outage',f'{name}. Дорогие читатели! Часть источников временно недоступна. Новости из остальных источников продолжают поступать. Сообщим, когда обновление восстановится.')
                        conn.execute('UPDATE sources SET last_error=%s WHERE id=%s', (db.redact(error)[:1500],source_id))
                    db.log(SERVICE,f'{name}: проверка не выполнена: {error}')
            if failures:
                raise RuntimeError('Не удалось проверить источники: ' + ', '.join(failures))
            note=f'Новых материалов: {total}'
            if partial_sources:
                note+=f'; дополнительные источники с ошибками: {len(partial_sources)}'
            db.finish_run(conn,SERVICE,run_id,total,note)
        except Exception as error:
            db.fail_run(conn,SERVICE,run_id,error)
            raise
        finally:
            conn.execute('SELECT pg_advisory_unlock(4208)')


if __name__ == '__main__':
    main()
