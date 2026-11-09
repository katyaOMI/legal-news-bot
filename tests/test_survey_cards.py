import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bot import main as bot
from test_existing_bot import BatchDB, Cursor


class SurveyCardsTests(unittest.TestCase):
    def test_new_means_discovered_and_old_date_is_visible(self):
        text=bot.finding_lines([('Как составить анкету','Методика','https://example.com/guide','Источник',
            datetime(2016,12,14,tzinfo=timezone.utc),'guide','Помогает подготовить анкету.')],new=True)[0]
        self.assertIn('НОВОЕ · Практика',text)
        self.assertIn('14.12.2016',text)
        self.assertIn('Помогает подготовить анкету.',text)

    def test_unknown_date_is_not_replaced_with_today(self):
        text=bot.finding_lines([('Анкета','Методика','https://example.com','Источник',None,'article','')],new=True)[0]
        self.assertIn('Дата публикации: не указана',text)

    def test_recent_delivery_defers_the_next_batch(self):
        class RecentDB(BatchDB):
            def execute(self,sql,params=()):
                if 'SELECT EXISTS (SELECT 1 FROM finding_deliveries' in sql:
                    return Cursor([(True,)])
                return super().execute(sql,params)
        conn=RecentDB([(1,'Анкета','Методика','https://example.com','Источник')])
        with patch.object(bot,'say') as send:
            bot.send_new(conn)
        send.assert_not_called()

    def test_benefit_cannot_inject_telegram_html(self):
        text=bot.finding_lines([('Анкета','Методика','https://example.com','Источник',None,'guide','<script>')])[0]
        self.assertNotIn('<script>',text)


if __name__=='__main__':
    unittest.main()
