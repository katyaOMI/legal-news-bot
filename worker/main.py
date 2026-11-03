"""Free first stage: classify official publication titles, without model calls."""
import time

import db
from collector.feeds import relevant_topics

SERVICE = 'worker'
POLL_SECONDS = 10


def classify(title, description=''):
    topics = relevant_topics(title,description)
    if not topics:
        return None
    # Do not present a title-based match as a verified legal interpretation.
    summary = title[:900] + ('…' if len(title) > 900 else '')
    return ', '.join(topics), summary


def process_new(conn):
    pending = conn.execute('''SELECT m.id,m.title,m.text FROM materials m JOIN sources s ON s.id=m.source_id
        WHERE m.status='new' AND s.kind IN ('pravo','legal_rss') AND s.active ORDER BY m.id''').fetchall()
    if not pending:
        return
    run_id = db.start_run(conn,SERVICE)
    found = 0
    try:
        for material_id,title,description in pending:
            result = classify(title,description or '')
            with conn.transaction():
                if result:
                    topic,summary = result
                    conn.execute('''INSERT INTO findings(material_id,entity,summary,model,prompt_version)
                        VALUES (%s,%s,%s,'rules-no-model','legal-title-v1')
                        ON CONFLICT(material_id) DO NOTHING''',(material_id,topic,summary))
                    found += 1
                conn.execute('UPDATE materials SET status=%s WHERE id=%s',
                    ('done' if result else 'skipped',material_id))
        db.finish_run(conn,SERVICE,run_id,found,f'Отобрано правовых публикаций: {found}')
    except Exception as error:
        db.fail_run(conn,SERVICE,run_id,error)
        raise


def main():
    while True:
        try:
            with db.connect() as conn:
                db.init_schema(conn)
                while True:
                    process_new(conn)
                    time.sleep(POLL_SECONDS)
        except Exception as error:
            db.log(SERVICE,f'Ошибка обработки: {error}')
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
