"""Public-source preview with accepted/rejected reasons. No database or Telegram."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from collector.survey import sources, read_source
from collector.feeds import SOURCES, read_rss
from legal_news.relevance import evaluate
from bot.main import finding_lines


def run(limit=6,from_snapshot=False):
    folder=ROOT/'work'
    folder.mkdir(exist_ok=True)
    raw=folder/'privacy-source-results.json'
    if from_snapshot:
        if raw.exists():
            results=json.loads(raw.read_text(encoding='utf-8'))
        else:
            results=json.loads((folder/'survey-source-results.json').read_text(encoding='utf-8'))
    else:
        jobs=[('web',s) for s in sources()]+[('rss',s) for s in SOURCES]
        def fetch(job):
            kind,source=job
            name,url=(source['name'],source['url']) if kind=='web' else source
            try:
                if kind=='web':
                    result=read_source(source,limit=limit)
                    return dict(source=name,url=url,**result)
                items=read_rss(url)
                return dict(source=name,url=url,items=items,errors=[],discovered=len(items),checked=[])
            except Exception as error:
                return dict(source=name,url=url,items=[],errors=[dict(error=type(error).__name__)],discovered=0,checked=[])
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(fetch,jobs))
        raw.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    accepted,rejected=[],[]
    seen=set()
    for result in results:
        candidates=list(result['items'])
        # Previously accepted legacy snapshots may contain now-rejected material.
        candidates += [r['item'] for r in result.get('checked',[]) if r.get('item')]
        for item in candidates:
            if item['url'] in seen:
                continue
            seen.add(item['url'])
            decision=evaluate(item['title'],item.get('text',''))
            base=dict(title=item['title'],url=item['url'],source=result['source'],published_at=item.get('published_at'))
            (accepted if decision['accepted'] else rejected).append(base|decision)
    accepted.sort(key=lambda r:(r['content_kind']=='news',r['published_at'] or ''),reverse=True)
    def card(row):
        data=(row['title'],', '.join(row['topics']),row['url'],row['source'],row['published_at'],
              row['content_kind'],row['usefulness'],row['risk'],row['audience'])
        return '<article>'+finding_lines([data])[0].replace('\n','<br>')+'</article>'
    rows=''.join('<tr><td>'+html.escape(r['source'])+'</td><td>'+str(len(r['items']))+'</td><td>'+str(len(r['errors']))+'</td></tr>' for r in results)
    rejected_html=''.join('<li>'+html.escape(r['title'])+' — '+html.escape(r['reason'])+'</li>' for r in rejected)
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Правовые риски опросов — проверка отбора</title><style>body{font:17px/1.6 Segoe UI,Arial;background:#f2f5f4;color:#17372f}main{max-width:900px;margin:auto;padding:24px}article{background:white;padding:24px;border-radius:12px;margin:20px 0;overflow-wrap:anywhere}a{color:#075e50}td{padding:8px;border-bottom:1px solid #ccc}h1{line-height:1.2}</style><main><h1>Правовые риски и безопасность опросов</h1><p>Локальный предпросмотр. Telegram не вызывается. «НОВОЕ» — только новости последних 72 часов; источник, дата и применимость требуют проверки. Это ограниченная выборка, а не полный мониторинг.</p>'''
    page+=''.join(map(card,accepted)) or '<p>Подходящих материалов в этой выборке не найдено.</p>'
    page+='<h2>Исключено</h2><ul>'+rejected_html+'</ul><h2>Источники</h2><table><tr><th>Ресурс</th><th>Кандидатов</th><th>Ошибок</th></tr>'+rows+'</table></main></html>'
    (folder/'privacy-preview.html').write_text(page,encoding='utf-8')
    report=dict(evaluated_at=datetime.now(timezone.utc).isoformat(),from_snapshot=from_snapshot,
                accepted=accepted,rejected=rejected,sources=[dict(name=r['source'],errors=r['errors'],candidates=len(r['items'])) for r in results],telegram='never called')
    (folder/'privacy-preview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(accepted=len(accepted),rejected=len(rejected),sources=len(results),errors=sum(bool(r['errors']) for r in results)),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=6)
    parser.add_argument('--from-snapshot',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=10: parser.error('--limit must be 1..10')
    run(args.limit,args.from_snapshot)
