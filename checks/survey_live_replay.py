"""Replay previously fetched public pages through a disposable local SQL pipeline."""
import json
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'work/python-packages'))
os.environ['DATABASE_URL']='postgresql://postgres@127.0.0.1:55432/postgres?sslmode=disable&connect_timeout=8'
import db
from collector import main as collector
from worker import main as worker
from bot import main as bot


def run():
    from psycopg import sql
    snapshot_path=ROOT/'work/survey-effective-results.json'
    if not snapshot_path.exists():
        snapshot_path=ROOT/'work/survey-source-results.json'
    snapshots=json.loads(snapshot_path.read_text(encoding='utf-8'))
    by_url={row['url']:row for row in snapshots}
    expected=sum(len(row['items']) for row in snapshots)
    assert expected>0,'Fetch the public preview first'
    original_connect=db.connect
    schema='public_'+uuid.uuid4().hex
    with original_connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def isolated():
        conn=original_connect()
        # Avoid backend-shared prepared-statement names in the local socket bridge.
        conn.prepare_threshold=None
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        return conn
    db.connect=isolated
    with db.connect() as conn:
        db.init_schema(conn)
        conn.execute('UPDATE sources SET active=FALSE')
        conn.execute("INSERT INTO subscribers(chat_id,name,started_at) VALUES (1,'LOCAL TEST ONLY',now()-interval '1 day')")
    def replay(source,known=()):
        snapshot=by_url[source['url']]
        return {key:snapshot[key] for key in ('items','checked','errors','discovered')}
    with patch.object(collector,'read_source',replay):
        collector.main()
    messages=[]
    with db.connect() as conn:
        worker.process_new(conn)
        findings=conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]
        assert findings==expected,(findings,expected)
        with patch.object(bot,'say',lambda chat,text,button=True:messages.append(text)):
            for _ in range((findings+2)//3):
                conn.execute("UPDATE finding_deliveries SET sent_at=now()-interval '2 hours'")
                bot.send_new(conn)
        delivered=conn.execute('SELECT count(*) FROM finding_deliveries').fetchone()[0]
        assert delivered==findings,(delivered,findings)
        assert all('НОВОЕ' in message and 'Дата публикации:' in message for message in messages)
    # Force a new check with the exact same snapshots, ignoring cache on purpose.
    with patch.object(collector,'read_source',replay):
        collector.main()
    with db.connect() as conn:
        worker.process_new(conn)
        assert conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]==findings
        conn.execute("UPDATE finding_deliveries SET sent_at=now()-interval '2 hours'")
        with patch.object(bot,'say') as send:
            bot.send_new(conn)
            send.assert_not_called()
    result=dict(public_materials=findings,local_message_previews=len(messages),repeat_messages=0,
        public_sources_with_materials=sum(bool(row['items']) for row in snapshots),
        telegram='never called',pipeline='public snapshots -> local SQL -> worker -> message formatting',messages=messages)
    (ROOT/'work/survey-live-pipeline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='messages'},ensure_ascii=False))


if __name__=='__main__':
    run()
