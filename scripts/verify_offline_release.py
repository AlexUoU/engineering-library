"""Read-only ZIP preflight. Cold-start HTTP/browser checks run on a fresh extraction."""
import hashlib,json,re,sys,zipfile
from pathlib import Path
from html.parser import HTMLParser
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from translation import validate_quality

class Structure(HTMLParser):
    def __init__(self,text):
        super().__init__();self.counts=Counter();self.assets=[];self.text=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        self.counts[tag]+=1
        if tag=='img':self.assets.append(dict(attrs).get('src',''))
    def handle_data(self,text):self.text.append(text)

def verify(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None,'ZIP CRC failure'
        names=set(archive.namelist());prefix='engineering-library/'
        manifest=json.loads(archive.read(prefix+'data/manifest.json'))
        assert manifest['reader_mode'] and manifest['offline_ready']
        assert not any('translation-models/' in n or '.translation-env/' in n or 'learning.sqlite3' in n or '/backups/' in n for n in names)
        rows=[]
        for sid,article in manifest['articles'].items():
            for version in range(1,article['latest']+1):
                assert prefix+f'data/versions/{sid}/v{version}.json' in names
            current=json.loads(archive.read(prefix+f'data/versions/{sid}/v{article["latest"]}.json'))
            digest=current['source']['digest'];docs=[]
            for folder in ['originals','translations']:
                doc=json.loads(archive.read(prefix+f'data/{folder}/{sid}/{digest}.json'))
                assert doc['source_digest']==digest
                parsed=Structure(doc['html']);assert ''.join(parsed.text).strip()
                for src in parsed.assets:
                    assert src.startswith('/original-assets/'),src
                    assert prefix+'data'+src in names,src
                docs.append((doc,parsed))
            validate_quality(docs[0][0]['html'],docs[1][0]['html'])
            assert docs[0][1].counts==docs[1][1].counts,(sid,'translation structure mismatch')
            assert docs[1][0]['original_hash']==hashlib.sha256(docs[0][0]['html'].encode()).hexdigest()
            assert not any(w.startswith(('插图未缓存','矢量图未缓存')) for w in docs[0][0].get('warnings',[]))
            rows.append(dict(id=sid,version=article['latest'],images=len(docs[0][1].assets),resource_notes=docs[0][0].get('warnings',[])))
        return dict(articles=len(rows),content_as_of=manifest['content_as_of'],size_bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),details=rows)
if __name__=='__main__':print(json.dumps(verify(Path(sys.argv[1])),ensure_ascii=False,indent=2))
