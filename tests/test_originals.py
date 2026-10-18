import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from library import Library,Page,SOURCE,now
from originals import ReaderHTML


def document(extra=''):
    return '<main><article><h1>Offline reader demonstration</h1><p>'+('This is our own fixture for verifying offline storage and history. '*12)+'</p>'+extra+'</article></main>'


class OriginalTests(unittest.TestCase):
    def setUp(self):
        translator=patch('translation.translate_html',side_effect=lambda content,root:dict(html='<p>简体中文测试</p>'+content,engine='test',language='zh-Hans'))
        translator.start()
        self.addCleanup(translator.stop)

    def test_sanitized_structure_preserves_code_and_removes_scripts(self):
        raw=document('<h2 id="part">Section</h2><pre><code>first\n  second</code></pre><script>BAD_SCRIPT()</script><p onclick="BAD_EVENT()">Safe</p><a href="javascript:BAD_LINK()">No script</a>')
        reader=ReaderHTML(raw,SOURCE+'/demo',lambda u:'/original-assets/a.png')
        text=''.join(reader.output)
        self.assertIn('first\n  second',text)
        self.assertIn('id="original-part"',text)
        self.assertNotIn('BAD_',text)
        self.assertNotIn('onclick',text)

    def test_images_local_only_and_failures_explicit(self):
        raw=document('<img src="https://www-cdn.anthropic.com/test.png" alt="Figure"><img src="http://127.0.0.1/private" alt="Forbidden">')
        urls=[]
        def cache(url):urls.append(url);return '/original-assets/local.png'
        reader=ReaderHTML(raw,SOURCE+'/demo',cache)
        self.assertEqual(len(urls),1)
        self.assertIn('src="/original-assets/local.png"',''.join(reader.output))
        self.assertEqual(len(reader.warnings),1)

    def test_only_first_article_and_no_footer(self):
        reader=ReaderHTML(document()+'<article><p>UNRELATED</p></article><footer>FOOTER</footer>',SOURCE,lambda u:'')
        self.assertNotIn('UNRELATED',''.join(reader.output))
        self.assertNotIn('FOOTER',''.join(reader.output))

    def test_cache_update_versions_and_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'content').mkdir()
            url=SOURCE+'/demo';raw=document();snap=Page(raw).snapshot(url,url)
            lesson=dict(id='demo',title='Demonstration',topic=0,url=url,summary='Summary',sections=[],questions=[],source=snap)
            (root/'content/seed.json').write_text(json.dumps(dict(created_at=now(),articles=[lesson])),encoding='utf-8')
            lib=Library(root=root)
            self.assertIsNone(lib.detail('demo')['original'])
            self.assertFalse(lib.manifest.get('cache_originals',False))
            lib.cache_original('demo',lambda u:(raw,u))
            self.assertIn('our own fixture',lib.detail('demo')['original']['html'])
            self.assertTrue(lib.manifest['cache_originals'])
            changed=document('<p>New source revision, written for this test.</p>')
            def updated(url):
                return ('<main><a href="'+SOURCE+'/demo">Demo</a></main>' if url==SOURCE else changed),url
            self.assertTrue(lib.check(updated))
            self.assertIn('New source revision',lib.detail('demo')['original']['html'])
            self.assertNotIn('New source revision',lib.detail('demo',1)['original']['html'])
            self.assertEqual(lib.history(),[])
            import zipfile
            with zipfile.ZipFile(root/'output/engineering-library-latest.zip') as z:
                self.assertEqual(len([n for n in z.namelist() if '/originals/' in n]),2)
                self.assertIsNone(z.testzip())

    def test_failed_original_extraction_keeps_previous_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'content').mkdir()
            url=SOURCE+'/demo';raw=document();snap=Page(raw).snapshot(url,url)
            lesson=dict(id='demo',title='Demo',topic=0,url=url,summary='',sections=[],questions=[],source=snap)
            (root/'content/seed.json').write_text(json.dumps(dict(created_at=now(),articles=[lesson])))
            lib=Library(root=root)
            with patch('originals.ReaderHTML') as reader:
                reader.return_value.output=['Too short']
                with self.assertRaises(ValueError):lib.cache_original('demo',lambda u:(raw,u))
            self.assertEqual(lib.detail('demo')['version'],1)
            self.assertIsNone(lib.detail('demo')['original'])


if __name__=='__main__':unittest.main()

class SVGTests(unittest.TestCase):
    def test_local_svg_removes_active_and_external_content(self):
        from originals import cache_svg
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            url=cache_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><path d="M0 0L10 10" onclick="bad()"/><use href="https://example.com/a.svg"/></svg>',root)
            value=(root/url.split('/')[-1]).read_text()
            self.assertIn('M0 0L10 10',value)
            for unsafe in ['script','onclick','example.com']:self.assertNotIn(unsafe,value)

    def test_inline_svg_preserves_mixed_case_elements(self):
        from originals import cache_svg
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            reader=ReaderHTML(document('<svg viewBox="0 0 10 10"><defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs><rect width="10" height="10" fill="url(#g)"/></svg>'),SOURCE,lambda u:'',lambda data:cache_svg(data,root))
            self.assertEqual(reader.warnings,[])
            self.assertIn('/original-assets/', ''.join(reader.output))

class DecorativeSVGTests(unittest.TestCase):
    def test_ignored_button_icons_are_not_missing_article_media(self):
        reader=ReaderHTML(document('<button><svg><path d="M0 0"/></svg></button>'),SOURCE,lambda u:'',lambda data: '/original-assets/icon.svg')
        self.assertEqual(reader.warnings,[])
        self.assertNotIn('icon.svg',''.join(reader.output))
