"""Exercise the released launcher in a fresh temporary extraction."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

archive = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='reader-launcher-') as folder:
    with zipfile.ZipFile(archive) as z:
        z.extractall(folder)
        for name in ['工程实践.app','停止工程实践.app']:
            info=z.getinfo('engineering-library/'+name+'/Contents/MacOS/launch')
            assert (info.external_attr >> 16) & 0o111, 'Missing executable permission'
    root=Path(folder)/'engineering-library'
    command=[sys.executable,str(root/'launcher.py')]
    def start():
        return subprocess.check_output(command+['--no-open'],text=True,timeout=20).strip()
    try:
        first=start()
        # Launcher has exited; the detached service must remain available.
        with urllib.request.urlopen(first+'api/catalog') as r:
            assert len(json.load(r)['articles'])==26
        assert start()==first, 'Repeated start must reuse the same service'
        subprocess.run(command+['--stop'],check=True,timeout=10)
        time.sleep(1)
        try:
            urllib.request.urlopen(first+'api/catalog',timeout=2)
        except OSError:
            pass
        else:
            raise AssertionError('Stop did not stop the service')
        restarted=start()
        with urllib.request.urlopen(restarted+'api/article?id=building-effective-agents') as r:
            assert r.status==200
        print(json.dumps({'passed':True,'checks':['ZIP app executable bits','service survives launcher exit','26 articles available','repeat start reused service','authenticated stop','restart and article read']},ensure_ascii=False,indent=2))
    finally:
        subprocess.run(command+['--stop'],check=True,timeout=10)
        time.sleep(1)
