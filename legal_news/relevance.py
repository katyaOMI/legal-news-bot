"""Conservative shared selection for privacy risks, not research methodology."""
import re

from legal_news.core import clean

VERSION = 'privacy-risk-v2'
DATA = r'персональн\w*\s+данн|152[‑–—-]\s*фз|субъект\w*\s+данн|personal data|data protection'
SURVEY = r'\bопрос\w*|\bанкет\w*|\bреспондент\w*|\bsurvey\b|\bquestionnaire\b|\btypeform\b|\btestograf\b|\bанкетолог\b|google forms|яндекс форм'
SAFETY = r'\bбезопас|\bконфиденц|\bприватност|\bфишинг|\bмошен|\bутечк|\bзащит|\bлокализац|\bтрансграничн|\bсогласи|\bprivacy\b|\bphishing\b|\bbreach\b'


def evaluate(title, description=''):
    headline = clean(title).lower().replace('ё', 'е')
    body = clean(description)[:5000]
    text = headline + ' ' + body.lower().replace('ё', 'е')
    # The headline or first substantive paragraph must carry the actual risk.
    lead = headline + ' ' + body[:700].lower().replace('ё', 'е')
    def has(pattern, value=text):
        return bool(re.search(pattern, value))
    rejected = dict(accepted=False, reason='Нет подтверждённой связи с приватностью и рисками опросов', version=VERSION)
    foreign = has(r'\bgdpr\b|евросоюз|европейск\w* союз|\bccpa\b|калифорни|\buk\b|\beu\b|\bсша\b|\bес\b|британи|германи|франци')
    russian = has(r'росси|\bрф\b|роскомнадзор|\bркн\b|152[‑–—-]|из россии|российск')
    if foreign and not russian:
        return rejected | dict(reason='Зарубежная применимость к работе из России не подтверждена')
    methodology = has(r'как составить анкет|как провести опрос|привлекать участник|привлечь участник|где искать.*участник|индекс лояльности|\bnps\b|\benps\b|размер\w* выборк|сколько респондент|метод\w* наблюден', headline)
    title_risk = has(DATA + '|' + SAFETY, headline)
    if methodology and not title_risk:
        return rejected | dict(reason='Методология, рекрутинг или расчёт показателей')
    if has(r'gemini|нейросет|искусственн\w* интеллект|\bии\b', headline) and not has(SURVEY, lead):
        return rejected | dict(reason='Общая новость ИИ без связи с данными опросов')
    data = has(DATA, lead)
    body_lead=body[:700].lower().replace('ё','е')
    survey_safety = ((has(SURVEY, headline) and has(SAFETY, lead))
                     or (has(SURVEY, body_lead) and has(SAFETY, body_lead)))
    if not data and not survey_safety:
        return rejected
    topic = 'Безопасность опросов' if survey_safety else 'Персональные данные'
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
    sentences = re.split(r'(?<=[.!?])\s+|[\r\n]+', body)
    evidence = next((s for s in sentences if has(DATA + '|' + SAFETY, s.lower())), clean(title))
    evidence = evidence[:650]
    checks = [
        (r'трансграничн|локализац', 'Проверить место хранения ответов и передачу данных иностранным получателям.'),
        (r'согласи', 'Проверить согласие и основания обработки данных респондентов.'),
        (r'утечк|фишинг|мошен', 'Проверить доступ к ответам и признаки компрометации используемого сервиса.'),
        (r'уведомлен', 'Проверить, касается ли описанное уведомление вашей обработки данных.'),
    ]
    check = next((value for pattern, value in checks if has(pattern, lead)),
                 'Сопоставить описанное требование с вашей обработкой данных; применимость требует проверки.')
    return dict(accepted=True, version=VERSION, reason=topic + ': ' + evidence,
                topics=[topic], content_kind=kind, usefulness=check,
                priority=20 if has(r'штраф|вступ\w* в силу|срок|утечк',lead) else 10,
                risk=evidence, audience='Организаторам опросов при обработке данных респондентов; применимость требует проверки.')


def verdict(title, description=''):
    result = evaluate(title, description)
    return result if result['accepted'] else None
