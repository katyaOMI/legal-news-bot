"""Disposable localhost SQL check for queue migration and daily delivery. No Telegram."""
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'work/python-packages')]
os.environ['DATABASE_URL']='postgresql://postgres@127.0.0.1:55432/postgres?sslmode=disable&connect_timeout=8'
import db
from worker.main import process_new
from bot import main as bot
from bot import delivery

def forbid_telegram(*args, **kwargs):
    raise AssertionError('Telegram must never be called by a local check')

bot.api=forbid_telegram


def run():
    from psycopg import sql
    schema='privacy_'+uuid.uuid4().hex
    with db.connect() as conn:
        conn.prepare_threshold=None
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        db.init_schema(conn)
        conn.execute('UPDATE sources SET active=FALSE')
        source=conn.execute("INSERT INTO sources(name,kind,url) VALUES ('LOCAL ONLY','survey_web','https://example.com') RETURNING id").fetchone()[0]
        conn.execute("INSERT INTO subscribers(chat_id,started_at) VALUES (1,now()-interval '30 days')")
        now=datetime.now(timezone.utc)
        when=now.replace(hour=10,minute=0,second=0,microsecond=0)
        def insert(title,index,legacy=False):
            mid=conn.execute('''INSERT INTO materials(source_id,title,text,url,published_at,status)
                VALUES (%s,%s,%s,%s,%s,'done') RETURNING id''',
                (source,title,'Источник разъяснил порядок обработки персональных данных.' if 'персональн' in title else 'Практические советы.',
                 'https://example.com/'+str(index),now-timedelta(hours=1))).fetchone()[0]
            if legacy:
                conn.execute("INSERT INTO findings(material_id,summary,model,prompt_version) VALUES (%s,%s,'old','survey-v1')",(mid,title))
            return mid
        bad=insert('Как составить анкету для исследования',0,True)
        # Existing delivery must survive even though the finding is now rejected.
        old=conn.execute('SELECT id FROM findings WHERE material_id=%s',(bad,)).fetchone()[0]
        conn.execute('INSERT INTO finding_deliveries(finding_id,chat_id) VALUES (%s,1)',(old,))
        for i in range(1,9):
            insert('РКН разъяснил согласие на обработку персональных данных: пример '+str(i),i,True)
        assert conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]==0
        process_new(conn)
        assert conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]==8
        assert conn.execute('SELECT accepted FROM materials WHERE id=%s',(bad,)).fetchone()[0] is False
        assert conn.execute('SELECT count(*) FROM findings').fetchone()[0]==9
        messages=[]
        bot.send_new(conn,when-timedelta(minutes=1))
        assert conn.execute('SELECT count(*) FROM daily_digest_runs').fetchone()[0]==0
        attempts=[]
        def flaky(chat,text,button=False):
            attempts.append(text)
            if len(attempts)==2:
                raise TimeoutError('local controlled failure')
            messages.append(text)
        with patch.object(bot,'say',flaky):
            bot.send_new(conn,when)
        assert len(messages)==1
        with patch.object(bot,'say',lambda chat,text,button=False:messages.append(text)):
            bot.send_new(conn,when+timedelta(minutes=2))
            bot.send_new(conn,when+timedelta(minutes=3))
        assert len(messages)==5 and len(set(messages))==5
        assert conn.execute('SELECT count(*) FROM daily_digest_items WHERE sent_at IS NOT NULL').fetchone()[0]==5
        # Re-initialisation (restart) must preserve daily batch and old history.
        db.init_schema(conn)
        with patch.object(bot,'say') as send:
            bot.send_new(conn,when+timedelta(minutes=4))
            bot.send_new(conn,when+timedelta(hours=2))
            send.assert_not_called()
        assert conn.execute('SELECT count(*) FROM finding_deliveries').fetchone()[0]==6
        assert 'Последняя реальная доставка' in bot.status_text(conn)
        with patch.object(bot,'say',lambda chat,text,button=False:messages.append(text)):
            bot.send_new(conn,when+timedelta(days=1))
        assert len(messages)==8
        # Snapshot refresh must not resurrect a rejected finding or erase delivery.
        conn.execute("UPDATE materials SET status='new'")
        process_new(conn)
        with patch.object(bot,'say') as send:
            bot.send_new(conn,when+timedelta(days=2))
            send.assert_not_called()
        assert conn.execute('SELECT count(*) FROM finding_deliveries').fetchone()[0]==9
        # A slow send crosses the closing boundary: later parts must wait.
        insert('Утечка персональных данных опроса: пример 20',20)
        insert('Утечка персональных данных опроса: пример 21',21)
        process_new(conn)
        state=[when+timedelta(days=3,minutes=14,seconds=59)]
        def slow(chat,text,button=False):
            messages.append(text)
            state[0]+=timedelta(seconds=2)
        with patch.object(delivery,'clock',lambda:state[0]),patch.object(bot,'say',slow):
            bot.send_new(conn)
        assert len(messages)==9
        with patch.object(bot,'say',lambda chat,text,button=False:messages.append(text)):
            bot.send_new(conn,when+timedelta(days=4))
        assert len(messages)==10
        # Exercise the real collector's retirement of generic blog sources.
        from collector import main as collector
        from collector.survey import article
        original_connect=db.connect
        def isolated_connect():
            local=original_connect()
            local.prepare_threshold=None
            local.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            return local
        config=dict(name='LOCAL PRIVACY SOURCE',url='https://example.com/privacy')
        item=article('https://example.com/privacy/consent',
            '<article><h1>Согласие на обработку персональных данных</h1><p>Как проверить согласие респондента.</p></article>')
        snapshot=dict(items=[item],checked=[dict(url=item['url'],item=item,error=None)],errors=[],discovered=1)
        # The local PGlite bridge serves one client connection at a time.
        conn.close()
        with patch.object(db,'connect',isolated_connect),patch.object(collector,'survey_sources',lambda:[config]),patch.object(collector,'read_source',lambda *a,**kw:snapshot):
            collector.main()
            collector.main()  # unchanged snapshot should remain a single material
        with isolated_connect() as check:
            assert check.execute('SELECT active FROM sources WHERE id=%s',(source,)).fetchone()[0] is False
            assert check.execute('SELECT count(*) FROM materials WHERE url=%s',(item['url'],)).fetchone()[0]==1
            assert check.execute('SELECT count(*) FROM subscribers').fetchone()[0]==1
        result=dict(passed=True,queue_reclassified=True,old_history_preserved=True,
                    first_day_cards=5,partial_failure_resumed=True,restart_duplicates=0,
                    outside_window_messages=0,slow_send_stopped_at_close=True,
                    collector_catalogue_migration=True,telegram='never called')
        (ROOT/'work/privacy-integration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': run()
