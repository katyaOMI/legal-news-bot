import contextlib
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bot import main as bot
from worker.main import classify


class Cursor:
    def __init__(self, rows): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class DeliveryDB:
    def __init__(self):
        self.delivered=set()
        self.events_delivered=set()
    def transaction(self): return contextlib.nullcontext()
    def execute(self, sql, params=()):
        if 'SELECT EXISTS' in sql: return Cursor([(False,)])
        if sql == 'SELECT chat_id FROM subscribers': return Cursor([(11,),(22,)])
        if 'SELECT f.id,f.summary' in sql:
            chat=params[0]
            return Cursor([] if (1,chat) in self.delivered else [(1,'О персональных данных','Персональные данные','https://publication.pravo.gov.ru/document/0001202611020001','Официальный портал')])
        if 'INSERT INTO finding_deliveries' in sql: self.delivered.add(tuple(params))
        if 'SELECT e.id' in sql:
            return Cursor([(event,chat,body) for event,body in [(1,'Сбой'),(2,'Восстановлено')]
                for chat in [11,22] if (event,chat) not in self.events_delivered])
        if 'INSERT INTO source_event_deliveries' in sql: self.events_delivered.add(tuple(params))
        return Cursor([])


class BatchDB(DeliveryDB):
    def __init__(self, rows):
        super().__init__()
        self.rows=rows
    def execute(self, sql, params=()):
        if sql == 'SELECT chat_id FROM subscribers': return Cursor([(11,)])
        if 'SELECT f.id,f.summary' in sql: return Cursor([row for row in self.rows if (row[0],11) not in self.delivered][:3])
        return super().execute(sql,params)


class WeeklyDB:
    def __init__(self, rows, recent_news=False):
        self.rows=rows
        self.recent_news=recent_news
        self.delivered=set()
        self.runs=set()
        self.exists_calls=0
    def transaction(self): return contextlib.nullcontext()
    def execute(self, sql, params=()):
        if 'SELECT EXISTS' in sql:
            self.exists_calls+=1
            return Cursor([(self.recent_news if self.exists_calls==2 else False,)])
        if sql == 'SELECT chat_id FROM subscribers': return Cursor([(11,)])
        if 'FROM weekly_digest_runs' in sql: return Cursor([(1,)] if (params[0],params[1]) in self.runs else [])
        if 'FROM legal_findings' in sql and 'LIMIT %s' in sql: return Cursor(self.rows[:params[-1]])
        if 'INSERT INTO finding_deliveries' in sql: self.delivered.add(tuple(params))
        if 'INSERT INTO weekly_digest_runs' in sql: self.runs.add((params[0],params[1]))
        return Cursor([])


class ExistingBotTests(unittest.TestCase):
    def test_free_classification(self):
        topic,summary=classify('О защите персональных данных')
        self.assertIn('Персональные данные',topic)
        self.assertEqual(summary,'О защите персональных данных')
        self.assertIsNone(classify('О назначении сотрудника'))

    def test_failed_recipient_is_retried_without_duplicate_to_successful(self):
        conn=DeliveryDB()
        delivered=[]
        def send(chat,text,button=True):
            if chat==22: raise urllib.error.HTTPError('redacted',500,'test',None,None)
            delivered.append(chat)
        with patch.object(bot,'say',send),patch.object(bot.db,'log'):
            bot.send_new(conn)
        self.assertEqual(conn.delivered,{(1,11)})
        with patch.object(bot,'say',lambda chat,text,button=True:delivered.append(chat)):
            bot.send_new(conn)
            bot.send_new(conn)
        self.assertEqual(delivered,[11,22])
        self.assertEqual(conn.delivered,{(1,11),(1,22)})

    def test_notices_once_per_recipient_and_order_after_failure(self):
        conn=DeliveryDB()
        def send(chat,text,button=True):
            if chat==11: raise urllib.error.HTTPError('redacted',500,'test',None,None)
        with patch.object(bot,'say',send),patch.object(bot.db,'log'):
            bot.send_source_events(conn)
        self.assertEqual(conn.events_delivered,{(1,22),(2,22)})
        delivered=[]
        with patch.object(bot,'say',lambda chat,text,button=True:delivered.append((chat,text))):
            bot.send_source_events(conn)
            bot.send_source_events(conn)
        self.assertEqual(delivered,[(11,'Сбой'),(11,'Восстановлено')])

    def test_no_site_button_by_default(self):
        with patch.dict(bot.os.environ,{'ENABLE_MINI_APP':'','COOLIFY_URL':'https://example.com'}):
            self.assertEqual(bot.page_url(),'')

    def test_html_escaped(self):
        text=bot.finding_lines([('<script>','Данные & опросы','https://example.com/','Источник')])[0]
        self.assertNotIn('<script>',text)
        self.assertIn('&lt;script&gt;',text)

    def test_new_news_messages_have_at_most_three_items(self):
        rows=[(i,f'Новость {i}','Персональные данные',f'https://example.com/{i}','Источник') for i in range(1,5)]
        conn=BatchDB(rows)
        messages=[]
        with patch.object(bot,'say',lambda chat,text,button=True:messages.append(text)),patch.object(bot.db,'log'):
            bot.send_new(conn)
        self.assertEqual([message.count('• <a href=') for message in messages],[3])
        self.assertTrue(all('НОВОЕ' in message for message in messages))

    def test_weekly_digest_is_due_monday_at_1300_moscow(self):
        monday_1300_moscow=datetime(2026,11,9,10,0,tzinfo=timezone.utc)
        monday_1259_moscow=monday_1300_moscow-timedelta(minutes=1)
        tuesday=datetime(2026,11,10,10,0,tzinfo=timezone.utc)
        self.assertEqual(bot.weekly_digest_window(monday_1300_moscow).isoformat(),'2026-11-09')
        self.assertIsNone(bot.weekly_digest_window(monday_1259_moscow))
        self.assertIsNone(bot.weekly_digest_window(tuesday))

    def test_weekly_digest_sends_at_most_three_archive_items_once(self):
        rows=[(i,f'Архивная новость {i}','Персональные данные',f'https://example.com/{i}','Источник') for i in range(1,5)]
        conn=WeeklyDB(rows)
        messages=[]
        when=datetime(2026,11,9,10,0,tzinfo=timezone.utc)
        with patch.object(bot,'say',lambda chat,text,button=True:messages.append(text)):
            bot.send_weekly_digest(conn,when)
            bot.send_weekly_digest(conn,when)
        self.assertEqual(len(messages),1)
        self.assertIn('Из архива правовых публикаций',messages[0])
        self.assertNotIn('Дайджест новостей',messages[0])
        self.assertEqual(messages[0].count('• <a href='),3)
        self.assertEqual(len(conn.delivered),3)

    def test_weekly_digest_waits_if_new_news_arrived_this_week(self):
        conn=WeeklyDB([],recent_news=True)
        with patch.object(bot,'say') as send:
            bot.send_weekly_digest(conn,datetime(2026,11,9,10,0,tzinfo=timezone.utc))
        send.assert_not_called()

    def test_weekly_digest_does_not_send_empty_message_or_consume_week(self):
        conn=WeeklyDB([])
        when=datetime(2026,11,9,10,0,tzinfo=timezone.utc)
        with patch.object(bot,'say') as send:
            bot.send_weekly_digest(conn,when)
            bot.send_weekly_digest(conn,when)
        send.assert_not_called()
        self.assertEqual(conn.delivered,set())
        self.assertEqual(conn.runs,set())

    def test_weekly_digest_can_send_when_material_arrives_after_empty_check(self):
        conn=WeeklyDB([])
        messages=[]
        when=datetime(2026,11,9,10,0,tzinfo=timezone.utc)
        with patch.object(bot,'say',lambda chat,text,button=True:messages.append(text)):
            bot.send_weekly_digest(conn,when)
            conn.rows=[(1,'О персональных данных','Персональные данные','https://example.com/1','Источник')]
            bot.send_weekly_digest(conn,when)
            bot.send_weekly_digest(conn,when)
        self.assertEqual(len(messages),1)
        self.assertIn('Из архива правовых публикаций',messages[0])
        self.assertEqual(conn.delivered,{(1,11)})
        self.assertEqual(len(conn.runs),1)


if __name__=='__main__': unittest.main()
