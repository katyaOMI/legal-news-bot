import unittest
from legal_news.relevance import evaluate
from worker.main import classify


class RiskRelevanceTests(unittest.TestCase):
    def test_owner_negative_examples_and_obsolete_themes(self):
        rejected = [
            'Как составить анкету для исследования',
            'Где искать и как привлекать участников исследований',
            'Индекс лояльности NPS: опрос, формула, пример',
            'Структурированное наблюдение в исследовании',
            'Сколько респондентов нужно для опроса: таблица',
            '12 сервисов для анкетирования',
            'Изменения маркировки рекламы',
            'Организацию признали иностранным агентом',
            'Нейросеть Gemini в России: как не потерять данные',
            'ФНС рассмотрены вопросы жалоб на страховые взносы',
            'Банк России: защита прав заёмщиков по вопросам задолженности',
        ]
        for title in rejected:
            with self.subTest(title=title):
                self.assertFalse(evaluate(title,'Как провести опрос и выбрать инструмент')['accepted'])

    def test_legal_safety_and_relevant_enforcement(self):
        for title in ['О защите персональных данных',
                      'РКН разъяснил требования к согласию на обработку персональных данных',
                      'Суд оштрафовал оператора за утечку персональных данных',
                      'Фишинговые опросы: как защитить респондентов',
                      'Трансграничная передача персональных данных из России']:
            with self.subTest(title=title):
                result=evaluate(title)
                self.assertTrue(result['accepted'])
                self.assertIn('провер',result['usefulness'].lower())
                self.assertTrue(result['reason'])

    def test_same_policy_for_rss_and_web(self):
        for kind in ['legal_rss','survey_web','pravo']:
            self.assertIsNone(classify('Как составить анкету',kind=kind))
            self.assertIsNotNone(classify('О персональных данных',kind=kind))

    def test_foreign_requires_russian_connection(self):
        self.assertFalse(evaluate('GDPR: штраф за обработку персональных данных')['accepted'])
        self.assertTrue(evaluate('GDPR: персональные данные',
            'Что учитывать при передаче данных из России иностранному сервису')['accepted'])

    def test_menu_or_late_reference_does_not_qualify_article(self):
        self.assertFalse(evaluate('Как выбрать CRM','Описание продукта. '*80+'Персональные данные')['accepted'])
        self.assertFalse(evaluate('ФНС: рассмотрение жалоб',
            'Вопросы взыскания задолженности, предметом которых является несогласие плательщика')['accepted'])

    def test_no_unsupported_legal_conclusion_or_amount(self):
        result=evaluate('Согласие на обработку персональных данных',
                        'Ведомство пояснило порядок получения согласия.')
        self.assertEqual(result['risk'],'Ведомство пояснило порядок получения согласия.')
        self.assertNotIn('руб',result['usefulness'])


if __name__=='__main__': unittest.main()
