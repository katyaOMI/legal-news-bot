"""Disposable local SQL integration check; no network sources or Telegram sends."""
import json
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'work/python-packages'))
# This fixed localhost-only endpoint cannot target the production database.
os.environ['DATABASE_URL']='postgresql://postgres@127.0.0.1:55432/postgres?sslmode=disable&connect_timeout=8'
import db
from collector import main as collector
from collector.survey import read_source
from worker import main as worker
from bot import main as bot


def run():
    from psycopg import sql
    original_connect=db.connect
    schema='survey_'+uuid.uuid4().hex
    with original_connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def isolated():
        conn=original_connect()
        # PGlite's socket bridge shares a backend across client connections.
        conn.prepare_threshold=None
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        return conn
    db.connect=isolated
    source=dict(name='LOCAL SURVEY TEST',url='https://example.com/blog',pattern=r'^/blog/[^/]+$',seeds=['https://example.com/blog/old'])
    calls=[]
    def fetch(url):
        calls.append(url)
        if url==source['url']:
            return '<a href="/blog/new">Опрос</a><a href="/blog/unrelated">Ипотека</a><a href="/blog/error">Ошибка</a>'
        if url.endswith('/error'):
            raise TimeoutError('local synthetic fixture')
        if url.endswith('/unrelated'):
            return '<article><h1>Правила ипотеки</h1><p>Кредитование жилья</p></article>'
        return '<article><h1>Как провести онлайн-опрос '+('по новой методике' if url.endswith('/new') else 'безопасно')+'</h1><meta itemprop="datePublished" content="2016-12-14"><p>Вопросы анкеты и защита персональных данных респондента.</p></article>'
    def read(config,known=()):
        return read_source(config,known=known,fetch=fetch)
    with db.connect() as conn:
        db.init_schema(conn)
        conn.execute('UPDATE sources SET active=FALSE')
        conn.execute("INSERT INTO subscribers(chat_id,name,started_at) VALUES (1,'LOCAL TEST ONLY',now()-interval '1 day')")
    def collect():
        with patch.object(collector,'survey_sources',lambda:[source]),patch.object(collector,'read_source',read):
            try:
                collector.main()
            except RuntimeError as error:
                # One failed page is expected and must be recorded without losing successes.
                assert 'Не удалось проверить источники' in str(error),str(error)
    collect()
    with db.connect() as conn:
        worker.process_new(conn)
        rows=conn.execute('SELECT id,summary FROM legal_findings').fetchall()
        assert len(rows)==2,rows
        assert conn.execute('SELECT count(*) FROM materials WHERE is_backfill').fetchone()[0]==0
        assert conn.execute('SELECT min(published_at) FROM materials').fetchone()[0].year==2016
        assert conn.execute('SELECT count(*) FROM source_pages').fetchone()[0]==4
        assert conn.execute('SELECT errors FROM source_checks').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM subscribers').fetchone()[0]==1
        messages=[]
        with patch.object(bot,'say',lambda chat,text,button=True:messages.append(text)):
            bot.send_new(conn)
            bot.send_new(conn)
        assert len(messages)==1,messages
        assert messages[0].count('НОВОЕ ·')==2
        assert '2016' in messages[0]
        assert conn.execute('SELECT count(*) FROM finding_deliveries').fetchone()[0]==2
    before=len(calls)
    collect()
    # Only the listing is fetched on the next immediate pass; pages use stored checks.
    assert len(calls)==before+1,calls[before:]
    with db.connect() as conn:
        worker.process_new(conn)
        assert conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]==2
        with patch.object(bot,'say') as send:
            bot.send_new(conn)
            send.assert_not_called()
        # A genuine content update keeps the finding identity and prior delivery.
        conn.execute("UPDATE materials SET title=title||' — дополнено',status='new'")
        worker.process_new(conn)
        assert conn.execute('SELECT count(*) FROM legal_findings').fetchone()[0]==2
        assert conn.execute('SELECT count(*) FROM finding_deliveries').fetchone()[0]==2
        status=bot.status_text(conn)
        assert 'Источников активно: 1' in status
    result=dict(engine='disposable local PostgreSQL/PGlite',synthetic=True,findings=2,messages=1,
        repeat_messages=0,old_date_preserved=True,source_error_isolated=True,telegram='not called')
    (ROOT/'work/survey-integration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    run()
