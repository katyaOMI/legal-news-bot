"""Shared rules for digital privacy, safety and service access."""
import re

from legal_news.core import clean
from legal_news.personal_data import focus

VERSION = 'personal-data-v2.2'
DATA = r'персональн\w*\s+данн|личн\w* (?:информац|данн)|152[‑–—-]\s*фз|субъект\w*\s+данн|personal data|data protection'
SURVEY = r'\bопрос\w*|\bанкет\w*|\bреспондент\w*|\bsurvey\b|\bquestionnaire\b|\btypeform\b|\btestograf\b|\bанкетолог\b|google forms|яндекс форм'
SAFETY = r'\bбезопас|\bконфиденц|\bприватност|\bфишинг|\bмошен|\bутечк|\bзащит|\bлокализац|\bтрансграничн|\bсогласи|\bprivacy\b|\bphishing\b|\bbreach\b|шифровальщик|вымогател|вредонос|уязвим|взлом|заражен|краж\w* (?:файл|данн|парол)'
DEVICE = r'\bnas\b|\bsynology\b|сетев\w* хранилищ|\bфайл\w*|\bустройств\w*|смартфон|компьютер|ноутбук|роутер|\bтелефон\w*|\bридер\w*'
DIGITAL = (SURVEY + r'|интернет|онлайн|он-лайн|\bсайт\w*|\bсервис\w*|\bплатформ\w*|мессенджер|'
           r'облач\w*|\bоблак\w*|браузер|аккаунт|учетн\w* запис|парол|соцсет|социальн\w* сет|'
           r'telegram|телеграм|whatsapp|\bgoogle\b|\bvpn\b|chatgpt|чат-бот|чатбот|\bgemini\b|' + DEVICE)
ACCESS = r'блокиров|заблокир|недоступ|огранич\w* доступ|доступност|прекрат\w* работ|перестал\w* работ'
POLICY = r'политик\w* конфиденц|услови\w* (?:использован|обработк)|правил\w* (?:использован|обработк)|хранени\w* данн|удалени\w* данн|передач\w* данн|сбор\w* данн'
AI = r'gemini|chatgpt|нейросет|искусственн\w* интеллект|\bии\b|чат-бот|чатбот'
AI_RISK = DATA + r'|утечк|фишинг|мошен|конфиденц|приватност|privacy|prompt injection|промпт-инъекц|краж\w* данн|парол|истори\w* чат'


def evaluate(title, description=''):
    headline = clean(title).lower().replace('ё', 'е')
    body = clean(description)[:5000]
    text = headline + ' ' + body.lower().replace('ё', 'е')
    # The headline or first substantive paragraph must carry the actual risk.
    lead = headline + ' ' + body[:700].lower().replace('ё', 'е')
    def has(pattern, value=text):
        return bool(re.search(pattern, value))
    rejected = dict(accepted=False, reason='Нет содержательной связи с приватностью, безопасностью или доступностью цифровых сервисов', version=VERSION)
    foreign = has(r'\bgdpr\b|евросоюз|европейск\w* союз|\bccpa\b|калифорни|\buk\b|\beu\b|\bсша\b|\bес\b|британи|германи|франци', lead)
    russian = has(r'росси|\bрф\b|роскомнадзор|\bркн\b|152[‑–—-]|из россии|российск', lead)
    foreign_law = has(r'\bgdpr\b|\bccpa\b|штраф|\bсуд\w*|закон|регулятор|обязан', lead)
    if foreign and foreign_law and not russian:
        return rejected | dict(reason='Зарубежная применимость к работе из России не подтверждена')
    methodology = has(r'как составить анкет|как провести опрос|привлекать участник|привлечь участник|где искать.*участник|индекс лояльности|\bnps\b|\benps\b|размер\w* выборк|сколько респондент|метод\w* наблюден', headline)
    title_risk = has(DATA + '|' + SAFETY, headline)
    if methodology and not title_risk:
        return rejected | dict(reason='Методология, рекрутинг или расчёт показателей')
    ai = has(AI, headline)
    if ai and not has(AI_RISK, lead):
        return rejected | dict(reason='Общая новость ИИ без содержательного риска приватности или безопасности')
    data = has(DATA, lead)
    digital = has(DIGITAL, lead)
    specialized = has(r'медработ|врачебн\w* тайн|пациент|фельдшер|увольнен|должностн\w* лиц|рекламн\w* (?:смс|sms)|маркетингов\w* мероприяти', lead)
    # SMS advertising disputes and sector-specific employment/medical cases do
    # not become survey news merely by mentioning personal data.
    specialized_link = has(SURVEY, lead) or (digital and has(
        r'утечк|фишинг|мошен|аккаунт|парол|мессенджер|облачн\w* сервис', lead))
    if specialized and not specialized_link:
        return rejected | dict(reason='Специальная правовая история без связи с цифровым сервисом или опросом')
    body_lead=body[:700].lower().replace('ё','е')
    survey_safety = ((has(SURVEY, headline) and has(SAFETY, lead))
                     or (has(SURVEY, body_lead) and has(SAFETY, body_lead)))
    risk_pattern = DATA + '|' + SAFETY + '|' + ACCESS + '|' + POLICY
    digital_risk = ((has(DIGITAL, headline) and has(risk_pattern, headline)) or
                    (has(DIGITAL, body_lead) and has(risk_pattern, body_lead)))
    personal_focus = focus(title)
    if not personal_focus and (survey_safety or (len(body) <= 250 and has(DATA + '|' + POLICY, body_lead))):
        personal_focus = 'Личные данные в содержательном описании'
    if not personal_focus:
        return rejected | dict(reason='Общая кибербезопасность или инфраструктура: связь с личными данными в интернете не показана в заголовке')
    topic = 'Безопасность опросов' if survey_safety else ('Личные данные в интернете' if digital else 'Персональные данные')
    if not survey_safety and has(DEVICE, lead):
        topic = 'Защита файлов и устройств'
    if ai:
        topic = 'Приватность и безопасность ИИ'
    elif digital and has(ACCESS, lead):
        topic = 'Доступность цифровых сервисов'
    if has(r'локализац|трансграничн|передач\w* данн', lead):
        topic = 'Хранение и передача данных'
    if has(r'^(?:федеральный закон|постановление|приказ|политика|условия обработки)', headline):
        kind = 'document'
    elif has(r'вступ\w* в силу|измен\w* требован|принят|оштраф|штраф\w* за|разъяснил|предупредил|утечк|обнаружен|обновил|подписан|увелич\w* штраф', headline):
        kind = 'news'
    elif has(r'обзор|сравнен|выбор сервиса', headline):
        kind = 'review'
    else:
        kind = 'article'
    # Extract an attributed sentence rather than generate an unsupported legal conclusion.
    evidence = next((s for s in re.split(r'(?<=[.!?])\s+|[\r\n]+', body[:700])
                     if has(DATA + '|' + SAFETY + '|' + ACCESS + '|' + POLICY, s.lower().replace('ё','е'))), clean(title))
    evidence = evidence[:650]
    checks = [
        (r'трансграничн|локализац', 'Проверить место хранения данных и их передачу иностранным получателям.'),
        (r'согласи', 'Проверить согласие и основания обработки данных в описанном сценарии.'),
        (r'шифровальщик|вымогател|уязвим|вредонос|взлом|заражен', 'Проверить, затронуты ли ваши устройства и версии программ, и меры защиты в источнике.'),
        (r'утечк|фишинг|мошен', 'Проверить, затрагивает ли описанный риск ваш сервис или аккаунт, и меры защиты в источнике.'),
        (ACCESS, 'Проверить, затрагивает ли изменение доступа ваш сервис и регион.'),
        (POLICY, 'Проверить описанные изменения условий и настройки приватности используемого сервиса.'),
        (r'уведомлен', 'Проверить, касается ли описанное уведомление вашей обработки данных.'),
    ]
    check = next((value for pattern, value in checks if has(pattern, lead)),
                 'Сопоставить описанное требование с вашей обработкой данных; применимость требует проверки.')
    audience = ('Организаторам и участникам онлайн-опросов.' if survey_safety else
                'Пользователям упомянутых устройств и хранилищ.' if topic == 'Защита файлов и устройств' else
                'Пользователям и администраторам упомянутых цифровых сервисов.' if (digital or ai) and kind != 'document' else
                'Тем, кто обрабатывает персональные данные в описанном сценарии.')
    return dict(accepted=True, version=VERSION, reason=topic + ': ' + evidence,
                topics=[topic], content_kind=kind, usefulness=check,
                priority=20 if has(r'штраф|вступ\w* в силу|срок|утечк',lead) else 10,
                risk=evidence, audience=audience)


def verdict(title, description=''):
    result = evaluate(title, description)
    return result if result['accepted'] else None
