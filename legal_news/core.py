"""Public legal-source readers and topic rules. No model API or local website."""
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://publication.pravo.gov.ru/api/Documents"
MOSCOW = timezone(timedelta(hours=3))
BLOCKS = ("president", "assembly", "government", "federal_authorities")


def today():
    return datetime.now(MOSCOW).date().isoformat()

def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).split())

def topics_for(title):
    rules = json.loads((Path(__file__).with_name("topics.json")).read_text(encoding="utf-8-sig"))
    text = title.lower().replace("ё", "е")
    return [topic for topic, patterns in rules.items() if any(re.search(p, text) for p in patterns)]

def request_json(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "LegalNewsMonitor/0.1 (daily public publications)",
        "Accept": "application/json"})
    # No certificate bypasses and no fallback to paid or third-party providers.
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(10_000_001)
    if len(body) > 10_000_000:
        raise ValueError("Ответ источника превышает допустимый размер")
    return json.loads(body.decode("utf-8-sig"))

def publications(start, end, fetch=request_json):
    """Read every page for each federal non-court block, fail on truncation."""
    unique = {}
    for block in BLOCKS:
        previous_page = None
        for index in range(1, 201):
            query = urllib.parse.urlencode(dict(index=index, pageSize=100,
                startDate=start, endDate=end, block=block))
            payload = fetch(API + "?" + query)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("Неожиданный формат ответа официального источника")
            items = payload["items"]
            pages = payload.get("pagesTotalCount")
            if pages is None:
                raise ValueError("Источник не сообщил число страниц; полнота сбора не подтверждена")
            pages = int(pages)
            if pages < 0 or pages > 200 or (not items and pages >= index):
                raise ValueError("Неполный ответ или превышен предел страниц")
            signature = json.dumps(items, ensure_ascii=False, sort_keys=True)
            if items and signature == previous_page:
                raise ValueError("Источник повторяет страницу; сбор остановлен")
            previous_page = signature
            for item in items:
                identifier = str(item.get("eoNumber", ""))
                if not re.fullmatch(r"\d{16}", identifier):
                    raise ValueError("В публикации отсутствует корректный номер")
                raw_date = str(item.get("publishDateShort") or "")[:10]
                published = date.fromisoformat(raw_date).isoformat()
                if not start <= published <= end:
                    raise ValueError("Источник не соблюдает заданный период публикаций")
                title = clean(item.get("title"))
                name = clean(item.get("name"))
                complex_name = clean(item.get("complexName"))
                title = " — ".join(dict.fromkeys(t for t in (complex_name, title, name) if t))
                if not title:
                    raise ValueError("Пустое название документа")
                unique[identifier] = dict(id=identifier, title=title,
                    published_at=published, metadata=item,
                    url="https://publication.pravo.gov.ru/document/" + identifier)
            if index >= pages:
                break
        else:
            raise ValueError("Сбор не завершён: превышен предел страниц")
    return list(unique.values())
