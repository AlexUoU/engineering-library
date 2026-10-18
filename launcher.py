"""macOS background launcher; runtime files stay beside this copy of the reader."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'data' / 'runtime'


def running():
    try:
        state = json.loads((RUNTIME / 'server.json').read_text())
        with urllib.request.urlopen(state['url'] + 'api/catalog', timeout=2) as response:
            catalog = json.load(response)
        return state if catalog.get('token') == state['token'] else None
    except (OSError, ValueError, KeyError):
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stop', action='store_true')
    p.add_argument('--no-open', action='store_true')
    args = p.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / 'launcher.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = running()
        if args.stop:
            if state:
                req = urllib.request.Request(state['url'] + 'api/shutdown', data=b'{}', headers={'Content-Type':'application/json','X-Library-Token':state['token']})
                with urllib.request.urlopen(req, timeout=3) as response:
                    response.read()
            return
        if not state:
            with (RUNTIME / 'server.log').open('ab') as log:
                proc = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', '0', '--no-open', '--no-check', '--runtime-file', str(RUNTIME / 'server.json')], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError('阅读服务未能启动，请查看 data/runtime/server.log。')
                state = running()
                if state:
                    break
                time.sleep(.1)
            if not state:
                proc.terminate()
                raise RuntimeError('启动等待超时，请重新打开阅读器。日志位于 data/runtime/server.log。')
        if not args.no_open:
            webbrowser.open(state['url'])
        print(state['url'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        if sys.platform == 'darwin':
            subprocess.run(['osascript', '-e', 'on run argv', '-e', 'display alert "工程实践无法启动" message (item 1 of argv)', '-e', 'end run', str(exc)])
        sys.exit(1)
