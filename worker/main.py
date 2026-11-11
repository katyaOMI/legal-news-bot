"""Free first stage: classify official publication titles, without model calls."""
import time

import db
from collector.feeds import relevant_topics
from collector.survey import classify_material
from legal_news.relevance import evaluate, VERSION

SERVICE = 'worker'
POLL_SECONDS = 10


def classify(title, description='', kind='legal_rss'):
    verdict = classify_material(title,description)
    topics = verdict['topics'] if verdict else []
    if not topics:
        return None
    # Do not present a title-based match as a verified legal interpretation.
    summary = title[:900] + ('…' if len(title) > 900 else '')
    return ', '.join(topics), summary


def process_new(conn):
    pending = conn.execute('''SELECT m.id,m.title,m.text,s.kind FROM materials m JOIN sources s ON s.id=m.source_id
        WHERE (m.status='new' OR m.evaluation_version<>%s)
        AND s.kind IN ('pravo','legal_rss','survey_web') AND s.active ORDER BY m.id LIMIT 200''',(VERSION,)).fetchall()
    if not pending:
        return
    run_id = db.start_run(conn,SERVICE)
    found = 0
    try:
        for material_id,title,description,kind in pending:
            result = classify(title,description or '',kind)
            decision = evaluate(title,description or '')
            with conn.transaction():
                if result:
                    topic,summary = result
                    conn.execute('''INSERT INTO findings(material_id,entity,summary,model,prompt_version)
                        VALUES (%s,%s,%s,'rules-no-model',%s)
                        ON CONFLICT(material_id) DO UPDATE SET entity=EXCLUDED.entity,summary=EXCLUDED.summary,prompt_version=EXCLUDED.prompt_version''',
                        (material_id,topic,summary,VERSION))
                    found += 1
                conn.execute('''UPDATE materials SET status=%s,evaluation_version=%s,accepted=%s,
                    evaluation_reason=%s,risk_excerpt=%s,audience=%s,content_kind=%s,usefulness=%s,risk_priority=%s WHERE id=%s''',
                    ('done' if result else 'skipped',VERSION,bool(result),decision['reason'],
                     decision.get('risk',''),decision.get('audience',''),decision.get('content_kind','article'),
                     decision.get('usefulness',''),decision.get('priority',0),material_id))
        db.finish_run(conn,SERVICE,run_id,found,f'Обработано подходящих материалов: {found}')
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
