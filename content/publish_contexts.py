"""One-time editorial revision; run with the local server stopped."""
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from library import Library, ROOT, now, write_json


def main():
    content=ROOT/'content'
    contexts=json.loads((content/'problem_contexts.json').read_text(encoding='utf-8'))
    seed=json.loads((content/'seed.json').read_text(encoding='utf-8'))
    assert set(contexts)=={a['id'] for a in seed['articles']}
    for lesson in seed['articles']:
        assert contexts[lesson['id']]!=lesson['summary']
        lesson['sections'][0]['body']=contexts[lesson['id']]
    seed['edition']='1.1'
    write_json(content/'seed.json',seed)
    lib=Library()
    before=lib.export_learning()
    changed=0
    for sid,body in contexts.items():
        article=lib.manifest['articles'][sid]
        old=lib.version(sid)
        if old['lesson']['sections'][0]['body']==body:
            continue
        retained=lib.version_path(sid,old['version']).read_bytes()
        updated=copy.deepcopy(old)
        updated['lesson']['sections'][0]['body']=body
        updated.update(version=article['latest']+1,saved_at=now(),
                       reason='编辑修订：正文首节补充具体问题情境，避免重复顶部摘要')
        write_json(lib.version_path(sid,updated['version']),updated)
        assert lib.version_path(sid,old['version']).read_bytes()==retained
        article['latest']=updated['version']
        lib.event(sid,'editorial',updated['reason'])
        changed+=1
    if changed:
        lib.manifest['updated_at']=now()
    lib.save()
    after=lib.export_learning()
    for key in ['reflections','reading','drafts']:
        assert before[key]==after[key],key
    print('Editorial versions published:',changed,'; learning records unchanged.')


if __name__=='__main__':main()
