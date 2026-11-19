import sqlite3
import unittest
from unittest.mock import patch

from bot import main as bot
from legal_news.relevance import evaluate


class PostDeployTests(unittest.TestCase):
    def test_survey_about_safety_is_not_survey_safety_guidance(self):
        title = 'Банк России запустил очередной опрос о безопасности банковских услуг'
        text = ('Респондентам предлагают поделиться информацией о видах кибермошенничества, '
                'с которыми они сталкивались, и правилах безопасного финансового поведения.')
        self.assertFalse(evaluate(title, text)['accepted'])
        self.assertFalse(evaluate('Начался опрос о приватности в интернете',
                                  'Приглашаем принять участие в исследовании.')['accepted'])

    def test_real_survey_privacy_guidance_and_fraud_alert_remain(self):
        for title, text in [
            ('Безопасно ли пользоваться сайтами платных интернет-опросов?', 'Как защитить персональные данные.'),
            ('Фишинговые опросы: как защитить респондентов', ''),
            ('Мошенники запустили поддельный опрос', 'Поддельный опрос крадёт персональные данные.'),
        ]:
            with self.subTest(title=title): self.assertTrue(evaluate(title, text)['accepted'])

    def test_latest_prefers_publication_date_over_processing_date(self):
        with sqlite3.connect(':memory:') as conn:
            conn.executescript('''
                CREATE TABLE legal_findings(id INTEGER,material_id INTEGER,summary TEXT,entity TEXT,created_at TEXT);
                CREATE TABLE materials(id INTEGER,source_id INTEGER,url TEXT,published_at TEXT,
                    content_kind TEXT,usefulness TEXT,risk_excerpt TEXT,audience TEXT);
                CREATE TABLE sources(id INTEGER,name TEXT);
                INSERT INTO sources VALUES(1,'Public test source');
            ''')
            for ident, title, published, processed in [
                (1, 'Old article', '2024-07-24T00:00:00Z', '2026-11-19T16:00:00Z'),
                (2, 'Fresh article', '2026-11-19T00:00:00Z', '2026-11-18T00:00:00Z'),
                (3, 'Unknown date', None, '2026-11-20T00:00:00Z'),
                (4, 'Yesterday article', '2026-11-18T00:00:00Z', '2026-11-17T00:00:00Z'),
            ]:
                conn.execute('INSERT INTO legal_findings VALUES(?,?,?,?,?)', (ident, ident, title, '', processed))
                conn.execute('INSERT INTO materials VALUES(?,?,?,?,?,?,?,?)',
                             (ident, 1, 'https://example.org/' + str(ident), published, 'article', '', '', ''))
            with patch.object(bot, 'say') as send:
                bot.handle(conn, {'chat': {'id': 1, 'type': 'private'}, 'text': '/latest'})
            message = send.call_args.args[1]
            self.assertIn('Yesterday article', message)
            self.assertLess(message.index('Fresh article'), message.index('Yesterday article'))
            self.assertLess(message.index('Yesterday article'), message.index('Old article'))
            self.assertNotIn('Unknown date', message)


if __name__ == '__main__': unittest.main()
