"""Daily official publications, with persistent source health and metadata history."""
import hashlib
import json
from datetime import datetime, timezone, timedelta

import db
from legal_news.core import MOSCOW, publications, today

from collector.feeds import read_rss, relevant_topics, dedup_key

SERVICE = 'collector'


def health_event(conn, source_id, kind, body):
    conn.execute('INSERT INTO source_events(source_id,kind,body) VALUES (%s,%s,%s)',
                 (source_id, kind, body))


def main():
    with db.connect() as conn:
        db.init_schema(conn)
        # Avoid overlapping scheduled collections and duplicate state transitions.
        if not conn.execute('SELECT pg_try_advisory_lock(4208)').fetchone()[0]:
            db.log(SERVICE, 'Предыдущий сбор ещё работает')
            return
        run_id = db.start_run(conn, SERVICE)
        total, failures = 0, []
        try:
            sources = conn.execute('''SELECT id,name,kind,url,monitoring_started_at,last_success_at,last_error
                FROM sources WHERE active''').fetchall()
            if not sources:
                raise RuntimeError('Нет активных источников')
            for source_id, name, kind, url, started, success, previous_error in sources:
                try:
                    if kind == 'pravo':
                        start = (success or started).astimezone(MOSCOW).date().isoformat()
                        items = publications(start, today())
                    elif kind == 'legal_rss':
                        # A day of overlap catches feed edits without creating duplicate cards.
                        since = max(started, success-timedelta(days=1)) if success else started
                        items = read_rss(url,since)
                    else:
                        raise ValueError('Неизвестный источник: ' + kind)
                    new = 0
                    with conn.transaction():
                        for item in items:
                            topics = relevant_topics(item['title'],item.get('text',''))
                            if not topics:
                                continue
                            row = conn.execute('''INSERT INTO materials(source_id,run_id,url,title,text,published_at,dedup_key)
                                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
                                (source_id,run_id,item['url'],item['title'],
                                 item.get('text',''),item['published_at'],item.get('dedup_key') or dedup_key(item['title']))).fetchone()
                            if row:
                                material_id = row[0]
                                new += 1
                            else:
                                material_id = conn.execute('SELECT id FROM materials WHERE url=%s OR dedup_key=%s ORDER BY id LIMIT 1', (item['url'],item.get('dedup_key') or dedup_key(item['title']))).fetchone()[0]
                            conn.execute('INSERT INTO material_mentions(material_id,source_id,url) VALUES (%s,%s,%s) ON CONFLICT(source_id,url) DO NOTHING',(material_id,source_id,item['url']))
                            metadata = json.dumps(item['metadata'],ensure_ascii=False,sort_keys=True)
                            conn.execute('''INSERT INTO material_snapshots(material_id,digest,metadata)
                                VALUES (%s,%s,%s::jsonb) ON CONFLICT(material_id,digest) DO NOTHING''',
                                (material_id,hashlib.sha256(metadata.encode()).hexdigest(),metadata))
                        if previous_error:
                            health_event(conn,source_id,'recovery',f'{name}: доступ восстановлен, обновление новостей возобновилось.')
                        conn.execute('UPDATE sources SET last_success_at=now(),last_error=NULL WHERE id=%s', (source_id,))
                    total += new
                    db.progress_run(conn,SERVICE,run_id,total,f'{name}: проверено {len(items)}, новых {new}')
                except Exception as error:
                    failures.append(name)
                    with conn.transaction():
                        if not previous_error:
                            health_event(conn,source_id,'outage',f'{name}. Дорогие читатели! Часть источников временно недоступна. Новости из остальных источников продолжают поступать. Сообщим, когда обновление восстановится.')
                        conn.execute('UPDATE sources SET last_error=%s WHERE id=%s', (str(error)[:1500],source_id))
                    db.log(SERVICE,f'{name}: проверка не выполнена: {error}')
            if failures:
                raise RuntimeError('Не удалось проверить источники: ' + ', '.join(failures))
            db.finish_run(conn,SERVICE,run_id,total,f'Новых правовых публикаций: {total}')
        except Exception as error:
            db.fail_run(conn,SERVICE,run_id,error)
            raise
        finally:
            conn.execute('SELECT pg_advisory_unlock(4208)')


if __name__ == '__main__':
    main()
