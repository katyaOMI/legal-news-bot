import unittest

from collector.survey import article, classify_material, discover, read_source


def page(title, body='', description='', date=''):
    return f'<html><head><meta name="description" content="{description}"></head><body><nav>Опросы и персональные данные</nav><article><h1>{title}</h1><meta itemprop="datePublished" content="{date}"><p>{body}</p></article></body></html>'


class SurveySourcesTests(unittest.TestCase):
    def test_owner_examples_are_relevant(self):
        for title in ['Безопасно ли пользоваться сайтами интернет-опросов?',
                      '12 сервисов для анкетирования',
                      'Федеральный закон О персональных данных',
                      'Онлайн-опросы: зачем они нужны и как их проводить',
                      'Как составить анкету для исследования']:
            with self.subTest(title=title):
                self.assertIsNotNone(classify_material(title))

    def test_unrelated_news_and_results_are_not_enough(self):
        for title in ['Импортные пошлины на алкоголь', 'Хранение продуктов на складе',
                      'Итоги голосования за лучшую фотографию', 'Как заработать на опросах']:
            self.assertIsNone(classify_material(title),title)

    def test_respondent_safety_and_description_participate(self):
        self.assertIsNotNone(classify_material('Как не стать жертвой мошенника','Безопасность респондентов в онлайн-опросах'))
        result=classify_material('Почему опасно зарабатывать на опросах','Как защитить персональные данные')
        self.assertIn('Безопасность опросов',result['topics'])

    def test_page_extracts_old_date_without_replacing_it(self):
        result=article('https://example.com/guide',page('Как провести онлайн-опрос','Понятные вопросы для респондента',date='2016-12-14'))
        self.assertEqual(result['published_at'],'2016-12-14T00:00:00+00:00')
        self.assertNotIn('Опросы и персональные данные',result['text'])
        self.assertEqual(result['content_kind'],'guide')

    def test_unknown_date_and_html_entities(self):
        result=article('https://example.com/guide',page('Анкеты &amp; опросы','Как задавать вопросы'))
        self.assertIsNone(result['published_at'])
        self.assertIn('&',result['title'])

    def test_menu_does_not_make_unrelated_article_relevant(self):
        self.assertIsNone(article('https://example.com/news',page('Ввозные пошлины на алкоголь','Ставки таможенных пошлин изменены')))

    def test_discovery_restricts_host_path_and_tracking(self):
        source=dict(url='https://example.com/blog',pattern=r'^/blog/[^/]+$',seeds=[])
        html='<a href="/blog/new?utm_source=rss">Опросы</a><a href="https://other.example/blog/new">X</a><a href="/login">Y</a><a href="javascript:alert(1)">Z</a>'
        self.assertEqual(discover(source,html),['https://example.com/blog/new'])

    def test_failure_is_isolated_and_unseen_discovery_is_loaded(self):
        source=dict(url='https://example.com/blog',pattern=r'^/blog/[^/]+$',seeds=['https://example.com/blog/old'])
        def fetch(url):
            if url.endswith('/blog'):
                return '<a href="/blog/new">Как провести опрос</a><a href="/blog/broken">Анкета</a>'
            if url.endswith('broken'):
                raise TimeoutError('offline test')
            return page('Как провести опрос',date='2026-11-01')
        result=read_source(source,known={'https://example.com/blog/old'},fetch=fetch)
        self.assertEqual([row['url'] for row in result['items']],['https://example.com/blog/new'])
        self.assertEqual(len(result['errors']),1)
        self.assertEqual(result['discovered'],3)

    def test_unchanged_known_pages_are_not_fetched_again(self):
        source=dict(url='https://example.com/guide',seed_only=True,pattern='.*',seeds=['https://example.com/guide'])
        result=read_source(source,known=set(source['seeds']),fetch=lambda _:self.fail('unexpected fetch'))
        self.assertEqual(result['items'],[])
        self.assertEqual(result['checked'],[])

    def test_access_denied_is_not_a_material(self):
        with self.assertRaises(ValueError):
            article('https://example.com/guide',page('Access denied'))


if __name__=='__main__':
    unittest.main()
