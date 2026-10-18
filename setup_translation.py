"""One-time local model installation. Python 3.11+ recommended."""
from pathlib import Path
import os
import subprocess
import sys
import venv
from translation import environment


def main():
    root = Path(__file__).resolve().parent
    target = root/'.translation-env'
    venv.create(target, with_pip=True)
    python = target/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root/'translation-requirements.txt')], check=True)
    subprocess.run([str(python), str(root/'translation.py'), '--install-model'], env=environment(root), check=True)
    print('本地翻译已就绪。准备分发包前运行：python library.py prepare')


if __name__ == '__main__':
    main()
