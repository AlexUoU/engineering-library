"""Local original-reader support. Source documents are cached on user request."""
import hashlib
import html
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener
from library import SafeRedirect

IMAGE_HOSTS = {'www-cdn.anthropic.com','cdn.sanity.io','assets.claude.com','code.claude.com','www.anthropic.com'}
TAGS = {'h1','h2','h3','h4','h5','p','pre','code','ul','ol','li','blockquote','table','thead','tbody','tr','th','td','strong','b','em','i','a','br','hr','figure','figcaption','sup','sub'}
SKIP = {'script','style','nav','footer','noscript','button','svg','form','iframe','video','audio'}
VOID = {'br','hr','img','input','meta','link','source','wbr','area','base','embed','param','track','col'}


SVG_TAGS = {'svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','defs','linearGradient','radialGradient','stop','clipPath','mask','title','desc','use'}
SVG_ATTRS = {'viewBox','width','height','x','y','x1','x2','y1','y2','cx','cy','r','rx','ry','d','points','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-dasharray','stroke-dashoffset','fill-rule','clip-rule','opacity','fill-opacity','stroke-opacity','transform','id','offset','stop-color','stop-opacity','gradientUnits','gradientTransform','clip-path','mask','font-size','font-family','font-weight','text-anchor','dominant-baseline','preserveAspectRatio','href'}

def cache_svg(data, folder):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('SVG external definitions are unsupported')
    root=ET.fromstring(data)
    def clean(node):
        tag=node.tag.split('}')[-1]
        if tag not in SVG_TAGS:return False
        node.tag='{http://www.w3.org/2000/svg}'+tag
        for key,value in list(node.attrib.items()):
            name=key.split('}')[-1]
            if name not in SVG_ATTRS or (name=='href' and not value.startswith('#')) or (('url(' in value.lower()) and not re.fullmatch(r'url\(#[\w-]+\)',value)):
                del node.attrib[key]
        for child in list(node):
            if not clean(child):node.remove(child)
        return True
    if root.tag.split('}')[-1]!='svg' or not clean(root):raise ValueError('Invalid SVG')
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    payload=ET.tostring(root,encoding='utf-8')
    name=hashlib.sha256(payload).hexdigest()+'.svg'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/name
    if not target.exists():target.write_bytes(payload)
    return '/original-assets/'+name


class ReaderHTML(HTMLParser):
    def __init__(self, source, url, image_cache, inline_cache=None):
        super().__init__(convert_charrefs=True)
        self.url, self.image_cache = url, image_cache
        self.inline_cache, self.svg_depth, self.svg_parts = inline_cache, 0, []
        self.svg_names = []
        self.target = 'article' if re.search(r'<article[\s>]',source,re.I) else 'main'
        self.stack, self.output, self.warnings = [], [], []
        self.depth, self.stopped, self.started = 0, False, False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if self.svg_depth:
            self.svg_parts.append(self.get_starttag_text())
            self.svg_names.append(re.match(r'<([^\s/>]+)',self.get_starttag_text()).group(1))
            if tag=='svg':self.svg_depth+=1
            return
        active=self.depth>0
        if tag=='svg' and active and self.inline_cache and not self.stopped and not any(t in SKIP for t,_ in self.stack):
            self.svg_depth=1
            self.svg_parts=[self.get_starttag_text()]
            self.svg_names=['svg']
            return
        if tag==self.target and not active and not self.started:
            self.started=True
            self.depth=len(self.stack)+1
            active=True
        blocked=any(t in SKIP for t,_ in self.stack) or tag in SKIP
        if active and not self.stopped and not any(t in SKIP for t,_ in self.stack) and tag in {'iframe','video','audio','svg'}:
            self.warnings.append('外部媒体或矢量图未缓存')
            self.output.append('<p class="muted">[外部媒体或矢量图未缓存，请查看官方来源]</p>')
        emitted=None
        if active and not blocked and not self.stopped:
            if tag=='img':
                src=a.get('src') or a.get('data-src')
                alt=a.get('alt','文章插图')
                if src:
                    try:
                        local=image_cache_url(self.url,src,self.image_cache)
                        self.output.append('<img src="'+html.escape(local,quote=True)+'" alt="'+html.escape(alt,quote=True)+'" loading="lazy">')
                    except Exception:
                        self.warnings.append('插图未缓存：'+alt)
                        self.output.append('<p class="muted">[插图未缓存：'+html.escape(alt)+']</p>')
            elif tag in TAGS:
                emitted=tag
                attributes=''
                if a.get('id') and re.fullmatch(r'[\w-]{1,150}',a['id']):
                    attributes+=' id="original-'+html.escape(a['id'],quote=True)+'"'
                if tag=='a':
                    href=urljoin(self.url,a.get('href',''))
                    if urlparse(href).scheme in {'https','http'}:
                        attributes+=' href="'+html.escape(href,quote=True)+'" target="_blank" rel="noopener noreferrer"'
                    if a.get('href','').startswith('#') and re.fullmatch(r'#[\w-]{1,150}',a['href']):
                        attributes+=' data-original-anchor="original-'+html.escape(a['href'][1:],quote=True)+'"'
                self.output.append('<'+tag+attributes+'>')
        if tag not in VOID:
            self.stack.append((tag,emitted))

    def handle_startendtag(self,tag,attrs):
        if self.svg_depth:
            self.svg_parts.append(self.get_starttag_text())
            return
        self.handle_starttag(tag,attrs)
        if self.svg_depth:
            self.svg_parts[0]=self.svg_parts[0].replace('/>','>')
        self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if self.svg_depth:
            self.svg_parts.append('</'+(self.svg_names.pop() if self.svg_names else tag)+'>')
            if tag=='svg':
                self.svg_depth-=1
                if not self.svg_depth:
                    try:
                        local=self.inline_cache(''.join(self.svg_parts).encode())
                        self.output.append('<img src="'+html.escape(local,quote=True)+'" alt="原文矢量图" loading="lazy">')
                    except (ValueError,ET.ParseError):
                        self.warnings.append('矢量图未缓存：无法安全解析')
                        self.output.append('<p class="muted">[矢量图未缓存，请查看官方来源]</p>')
            return
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                for _,emitted in reversed(self.stack[i:]):
                    if emitted:
                        self.output.append('</'+emitted+'>')
                self.stack=self.stack[:i]
                if self.depth and len(self.stack)<self.depth:
                    self.depth=0
                break

    def handle_data(self,value):
        if self.svg_depth:
            self.svg_parts.append(html.escape(value))
            return
        if not self.depth or self.stopped or any(t in SKIP for t,_ in self.stack):
            return
        if value.strip() in {'Get the developer newsletter','Was this page helpful?','Related content'}:
            self.stopped=True
            return
        self.output.append(html.escape(value))


def image_cache_url(base,src,cache):
    url=urljoin(base,src)
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in IMAGE_HOSTS or p.username or p.port not in (None,443):
        raise ValueError('Unsupported image source')
    return cache(url)


def fetch_image(url, folder):
    # Image redirects must remain on an image host; no host access is widened.
    class Redirect(SafeRedirect):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            p=urlparse(newurl)
            if p.scheme!='https' or p.hostname not in IMAGE_HOSTS or p.username or p.port not in (None,443):
                raise ValueError('Image redirect not supported')
            from urllib.request import HTTPRedirectHandler
            return HTTPRedirectHandler.redirect_request(self,req,fp,301 if code==308 else code,msg,headers,newurl)
    req=Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'image/*'})
    with build_opener(Redirect()).open(req,timeout=20) as response:
        kind=response.headers.get('Content-Type','').split(';')[0]
        ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif','image/avif':'avif','image/svg+xml':'svg'}.get(kind)
        if not ext:
            raise ValueError('Unsupported image format')
        data=response.read(15_000_001)
        if len(data)>15_000_000:
            raise ValueError('Image too large')
    if ext=='svg':return cache_svg(data,folder)
    name=hashlib.sha256(data).hexdigest()+'.'+ext
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/name
    if not target.exists():
        temp=target.with_suffix('.tmp');temp.write_bytes(data);temp.replace(target)
    return '/original-assets/'+name
