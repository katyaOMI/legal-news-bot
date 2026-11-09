"""Bounded public-page discovery. No paid API, database or Telegram calls here."""
import hashlib
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from collector.feeds import canonical_url
from legal_news.core import clean

CATALOG = Path(__file__).with_name('survey_sources.json')
MAX_BYTES = 4_000_000
MAX_PAGES = 6


def sources():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def classify_material(title, description=''):
    """Explain topic matches; general data regulation is relevant without 'survey'."""
    title = clean(title).lower().replace('ё', 'е')
    text = (title + ' ' + clean(description)[:4000]).lower().replace('ё', 'е')
    # Owner feedback T4: sample-size calculations are outside this digest.
    if re.search(r'сколько\s+(?:респондент|человек)|(?:размер|объем|численност)\w*\s+выборк|расчет\w*\s+(?:количеств\w*\s+)?респондент',title):
        return None
    survey = re.search(r'опрос|анкет|респондент|\bnps\b|\benps\b|\bcustdev\b|survey|questionnaire|маркетингов\w* исследован', text)
    data = re.search(r'персональн\w* данн|152[‑–—-]фз|обезличиван|трансграничн\w* передач', text)
    # Avoid short generic pages, results-only headlines and unrelated data science.
    if not survey and not data:
        return None
    title_signal = re.search(r'опрос|анкет|респондент|\bnps\b|\bcustdev\b|survey|исследован|персональн\w* данн|152[‑–—-]фз',title)
    if not title_signal and not re.search(r'персональн\w* данн|трансграничн\w* передач',text[:700]):
        if len(re.findall(r'опрос|анкет|респондент|\bnps\b|survey',text)) < 2:
            return None
    if re.search(r'^(?:результаты|итоги) (?:голосования|опроса)', title) and not data:
        return None
    if re.search(r'заработ(?:ать|ок).*опрос|опрос.*заработ', title) and not re.search(r'безопас|мошен|риск|защит', text):
        return None
    topics = []
    if data:
        topics.append('Персональные данные')
    if survey and re.search(r'безопас|мошен|конфиденц|анонимн|защит|утечк', text):
        topics.append('Безопасность опросов')
    if survey and re.search(r'сервис|платформ|конструктор|форм[аыу]|инструмент|google forms|гугл', text):
        topics.append('Инструменты опросов')
    if survey:
        topics.append('Методика исследований')
    if re.search(r'федеральный закон|постановление|приказ|152-фз', title):
        kind = 'document'
    elif re.search(r'обзор|сервис|платформ|конструктор|инструмент', title):
        kind = 'review'
    elif re.search(r'как |зачем |метод|анкет|опрос', title):
        kind = 'guide'
    else:
        kind = 'article'
    benefits = {
        'Персональные данные': 'Помогает разобраться с требованиями к сбору, хранению и использованию данных.',
        'Безопасность опросов': 'О рисках для участников и защите данных при проведении опросов.',
        'Инструменты опросов': 'Помогает выбрать или настроить инструмент для анкетирования.',
        'Методика исследований': 'Помогает подготовить исследование, анкету или работу с респондентами.',
    }
    preferred = next((t for t in topics if t != 'Персональные данные'), topics[0])
    return dict(topics=topics, content_kind=kind, usefulness=benefits[preferred])


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.meta = [], {}
        self.title_parts, self.h1_parts, self.body_parts, self.article_parts = [], [], [], []
        self.specific_parts, self.specific_depth = [], None
        self.stack = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key = attrs.get('property') or attrs.get('name') or attrs.get('itemprop')
            if key:
                self.meta[key.lower()] = attrs.get('content', '')
        if tag == 'time' and attrs.get('datetime') and 'datepublished' not in self.meta:
            self.meta['datepublished'] = attrs['datetime']
        if tag == 'a' and attrs.get('href'):
            self.links.append([attrs['href'], ''])
            self.anchor = self.links[-1]
        if tag not in {'meta','link','img','br','hr','input','source','wbr','area','base','embed','param','track'}:
            self.stack.append(tag)
            if attrs.get('itemprop','').lower() == 'articlebody':
                self.specific_depth = len(self.stack)
        if tag in {'p','div','li','h1','h2','h3','br'}:
            self.body_parts.append('\n')
            self.article_parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'a':
            self.anchor = None
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack)-1-self.stack[::-1].index(tag)]
            if self.specific_depth is not None and len(self.stack) < self.specific_depth:
                self.specific_depth = None

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor[1] += data
        if any(t in self.stack for t in ('script','style','svg','noscript','nav','footer','header','aside')):
            return
        if 'title' in self.stack:
            self.title_parts.append(data)
            return
        if 'h1' in self.stack:
            self.h1_parts.append(data)
        self.body_parts.append(data + ' ')
        if 'article' in self.stack or 'main' in self.stack:
            self.article_parts.append(data + ' ')
        if self.specific_depth is not None:
            self.specific_parts.append(data + ' ')


def page_date(meta):
    for key in ('article:published_time','datepublished','date','pubdate'):
        value = meta.get(key, '').strip()
        if not value:
            continue
        try:
            moment = datetime.fromisoformat(value.replace('Z','+00:00'))
            return moment.replace(tzinfo=moment.tzinfo or timezone.utc).isoformat()
        except ValueError:
            continue
    return None


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = canonical_url(newurl)
        # Redirects must stay on the curated source domain (www alias allowed).
        if urllib.parse.urlsplit(target).hostname.removeprefix('www.') != urllib.parse.urlsplit(req.full_url).hostname.removeprefix('www.'):
            raise ValueError('Переадресация за пределы источника')
        return super().redirect_request(req,fp,code,msg,headers,target)


def fetch_page(url):
    url = canonical_url(url)
    request = urllib.request.Request(url, headers={
        'User-Agent':'SurveyResearchReader/1.0', 'Accept':'text/html,application/xhtml+xml'})
    opener = urllib.request.build_opener(PublicRedirect())
    with opener.open(request, timeout=12) as response:
        body = response.read(MAX_BYTES+1)
        charset = response.headers.get_content_charset() or 'utf-8'
    if len(body) > MAX_BYTES:
        raise ValueError('Страница превышает допустимый размер')
    return body.decode(charset, errors='replace')


def parse_page(text):
    page = Page()
    page.feed(text)
    return page


def article(url, text):
    page = parse_page(text)
    title = clean(page.meta.get('og:title') or ''.join(page.h1_parts) or ''.join(page.title_parts))
    if not title or re.search(r'access denied|just a moment|captcha|доступ ограничен|проверка браузера', title, re.I):
        raise ValueError('Нет доступного текста публикации')
    description = clean(page.meta.get('description') or page.meta.get('og:description'))
    body = clean(' '.join(page.specific_parts) or (' '.join(page.article_parts) if clean(' '.join(page.article_parts)) else ' '.join(page.body_parts)))
    # The menu cannot supply a topic: use metadata/H1 and content after H1 only.
    if not clean(' '.join(page.article_parts)) and page.h1_parts:
        h1 = clean(''.join(page.h1_parts))
        index = body.find(h1)
        if index >= 0:
            body = body[index+len(h1):]
    verdict = classify_material(title, description or body[:2500])
    if not verdict:
        return None
    excerpt = (description + ' ' + body[:5000]).strip()
    return dict(url=canonical_url(url), title=title[:1000], text=excerpt,
        published_at=page_date(page.meta),
        dedup_key=hashlib.sha256(('page:'+canonical_url(url)).encode()).hexdigest(),
        **verdict, metadata=dict(kind='public_page',published_at=page_date(page.meta)))


def discover(source, text):
    host = urllib.parse.urlsplit(source['url']).hostname.removeprefix('www.')
    result = []
    for href, label in parse_page(text).links:
        try:
            url = canonical_url(urllib.parse.urljoin(source['url'],href))
        except ValueError:
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname.removeprefix('www.') != host or parsed.query:
            continue
        if re.fullmatch(source['pattern'],parsed.path) and url not in result:
            result.append(url)
        if len(result) >= 200:
            break
    return result


def read_source(source, known=(), fetch=fetch_page, limit=MAX_PAGES):
    """Seed then discovery; checked URLs are not retried until the caller's TTL."""
    urls = list(source.get('seeds', []))
    errors = []
    if not source.get('seed_only'):
        try:
            urls.extend(discover(source, fetch(source['url'])))
        except Exception as error:
            errors.append(dict(url=source['url'],error=type(error).__name__))
    urls = list(dict.fromkeys(urls))
    selected = [url for url in urls if url not in known][:limit]
    def load(url):
        try:
            return dict(url=url,item=article(url,fetch(url)),error=None)
        except Exception as error:
            return dict(url=url,item=None,error=type(error).__name__)
    with ThreadPoolExecutor(max_workers=3) as pool:
        checked = list(pool.map(load,selected))
    items = [row['item'] for row in checked if row['item']]
    errors.extend(dict(url=row['url'],error=row['error']) for row in checked if row['error'])
    if not urls:
        errors.append(dict(url=source['url'],error='NoArticleLinks'))
    return dict(items=items,checked=checked,errors=errors,discovered=len(urls))
