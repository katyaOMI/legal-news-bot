"""Manual local integration check. Never connects to production or sends Telegram."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'work/python-packages'))
# Intentionally fixed to the disposable local PostgreSQL test engine.
os.environ['DATABASE_URL']='postgresql://postgres@127.0.0.1:55432/postgres?sslmode=disable&connect_timeout=8'
import db
from collector import main as collector
from collector.feeds import SOURCES, fetch_bytes,parse_rss
from worker import main as worker
from bot import main as bot


def run():
    # A fresh schema makes reruns independent without deleting any prior test data.
    from psycopg import sql
    original_connect=db.connect
    schema='check_'+uuid.uuid4().hex
    with original_connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def isolated_connect():
        conn=original_connect()
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        return conn
    db.connect=isolated_connect
    saved={}
    def fetch_and_save(url,since=None):
        body=fetch_bytes(url)
        saved[url]=body
        return parse_rss(body,since)
    with db.connect() as conn:
        db.init_schema(conn)
        # The sample uses today's already published items only in the test store.
        conn.execute("UPDATE sources SET monitoring_started_at=date_trunc('day',now() AT TIME ZONE 'Europe/Moscow') AT TIME ZONE 'Europe/Moscow' WHERE kind='legal_rss'")
        conn.execute("INSERT INTO subscribers(chat_id,name,started_at) VALUES (1,'LOCAL TEST ONLY',now()-interval '1 day') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO sources(name,kind,url) VALUES ('OLD AI TEST','hn','https://example.invalid/old-ai') ON CONFLICT DO NOTHING")
        source=conn.execute("SELECT id FROM sources WHERE name='OLD AI TEST'").fetchone()[0]
        conn.execute("INSERT INTO materials(source_id,url,title) VALUES (%s,'https://example.invalid/old-ai','Old AI finding') ON CONFLICT DO NOTHING",(source,))
        old_id=conn.execute("SELECT id FROM materials WHERE url='https://example.invalid/old-ai'").fetchone()[0]
        conn.execute("INSERT INTO findings(material_id,entity,summary,model,prompt_version) VALUES (%s,'AI','Old AI finding','old','old') ON CONFLICT DO NOTHING",(old_id,))
    with patch.object(collector,'read_rss',fetch_and_save):
        collector.main()
    with db.connect() as conn:
        before=conn.execute('SELECT count(*) FROM materials WHERE source_id IN (SELECT id FROM sources WHERE kind=\'legal_rss\')').fetchone()[0]
        assert before>0,'No relevant publication in current feeds; inspect live results'
        worker.process_new(conn)
        findings=conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]
        mentions=conn.execute('SELECT count(*) FROM material_mentions').fetchone()[0]
        assert findings>0 and mentions>=findings
        delivered=[]
        with patch.object(bot,'say',lambda chat,text,button=True:delivered.append(dict(chat=chat,text=text))):
            bot.send_new(conn)
            first_count=len(delivered)
            bot.send_new(conn)
        assert len(delivered)==first_count>0
        assert all('Old AI finding' not in item['text'] for item in delivered)
        sample=dict(before=before,findings=findings,mentions=mentions,messages=delivered,
            saved_subscribers=conn.execute('SELECT count(*) FROM subscribers').fetchone()[0])
    # Repeat a real HTTP collection; unchanged publications must not duplicate cards.
    with patch.object(collector,'read_rss',fetch_and_save):
        collector.main()
    with db.connect() as conn:
        worker.process_new(conn)
        assert conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]==findings
        with patch.object(bot,'say') as send:
            bot.send_new(conn)
            send.assert_not_called()
        sample['duplicate_run_new_findings']=0
    # Replay the downloaded RSS to test persistent outage/recovery transitions.
    def replay(url,since=None):
        return parse_rss(saved[url],since)
    def broken(url,since=None):
        if url==SOURCES[0][1]: raise TimeoutError('simulated source failure')
        return replay(url,since)
    for _ in range(2):
        try:
            with patch.object(collector,'read_rss',broken): collector.main()
        except RuntimeError: pass
        else: raise AssertionError('Failed source was incorrectly marked successful')
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM source_events').fetchone()[0]==1
    with patch.object(collector,'read_rss',replay): collector.main()
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM source_events').fetchone()[0]==2
        alerts=[]
        with patch.object(bot,'say',lambda chat,text,button=True:alerts.append(text)):
            bot.send_source_events(conn)
            bot.send_source_events(conn)
        assert len(alerts)==2
        sample['outage_recovery_messages']=len(alerts)
        sample['source_checks']=[dict(name=name,status='OK',items=len(parse_rss(saved[url]))) for name,url in SOURCES]
        sample['snapshots']=conn.execute('SELECT count(*) FROM material_snapshots').fetchone()[0]
        sample['engine']=conn.execute('SELECT version()').fetchone()[0]
        sample['checked_at']=datetime.now(timezone.utc).isoformat()
        sample['telegram']='preview only; no real messages sent'
        sample['test_schema']=schema
    (ROOT/'work/live-check-result.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(sample,ensure_ascii=False,indent=2))


if __name__=='__main__': run()
