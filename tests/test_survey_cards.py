import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from bot import main as bot
from test_existing_bot import BatchDB, Cursor


class SurveyCardsTests(unittest.TestCase):
    def test_old_article_never_gets_new_label(self):
        text=bot.finding_lines([('Как составить анкету','Методика','https://example.com/guide','Источник',
            datetime(2016,12,14,tzinfo=timezone.utc),'guide','Помогает подготовить анкету.')],new=True)[0]
        self.assertNotIn('НОВОЕ',text)
        self.assertIn('14.12.2016',text)
        self.assertIn('Помогает подготовить анкету.',text)

    def test_unknown_date_is_not_replaced_with_today(self):
        text=bot.finding_lines([('Анкета','Методика','https://example.com','Источник',None,'article','')],new=True)[0]
        self.assertIn('Дата публикации: не указана',text)

    def test_new_label_uses_publication_age_and_kind_on_every_render(self):
        now=datetime(2026,11,11,10,tzinfo=timezone.utc)
        for age,kind,expected in [(0,'news',True),(72,'news',True),
                                  (72.01,'news',False),(-1,'news',False),
                                  (1,'article',False),(1,'document',False)]:
            with self.subTest(age=age,kind=kind):
                row=('Риск','Данные','https://example.com','Источник',now-timedelta(hours=age),kind,'Проверить')
                text=bot.finding_lines([row],now=now)[0]
                self.assertEqual('НОВОЕ' in text,expected)
        row=('Риск','','https://example.com','Источник',now,'news','')
        self.assertNotIn('НОВОЕ',bot.finding_lines([row],now=now+timedelta(days=4))[0])

    def test_unknown_date_news_has_no_new_label(self):
        text=bot.finding_lines([('Риск','','https://example.com','Источник',None,'news','')],new=True)[0]
        self.assertNotIn('НОВОЕ',text)

    def test_benefit_cannot_inject_telegram_html(self):
        text=bot.finding_lines([('Анкета','Методика','https://example.com','Источник',None,'guide','<script>')])[0]
        self.assertNotIn('<script>',text)


if __name__=='__main__':
    unittest.main()
