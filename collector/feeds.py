"""Free publisher RSS feeds. Titles and provided announcements only, no paywalls."""
import hashlib
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from legal_news.core import clean, topics_for

SOURCES = [
    ('Касперский: блог безопасности', 'https://www.kaspersky.ru/blog/feed/'),
    ('КонсультантПлюс: горячие документы', 'https://www.consultant.ru/rss/hotdocs.xml'),
    ('КонсультантПлюс: федеральное законодательство', 'https://www.consultant.ru/rss/fd.xml'),
    ('КонсультантПлюс: новости для юриста', 'https://www.consultant.ru/rss/nw.xml'),
    ('ГАРАНТ: новости', 'https://rss.garant.ru/categories/news/'),
    ('ГАРАНТ: федеральные документы', 'https://rss.garant.ru/hotlaw/federal/'),
]


def canonical_url(url):
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Недопустимая ссылка публикации')
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k,v) for k,v in query if not k.lower().startswith('utm_')]
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,urllib.parse.urlencode(query),''))


def dedup_key(title):
    # Same named act can appear in multiple publisher feeds with different URLs.
    title = clean(title).lower().replace('ё','е')
    official = re.match(r'^(.{5,220}?)\s+от\s+(\d{2}\.\d{2}\.\d{4})\s*(?:№|n)\s*([\w/–—-]+)', title)
    if official:
        authority, day, number = official.groups()
        value = 'act:'+ ' '.join(authority.split())+':'+day+':'+number
    else:
        value = 'title:'+re.sub(r'[^\w]+',' ',title).strip()
    return hashlib.sha256(value.encode()).hexdigest()


def relevant_topics(title, description=''):
    from legal_news.relevance import verdict
    result = verdict(title, description)
    return result['topics'] if result else []


def fetch_bytes(url):
    req=urllib.request.Request(url,headers={'User-Agent':'LegalNewsMonitor/0.1','Accept':'application/rss+xml, application/xml, text/xml'})
    with urllib.request.urlopen(req,timeout=15) as response:
        body=response.read(4_000_001)
    if len(body)>4_000_000:
        raise ValueError('RSS превышает допустимый размер')
    return body


def parse_rss(body, since=None):
    # Reject DTDs/entities before parsing untrusted remote XML.
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)',body,re.I):
        raise ValueError('DTD и внешние сущности в RSS запрещены')
    root=ET.fromstring(body)
    if root.tag!='rss' or root.find('channel') is None:
        raise ValueError('Источник вернул не RSS-ленту')
    result=[]
    for element in root.findall('./channel/item'):
        title=clean(element.findtext('title'))
        raw_description=element.findtext('description') or ''
        description=clean(raw_description)
        link=canonical_url(element.findtext('link') or '')
        raw_date=element.findtext('pubDate')
        if not title or not raw_date:
            raise ValueError('В публикации нет названия или даты')
        published=parsedate_to_datetime(raw_date)
        if published.tzinfo is None:
            raise ValueError('В дате RSS не указан часовой пояс')
        published=published.astimezone(timezone.utc)
        if since and published < since:
            continue
        categories=[clean(node.text) for node in element.findall('category')]
        result.append(dict(url=link,title=title,text=description,published_at=published.isoformat(),
            dedup_key=dedup_key(title),metadata=dict(title=title,description=raw_description,
            link=link,pubDate=raw_date,categories=categories)))
    return result


def read_rss(url,since=None,fetch=fetch_bytes):
    return parse_rss(fetch(url),since)
