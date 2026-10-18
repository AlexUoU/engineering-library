"""Mechanical checks only; the review reports document semantic checks."""
import json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from translation import validate_structure,validate_quality

def audit(root):
    manifest=json.loads((root/'data/manifest.json').read_text())
    rows=[]
    for sid,article in manifest['articles'].items():
        version=json.loads((root/f'data/versions/{sid}/v{article["latest"]}.json').read_text())
        digest=version['source']['digest']
        original=json.loads((root/f'data/originals/{sid}/{digest}.json').read_text())
        translated=json.loads((root/f'data/translations/{sid}/{digest}.json').read_text())
        errors=[]
        for check in [validate_structure,validate_quality]:
            try:check(original['html'],translated['html'])
            except ValueError as error:errors.append(str(error))
        rows.append(dict(id=sid,errors=errors))
    return dict(scope='Latest versions; mechanical checks do not certify semantic accuracy',articles=len(rows),passed=all(not r['errors'] for r in rows),results=rows)

if __name__=='__main__':
    result=audit(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1])
    print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(0 if result['passed'] else 1)
