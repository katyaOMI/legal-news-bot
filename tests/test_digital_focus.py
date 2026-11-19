import unittest
from pathlib import Path

from legal_news.relevance import evaluate, VERSION
from worker.main import classify


class DigitalFocusTests(unittest.TestCase):
    def test_general_device_attacks_are_lower_priority_after_owner_review(self):
        for title in ['C3RB3R атакует NAS Synology: как защитить свои файлы',
                      'Уязвимость роутера позволяет взломать устройство',
                      'Вредоносная программа заражает смартфоны',
                      'Новый смартфон получил большой экран']:
            with self.subTest(title=title):
                self.assertFalse(evaluate(title)['accepted'])
    def test_owner_screenshots_are_rejected(self):
        examples = [
            ('Суд признал незаконным увольнение медработника за разглашение врачебной тайны',
             'При обращении в прокуратуру фельдшер допустила передачу персональных данных пациента и была уволена.'),
            ('Персональные данные должностных лиц начнут обрабатывать по-другому',
             '№ 23-ФЗ вносит изменения в несколько законодательных актов, включая закон о персональных данных.'),
            ('Пенсионерка отсудила у банка 1 тыс. руб. компенсации за рекламное СМС-сообщение',
             'Клиентка не давала своего согласия на обработку персональных данных для участия в маркетинговых мероприятиях.'),
        ]
        for title, text in examples:
            with self.subTest(title=title):
                self.assertFalse(evaluate(title, text)['accepted'])
                self.assertIsNone(classify(title, text))

    def test_online_risks_do_not_need_survey_keyword(self):
        for title in [
            'Утечка переписки пользователей мессенджера',
            'Telegram изменил настройки приватности',
            'Облачный сервис изменил условия обработки данных',
            'SSL-сертификаты российских сайтов: что будет с браузерами',
            'В браузере появилась настройка приватности',
        ]:
            with self.subTest(title=title):
                result = evaluate(title)
                self.assertTrue(result['accepted'])
                self.assertNotIn('Организаторам опросов', result['audience'])

    def test_ai_requires_substantive_privacy_or_safety(self):
        for title in ['ChatGPT: утечка истории чатов',
                      'Нейросеть сохраняет персональные данные пользователей',
                      'Промпт-инъекция в ИИ: риск для конфиденциальных документов']:
            with self.subTest(title=title):
                self.assertTrue(evaluate(title)['accepted'])
        for title in ['Новая нейросеть пишет код быстрее',
                      'ChatGPT запустил голосовой режим',
                      'Безопасный ИИ стал быстрее и умнее']:
            with self.subTest(title=title):
                self.assertFalse(evaluate(title)['accepted'])

    def test_global_service_and_local_foreign_law_have_different_scope(self):
        self.assertTrue(evaluate('В США сообщили, как глобальный сервис собирает личные данные')['accepted'])
        self.assertFalse(evaluate('В Германии суд назначил штраф по GDPR за персональные данные')['accepted'])
        self.assertTrue(evaluate('GDPR: персональные данные',
                                 'Передача данных из России иностранному сервису.')['accepted'])

    def test_late_digital_reference_cannot_rescue_specialized_case(self):
        self.assertFalse(evaluate('Персональные данные должностных лиц',
                                  'Изменения специального порядка. ' * 45 +
                                  'Читайте также: приватность в интернете')['accepted'])

    def test_generic_publisher_website_does_not_rescue_medical_case(self):
        self.assertFalse(evaluate('Персональные данные медработника недопустимо публиковать без его согласия',
                                  'Суд отказал интернет-порталу в жалобе на закон о персональных данных.')['accepted'])
        self.assertTrue(evaluate('Утечка данных пациентов онлайн-сервиса',
                                 'Утечка персональных данных затронула аккаунты пользователей.')['accepted'])

    def test_survey_safety_and_general_data_guidance_remain(self):
        self.assertTrue(evaluate('О персональных данных')['accepted'])
        result = evaluate('Фишинговые опросы: как защитить респондентов')
        self.assertTrue(result['accepted'])
        self.assertIn('опросов', result['audience'])

    def test_source_excerpt_is_not_rewritten_as_a_new_fact(self):
        text = 'Облачный сервис изменил условия обработки данных. Подробнее — в источнике.'
        result = evaluate('Облачный сервис обновил правила', text)
        self.assertTrue(result['accepted'])
        self.assertIn(result['risk'], text)

    def test_sql_view_and_worker_policy_versions_match(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("m.evaluation_version='" + VERSION + "'", (root / 'db/schema.sql').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
