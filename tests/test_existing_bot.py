import contextlib
import unittest
import urllib.error
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


if __name__=='__main__': unittest.main()
