import unittest
from legal_news.relevance import evaluate


class PersonalDataFocusTests(unittest.TestCase):
    def test_owner_approved_headlines_and_new_paraphrases(self):
        for title in [
            'Вскрытие Claude Code: секреты и двойная бухгалтерия телеметрии',
            'Вы ещё не нажали «Согласен», а сайт уже собирает данные',
            'Отзыв SSL-сертификатов российских сайтов: что будет с Chrome и Firefox',
            'Безопасно ли пользоваться сайтами платных интернет-опросов?',
            'Федеральный закон «О персональных данных»',
            'Какая личная информация подвергает вас риску?',
            'Сервис изменил политику конфиденциальности',
        ]:
            with self.subTest(title=title):
                self.assertTrue(evaluate(title)['accepted'])

    def test_infrastructure_news_is_not_rescued_by_generic_safety_terms(self):
        for title in ['Как защитить файлы NAS Synology от шифровальщика',
                      'Защита сайтов: как мы управляем инфраструктурой с помощью Temporal',
                      'На взлом Java ушло меньше 200 долларов',
                      'InfoWatch Traffic Monitor совместим с новым сетевым экраном']:
            with self.subTest(title=title):
                self.assertFalse(evaluate(title, 'Безопасность сервиса, защита файлов и облака.')['accepted'])

    def test_short_focused_lead_is_read_but_late_menu_is_not(self):
        self.assertTrue(evaluate('Новые правила сервиса', 'Изменены условия обработки персональных данных.')['accepted'])
        self.assertFalse(evaluate('Новый роутер', 'Технические характеристики. ' * 40 +
                                  'Читайте также: личные данные и приватность')['accepted'])


if __name__ == '__main__': unittest.main()
