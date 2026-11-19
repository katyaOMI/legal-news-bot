"""Conservative editorial focus approved from the owner's public examples."""
import re
from legal_news.core import clean


def focus(title):
    title = clean(title).lower().replace('ё', 'е')
    if re.search(r'персональн\w* данн|личн\w* (?:информац|данн)|152[‑–—-]\s*фз|'
                 r'приватност|телеметри|конфиденц|\bcookie|куки|трекинг|слежк|отслеживан|'
                 r'собира\w* (?:\w+\s+){0,3}данн|услови\w* обработк\w* данн|'
                 r'истори\w* чат|переписк', title):
        return 'Личные данные, сбор информации и приватность'
    if re.search(r'\bопрос\w*', title) and re.search(r'безопас|мошен|данн|приват|фишинг', title):
        return 'Безопасность участия в онлайн-опросах'
    if re.search(r'ssl|tls|сертификат', title) and re.search(r'chrome|firefox|браузер|https', title):
        return 'Защищённое соединение и доверие к сайтам'
    return None
