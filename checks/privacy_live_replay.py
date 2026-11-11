"""Replay real public-source snapshots locally, including rejected candidates."""
from privacy_pipeline import ROOT, db, process_new, bot, forbid_telegram
from datetime import datetime, timezone
import json
import uuid
from unittest.mock import patch


def run():
    from psycopg import sql
    snapshots=json.loads((ROOT/'work/privacy-source-results.json').read_text(encoding='utf-8'))
    expected_report=json.loads((ROOT/'work/privacy-preview.json').read_text(encoding='utf-8'))
    expected=len(expected_report['accepted'])
    schema='replay_'+uuid.uuid4().hex
    with db.connect() as conn:
        conn.prepare_threshold=None
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        db.init_schema(conn)
        conn.execute('UPDATE sources SET active=FALSE')
        conn.execute("INSERT INTO subscribers(chat_id,started_at) VALUES (1,now()-interval '30 days')")
        for source in snapshots:
            sid=conn.execute('''INSERT INTO sources(name,kind,url,active) VALUES (%s,'survey_web',%s,TRUE)
                ON CONFLICT(name) DO UPDATE SET active=TRUE RETURNING id''',(source['source'],source['url'])).fetchone()[0]
            for item in source['items']:
                conn.execute('''INSERT INTO materials(source_id,title,text,url,published_at)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (sid,item['title'],item.get('text',''),item['url'],item.get('published_at')))
        process_new(conn)
        count=conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]
        assert count==expected,(count,expected)
        when=datetime.now(timezone.utc).replace(hour=10,minute=0,second=0,microsecond=0)
        messages=[]
        with patch.object(bot,'say',lambda chat,text,button=False:messages.append(text)):
            bot.send_new(conn,when)
            bot.send_new(conn,when)
        assert len(messages)==min(expected,5)
        assert len(messages)==len(set(messages))
        assert conn.execute('SELECT count(*) FROM finding_deliveries').fetchone()[0]==len(messages)
        result=dict(accepted=count,rejected=conn.execute('SELECT count(*) FROM materials WHERE NOT accepted').fetchone()[0],
                    messages=len(messages),repeat_messages=0,telegram='never called',messages_preview=messages)
        (ROOT/'work/privacy-live-pipeline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='messages_preview'},ensure_ascii=False))


if __name__=='__main__': run()
