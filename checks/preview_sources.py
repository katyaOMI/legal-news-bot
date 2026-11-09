"""Fetch a bounded public sample and build an offline preview. Never sends Telegram."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from collector.survey import classify_material, read_source, sources


def run(limit=4,from_snapshot=False):
    folder=ROOT/'work'
    folder.mkdir(exist_ok=True)
    fetched_at=None
    def check(source):
        result=read_source(source,limit=limit)
        return dict(source=source['name'],url=source['url'],**result)
    if from_snapshot:
        results=json.loads((folder/'survey-source-results.json').read_text(encoding='utf-8'))
        if (folder/'survey-preview.json').exists():
            fetched_at=json.loads((folder/'survey-preview.json').read_text(encoding='utf-8')).get('checked_at')
            before=folder/'survey-preview-before-T4.html'
            if not before.exists():
                before.write_text((folder/'survey-preview.html').read_text(encoding='utf-8'),encoding='utf-8')
        for result in results:
            result['items']=[item for item in result['items'] if classify_material(item['title'],item['text'])]
            for checked in result['checked']:
                item=checked['item']
                if item and not classify_material(item['title'],item['text']):
                    checked['item']=None
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(check,sources()))
    items=[]
    for result in results:
        for item in result['items']:
            # Only our short explanation and title are exported, not full article text.
            items.append({key:item[key] for key in ('url','title','published_at','topics','content_kind','usefulness')}|{'source':result['source']})
    report=dict(checked_at=fetched_at or datetime.now(timezone.utc).isoformat(),evaluated_at=datetime.now(timezone.utc).isoformat(),telegram='not called',items=items,
        sources=[dict(name=r['source'],url=r['url'],discovered=r['discovered'],checked=len(r['checked']),
            accepted=len(r['items']),errors=r['errors']) for r in results])
    if not from_snapshot:
        (folder/'survey-source-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'survey-effective-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'survey-preview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    cards=[]
    for item in items:
        cards.append('<article><span class="tag">НОВОЕ · '+html.escape(item['topics'][0])+'</span><h2><a href="'+html.escape(item['url'],quote=True)+'">'+html.escape(item['title'])+'</a></h2><p>'+html.escape(item['usefulness'])+'</p><small>'+html.escape(item['source'])+' · '+html.escape((item['published_at'] or 'Дата не указана')[:10])+'</small></article>')
    rows=''.join('<tr><td>'+html.escape(r['name'])+'</td><td>'+str(r['discovered'])+'</td><td>'+str(r['checked'])+'</td><td>'+str(r['accepted'])+'</td><td>'+str(len(r['errors']))+'</td></tr>' for r in report['sources'])
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Материалы для твоего бота</title><style>body{font:17px/1.6 Segoe UI,Arial;background:#f2f5f4;color:#17372f;margin:0}main{max-width:900px;margin:auto;padding:30px 20px}article{background:white;padding:22px;border-radius:12px;margin:18px 0;border:1px solid #d3e0da}h1{line-height:1.2}h2{font-size:21px;line-height:1.4}a{color:#075e50}.tag{font-size:13px;font-weight:700;color:#11774c}small{color:#536c64}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:8px;border-bottom:1px solid #ccd9d1}</style><main><h1>Что бот сможет присылать</h1><p>Реальные публичные материалы. «НОВОЕ» означает первую находку; дата публикации показана отдельно. Это локальный предпросмотр, сообщения в Telegram не отправлялись.</p>'''+''.join(cards)+'<h2>Проверка источников</h2><table><tr><th>Источник</th><th>URL найдено</th><th>Прочитано</th><th>Подошло</th><th>Ошибок</th></tr>'+rows+'</table></main></html>'
    (folder/'survey-preview.html').write_text(page,encoding='utf-8')
    print(json.dumps(dict(materials=len(items),sources=len(results),sources_with_materials=sum(bool(r['items']) for r in results),report=str(folder/'survey-preview.json')),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=4)
    parser.add_argument('--from-snapshot',action='store_true',help='Re-evaluate saved public pages without network requests')
    args=parser.parse_args()
    if not 1<=args.limit<=10:
        parser.error('--limit must be between 1 and 10')
    run(args.limit,args.from_snapshot)
