import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from collector.feeds import canonical_url,dedup_key,parse_rss,relevant_topics
from worker.main import classify


def xml(title='Новый порядок',description='Обработка персональных данных',date='Mon, 02 Nov 2026 12:00:00 +0300',category=''):
    return f'''<?xml version="1.0" encoding="utf-8"?><rss><channel><item>
    <title>{title}</title><link>https://example.com/news/1?utm_source=rss&amp;id=1#utm_campaign=test</link>
    <description>{description}</description><pubDate>{date}</pubDate><category>{category}</category>
    </item></channel></rss>'''.encode()


class FreeRSSCheck(unittest.TestCase):
    def test_description_participates_in_strict_selection(self):
        rows=parse_rss(xml())
        self.assertIn('Персональные данные',relevant_topics(rows[0]['title'],rows[0]['text']))
        self.assertIsNotNone(classify(rows[0]['title'],rows[0]['text']))
        self.assertIsNone(classify('Назначен сотрудник','Хранение продуктов'))

    def test_doc_identity_across_publisher_title_formats(self):
        a='Постановление Правительства РФ от 28.10.2026 N 1418 "О персональных данных"'
        b='Постановление Правительства РФ от 28.10.2026 № 1418"О персональных данных"'
        self.assertEqual(dedup_key(a),dedup_key(b))
        self.assertNotEqual(dedup_key(a),dedup_key(b.replace('1418','1419')))
        self.assertNotEqual(dedup_key(a),dedup_key(b.replace('28.10','29.10')))

    def test_links_remove_tracking_but_keep_meaningful_query(self):
        row=parse_rss(xml())[0]
        self.assertEqual(row['url'],'https://example.com/news/1?id=1')
        self.assertEqual(canonical_url('https://example.com/a?x=1&utm_medium=rss'),'https://example.com/a?x=1')

    def test_start_cutoff_and_timezone(self):
        self.assertEqual(len(parse_rss(xml(),datetime(2026,11,2,8,tzinfo=timezone.utc))),1)
        self.assertEqual(parse_rss(xml(),datetime(2026,11,2,10,tzinfo=timezone.utc)),[])

    def test_relevant_enforcement_is_not_blanket_excluded(self):
        self.assertEqual(len(parse_rss(xml(category='Судебная практика'))),1)
        self.assertTrue(relevant_topics('ВС РФ: персональные данные'))
        self.assertTrue(relevant_topics('Суд обязал удалить персональные данные'))

    def test_unsafe_link_rejected(self):
        for link in ['javascript:alert(1)','file:///private','https://user:password@example.com/']:
            with self.subTest(link=link), self.assertRaises(ValueError): canonical_url(link)

    def test_missing_date_and_html_challenge_are_errors(self):
        for body in [xml(date=''),b'<html>Access denied</html>',b'<rss><channel><item></rss>']:
            with self.assertRaises((ValueError,ET.ParseError)):
                parse_rss(body)

    def test_dtd_rejected(self):
        with self.assertRaises(ValueError):
            parse_rss(b'<!DOCTYPE rss [<!ENTITY test "secret">]><rss><channel/></rss>')

    def test_announcement_markup_cleaned_original_retained(self):
        row=parse_rss(xml(description='&lt;b&gt;Персональные данные&lt;/b&gt;'))[0]
        self.assertEqual(row['text'],'Персональные данные')
        self.assertIn('<b>',row['metadata']['description'])


if __name__=='__main__': unittest.main()
