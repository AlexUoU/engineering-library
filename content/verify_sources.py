"""Build a dated metadata baseline. Does not save article full text."""
import concurrent.futures
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library import fetch, Page, discover, now, SOURCE


def main():
    path=Path(__file__).with_name('seed.json')
    seed=json.loads(path.read_text(encoding='utf-8'))
    source,_=fetch(SOURCE)
    urls=discover(source)
    expected={a['url'] for a in seed['articles']}
    if set(urls)!=expected:
        raise RuntimeError('栏目与初始文章清单不一致，请人工核对：'+str(set(urls)^expected))
    def read(a):
        raw,resolved=fetch(a['url'])
        snap=Page(raw).snapshot(a['url'],resolved)
        return a['id'],snap
    results={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for sid,snap in pool.map(read,seed['articles']):
            results[sid]=snap
            print(sid, 'OK', flush=True)
    for a in seed['articles']:
        a['source']=results[a['id']]
    seed['created_at']=now()
    path.write_text(json.dumps(seed,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Verified',len(results),'articles; no full article text saved.')


if __name__=='__main__':
    main()
