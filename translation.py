"""Local translation worker; reading cached translations has no model dependency."""
import json
import hashlib
from collections import Counter
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import subprocess
import sys


def environment(root):
    root = Path(root).resolve()
    return dict(os.environ, ARGOS_PACKAGES_DIR=str(root/'data/translation-models'),
                ARGOS_MODEL_PROVIDER='OPENNMT', ARGOS_DEVICE_TYPE='cpu',
                XDG_DATA_HOME=str(root/'.translation-state/share'),
                XDG_CONFIG_HOME=str(root/'.translation-state/config'),
                XDG_CACHE_HOME=str(root/'.translation-state/cache'))


def validate_structure(original, translated):
    class Structure(HTMLParser):
        def __init__(self, content):
            super().__init__(convert_charrefs=True)
            self.tags=Counter()
            self.code=[]
            self.depth=0
            self.feed(content)
        def handle_starttag(self, tag, attrs):
            self.tags[tag]+=1
            if tag in {'pre','code'}:self.depth+=1
        def handle_endtag(self, tag):
            if tag in {'pre','code'}:self.depth=max(0,self.depth-1)
        def handle_data(self, data):
            if self.depth:self.code.append(data)
    before,after=Structure(original),Structure(translated)
    if before.tags != after.tags or before.code != after.code:
        raise ValueError('译文段落结构或代码校验失败，保留旧版')


def translate_html(content, root):
    root = Path(root)
    python = root/'.translation-env'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        raise ValueError('本地翻译尚未安装，请先运行 setup_translation.py')
    try:
        result = subprocess.run([str(python), str(root/'translation.py'), '--worker'],
                                input=json.dumps({'html':content}), text=True, encoding='utf-8',
                                capture_output=True, env=environment(root), timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('本篇本地翻译超过 10 分钟，已停止；保留旧版，可重试。') from exc
    if result.returncode:
        message=result.stderr.strip().splitlines()
        raise ValueError('本地翻译失败：'+(message[-1] if message else '翻译进程异常退出'))
    value = json.loads(result.stdout.strip().splitlines()[-1])
    if not value.get('html'):
        raise ValueError('本地翻译未返回正文')
    validate_structure(content,value['html'])
    validate_quality(content,value['html'])
    return value


TERMS = re.compile(r'\b(?:Anthropic|Claude(?: Code| Desktop)?|MCP|LLMs?|SDK|API|Skills|(?i:agents?))\b')


def canonical_term(term):
    return 'agent' if term.lower() in {'agent','agents'} else term


def validate_quality(original, translated):
    """Reject mechanical corruption; semantic review remains a separate step."""
    class Text(HTMLParser):
        def __init__(self, html):
            super().__init__(); self.parts=[]; self.code=0; self.feed(html)
        def handle_starttag(self, tag, attrs):
            if tag in {'code','pre'}: self.code+=1
        def handle_endtag(self, tag):
            if tag in {'code','pre'}: self.code=max(0,self.code-1)
        def handle_data(self, data):
            if not self.code: self.parts.append(data)
    before=' '.join(Text(original).parts)
    after=' '.join(Text(translated).parts)
    if '\ufffd' in after or re.search(r'ZXQ\d+QXZ|(?:。\s*){2,}',after):
        raise ValueError('译文存在乱码、占位符或异常重复标点，请校对后发布')
    if re.search(r'页\s*[:：]\s*1|单位\s*[:：]\s*千美元|E/CN\.4',after) and not re.search(r'page\s*1|thousands? of dollars|E/CN\.4',before,re.I):
        raise ValueError('译文疑似插入无来源文字，请对照英文复核')
    expected=len(re.findall(r'\bagents?\b',before,re.I))
    actual=len(re.findall(r'(?<![A-Za-z])agent(?![A-Za-z])',after))
    if actual < expected:
        raise ValueError('译文未完整保留 agent 术语，请校对后发布')


def translate_text(text, translate):
    if not any(c.isalpha() for c in text):
        return text,False
    names=[]
    def protect(match):
        token='ZXQ%dQXZ'%len(names)
        names.append((token,canonical_term(match.group())))
        return token
    translated=translate(TERMS.sub(protect,text))
    if all(translated.count(token)==1 for token,_ in names):
        for token,name in names:
            translated=translated.replace(token,name)
        return translated,False
    # Some models rewrite placeholder tokens. Retry the surrounding text while
    # keeping the actual identifiers outside the model, rather than rejecting a whole article.
    def segment(value):
        if not any(c.isalpha() for c in value):
            return value
        result=translate(value)
        if not result.strip():
            raise ValueError('本地模型返回空译文，本次不发布该版本')
        return result
    parts=[]
    end=0
    for match in TERMS.finditer(text):
        before=text[end:match.start()]
        parts.extend([segment(before),canonical_term(match.group())])
        end=match.end()
    tail=text[end:]
    parts.append(segment(tail))
    return ''.join(parts),True


def worker():
    import argostranslate.package
    import argostranslate.translate
    if '--install-model' in sys.argv:
        if any(p.from_code == 'en' and p.to_code == 'zh' for p in argostranslate.package.get_installed_packages()):
            print('已存在本地英译中文模型。')
            return
        argostranslate.package.update_package_index()
        package = next(p for p in argostranslate.package.get_available_packages()
                       if p.from_code == 'en' and p.to_code == 'zh')
        argostranslate.package.install_from_path(package.download())
        print('英译简体中文模型已安装。')
        return
    from bs4 import BeautifulSoup
    from opencc import OpenCC
    translation = argostranslate.translate.get_translation_from_codes('en','zh')
    source = json.load(sys.stdin)['html']
    soup = BeautifulSoup(source, 'html.parser')
    checkpoint = Path(os.environ['XDG_CACHE_HOME'])/'paragraphs-v2'/(hashlib.sha256(source.encode()).hexdigest()+'.json')
    try:
        completed = json.loads(checkpoint.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        completed = {}
    checkpoint.parent.mkdir(parents=True,exist_ok=True)
    # Reuse completed paragraphs when only the image markup was repaired.
    for previous in checkpoint.parent.glob('*.json'):
        if previous != checkpoint:
            try:
                for key,value in json.loads(previous.read_text(encoding='utf-8')).items():
                    completed.setdefault(key,value)
            except (OSError,ValueError):
                pass
    simplified = OpenCC('t2s')
    fallbacks=0
    for node in list(soup.find_all(string=True)):
        text = str(node)
        if text.strip() and not node.find_parent(['pre','code']):
            key=hashlib.sha256(text.encode()).hexdigest()
            if key in completed:
                translated,fallback=completed[key]
            else:
                translated,fallback=translate_text(text,translation.translate)
                if not translated.strip():
                    raise ValueError('本地模型返回空译文，本次不发布该版本')
                completed[key]=[translated,fallback]
                temporary=checkpoint.with_suffix('.tmp')
                temporary.write_text(json.dumps(completed,ensure_ascii=False),encoding='utf-8')
                temporary.replace(checkpoint)
            if not translated.strip():
                raise ValueError('本地模型返回空译文，本次不发布该版本')
            fallbacks+=int(fallback)
            node.replace_with(simplified.convert(translated))
    result = str(soup)
    print(json.dumps({'html':result, 'engine':'Argos Translate', 'language':'zh-Hans', 'terminology_fallbacks':fallbacks}, ensure_ascii=False))


if __name__ == '__main__':
    os.environ.update(environment(Path(__file__).resolve().parent))
    worker()
