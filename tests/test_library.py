import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from library import Library, Page, discover, SOURCE, now, allowed


def page(text='A reliable engineering article. '*30, title='A useful article', nav='Home'):
    return '<html><nav>'+nav+'</nav><main><article><h1>'+title+'</h1><p>'+text+'</p></article></main><footer>Footer</footer></html>'


class LibraryTests(unittest.TestCase):
    def setUp(self):
        translator=patch("translation.translate_html",return_value=dict(html="<p>测试中文全文</p>",engine="test",language="zh-Hans"))
        translator.start()
        self.addCleanup(translator.stop)
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'content').mkdir()
        lesson=dict(id='test-article', title='测试导读', topic=0,url=SOURCE+'/test-article',
                    summary='测试摘要',sections=[dict(heading='方法',body='初始内容')],
                    questions=[dict(prompt='为什么？',points='检查证据')],
                    source=Page(page()).snapshot(SOURCE+'/test-article',SOURCE+'/test-article'))
        seed=dict(created_at=now(),articles=[lesson])
        (self.root/'content/seed.json').write_text(json.dumps(seed),encoding='utf-8')
        self.lib=Library(root=self.root)
        self.url=lesson['url']

    def tearDown(self):
        self.temp.cleanup()

    def fetcher(self, listing=True, body=None, error=None):
        def fetch(url):
            if url==SOURCE:
                other=SOURCE+'/another'
                return '<main><a href="'+(self.url if listing else other)+'">Article</a></main>',url
            if url!=self.url:
                return page(title='Another article'),url
            if error:
                raise error
            return body or page(),url
        return fetch

    def test_parser_ignores_navigation(self):
        self.assertEqual(Page(page(nav='Changed navigation')).snapshot(self.url,self.url)['digest'],
                         Page(page()).snapshot(self.url,self.url)['digest'])

    def test_parser_refuses_challenge_or_truncated(self):
        for raw in ['<main>Checking browser</main>','<main><h1>Not found</h1>short</main>']:
            with self.assertRaises(ValueError):Page(raw).snapshot(self.url,self.url)

    def test_unchanged_check_no_duplicate_versions(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        self.assertTrue(self.lib.check(self.fetcher()))
        self.assertEqual(self.lib.manifest['articles']['test-article']['latest'],1)

    def test_check_prepares_both_languages_without_article_click(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        detail=self.lib.detail('test-article')
        self.assertIn('reliable engineering',detail['original']['html'])
        self.assertEqual(detail['translation']['source_digest'],detail['source']['digest'])
        self.assertEqual(self.lib.offline_status()['ready'],1)
        with zipfile.ZipFile(self.root/'output/engineering-library-latest.zip') as z:
            self.assertTrue(json.loads(z.read('engineering-library/data/manifest.json'))['offline_ready'])
            self.assertTrue(any('/translations/' in n for n in z.namelist()))

    def test_failed_translation_retains_version_and_complete_package(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        package=(self.root/'output/engineering-library-latest.zip').read_bytes()
        with patch('translation.translate_html',side_effect=ValueError('model failure')):
            self.assertFalse(self.lib.check(self.fetcher(body=page(title='New revision'))))
        self.assertEqual(self.lib.detail('test-article')['version'],1)
        self.assertEqual((self.root/'output/engineering-library-latest.zip').read_bytes(),package)
        self.assertEqual(self.lib.progress['stage'],'partial')

    def test_package_requires_prepared_languages_when_requested(self):
        with self.assertRaises(ValueError):self.lib.build_package(require_complete=True)
        self.assertFalse((self.root/'output/engineering-library-latest.zip').exists())
        self.lib.check(self.fetcher())
        self.lib.build_package(require_complete=True)

    def test_quality_failure_keeps_previous_package(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        target=self.root/'output/engineering-library-latest.zip'
        previous=target.read_bytes()
        digest=self.lib.version('test-article')['source']['digest']
        path=self.lib.data/'translations/test-article'/(digest+'.json')
        value=json.loads(path.read_text())
        value['html']+='<p>。 。 。</p>'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.lib.build_package(require_complete=True)
        self.assertEqual(target.read_bytes(),previous)

    def test_unpacked_package_first_read_does_not_fetch(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        destination=self.root/'unpacked'
        with zipfile.ZipFile(self.root/'output/engineering-library-latest.zip') as z:
            z.extractall(destination)
        with patch('library.fetch',side_effect=TimeoutError('offline')) as network:
            installed=Library(root=destination/'engineering-library')
            detail=installed.detail('test-article')
            self.assertIsNotNone(detail['original'])
            self.assertIsNotNone(detail['translation'])
            network.assert_not_called()

    def test_changed_tail_creates_version_retains_old(self):
        old=self.lib.version('test-article')
        self.assertTrue(self.lib.check(self.fetcher(body=page(text='A reliable engineering article. '*30+'Changed tail.'))))
        self.assertEqual(self.lib.version('test-article',1),old)
        self.assertEqual(self.lib.detail('test-article')['version'],2)
        self.assertEqual(self.lib.version('test-article')['lesson']['questions'],[])
        self.assertEqual(len(self.lib.version('test-article',1)['lesson']['questions']),1)

    def test_new_article_no_generated_questions(self):
        self.lib.check(self.fetcher(listing=False))
        self.assertEqual(self.lib.version('another')['lesson']['questions'],[])
        self.assertEqual(self.lib.version('another')['editorial'],'pending')

    def test_removal_requires_two_checks_and_retains_versions(self):
        self.lib.check(self.fetcher(listing=False))
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'active')
        self.lib.check(self.fetcher(listing=False))
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'removed')
        self.assertTrue(self.lib.version_path('test-article',1).exists())
        self.lib.check(self.fetcher())
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'active')

    def test_delete_and_reappearance(self):
        e=HTTPError(self.url,404,'Gone',{},None)
        for _ in range(2):self.lib.check(self.fetcher(error=e))
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'deleted')
        self.assertTrue(self.lib.version_path('test-article',1).exists())
        self.lib.check(self.fetcher())
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'active')

    def test_transient_failures_do_not_delete_or_change_content(self):
        for e in [TimeoutError('offline'),HTTPError(self.url,403,'Denied',{},None)]:
            self.assertFalse(self.lib.check(self.fetcher(error=e)))
        self.assertEqual(self.lib.detail('test-article')['version'],1)
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'active')

    def test_invalid_index_does_not_remove(self):
        self.assertFalse(self.lib.check(lambda u:('<main>Empty</main>',u)))
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'active')

    def test_reflections_are_additive_and_survive_restart(self):
        for answer in ['第一次理解','后来改变了想法']:
            self.lib.add_reflection(dict(article='test-article',version=1,answers=[answer]))
        reopened=Library(root=self.root)
        self.assertEqual(len(reopened.history('test-article')),2)
        self.lib.check(self.fetcher(body=page(title='New title')))
        self.assertTrue(all(r['version']==1 for r in self.lib.history()))

    def test_drafts_and_reading_survive_restart(self):
        self.lib.draft(dict(article='test-article',version=1,answers=['未写完']))
        self.lib.mark_reading(dict(article='test-article',version=1,status='read'))
        self.lib.mark_reading(dict(article='test-article',version=1,status='reading'))
        reopened=Library(root=self.root)
        self.assertEqual(reopened.listing()['reading']['test-article']['status'],'read')
        self.assertEqual(reopened.export_learning()['drafts'][0]['answers'],['未写完'])

    def test_backup_import_idempotent_and_invalid_import_atomic(self):
        self.lib.add_reflection(dict(article='test-article',version=1,answers=['保存想法']))
        backup=self.lib.export_learning()
        self.lib.import_learning(backup)
        self.lib.import_learning(backup)
        self.assertEqual(len(self.lib.history()),1)
        bad=copy.deepcopy(backup)
        bad['reflections'].append(dict(id='bad',article='missing',version=99,answers=['x'],created_at=now()))
        with self.assertRaises(KeyError):self.lib.import_learning(bad)
        self.assertEqual(len(self.lib.history()),1)

    def test_package_integrity_consistency_and_privacy(self):
        self.lib.add_reflection(dict(article='test-article',version=1,answers=['PRIVATE-ANSWER']))
        target=self.lib.build_package()
        with zipfile.ZipFile(target) as z:
            self.assertIsNone(z.testzip())
            names=z.namelist()
            self.assertFalse(any('learning.sqlite' in n or 'backups' in n for n in names))
            m=json.loads(z.read('engineering-library/data/manifest.json'))
            self.assertEqual(m['articles'],self.lib.manifest['articles'])
            self.assertTrue(any('versions/test-article/v1.json' in n for n in names))
            self.assertNotIn(b'PRIVATE-ANSWER',b''.join(z.read(n) for n in names))

    def test_reading_not_gated_by_reflection(self):
        self.lib.mark_reading(dict(article='test-article',version=1,status='read'))
        self.assertEqual(self.lib.history(),[])
        self.assertEqual(self.lib.detail('test-article')['version'],1)

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):self.lib.version_path('../../outside',1)

    def test_failed_version_write_never_moves_latest(self):
        snap=Page(page(title='Changed')).snapshot(self.url,self.url)
        old=self.lib.detail('test-article')
        with patch('library.write_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.lib.apply_snapshot('test-article',snap)
        self.assertEqual(self.lib.detail('test-article'),old)

    def test_nonconsecutive_404_does_not_confirm_deletion(self):
        missing=HTTPError(self.url,404,'Missing',{},None)
        self.lib.check(self.fetcher(error=missing))
        self.lib.check(self.fetcher(error=TimeoutError('offline')))
        self.lib.check(self.fetcher(error=missing))
        self.assertEqual(self.lib.manifest['articles']['test-article']['state'],'active')

    def test_sources_restricted_to_official_https(self):
        for url in ['http://www.anthropic.com/engineering','https://127.0.0.1/a',
                    'https://www.anthropic.com.evil.example/a','https://user@www.anthropic.com/a']:
            self.assertFalse(allowed(url))
        self.assertTrue(allowed('https://code.claude.com/docs/en/best-practices'))

    def test_empty_reflection_does_not_create_history(self):
        with self.assertRaises(ValueError):self.lib.add_reflection(dict(article='test-article',version=1,answers=['']))
        self.assertEqual(self.lib.history(),[])

    def test_progress_tracks_real_work_and_package_completion(self):
        observations=[]
        base=self.fetcher()
        def observed(url):
            observations.append(dict(self.lib.progress))
            return base(url)
        self.assertTrue(self.lib.check(observed))
        self.assertEqual(observations[0]['stage'],'discovering')
        self.assertEqual(observations[1]['total'],1)
        self.assertEqual(observations[1]['completed'],0)
        self.assertEqual(self.lib.listing()['progress']['stage'],'done')
        self.assertEqual(self.lib.progress['completed'],1)
        self.assertEqual(self.lib.progress['percent'],100)
        self.assertFalse(self.lib.updating)

    def test_pause_waits_at_checkpoint_and_resume_continues_same_run(self):
        entered,release=threading.Event(),threading.Event()
        base=self.fetcher()
        calls=[]
        def fetch(url):
            calls.append(url)
            if url==SOURCE:
                entered.set()
                if not release.wait(5):raise TimeoutError('test release missing')
            return base(url)
        thread=threading.Thread(target=self.lib.check,args=(fetch,),daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.lib.control_updates(True)
            release.set()
            with self.lib.pause_condition:
                self.assertTrue(self.lib.pause_condition.wait_for(lambda:self.lib.progress['stage']=='paused',timeout=3))
            self.assertEqual(len(calls),1)
            self.assertEqual(self.lib.progress['completed'],0)
            self.assertTrue(self.lib.updating)
        finally:
            release.set()
            self.lib.control_updates(False)
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.lib.progress['stage'],'done')
        self.assertEqual(self.lib.progress['completed'],1)

    def test_pause_requires_active_update(self):
        with self.assertRaises(ValueError):self.lib.control_updates(True)

    def test_failed_check_progress_does_not_report_success(self):
        self.assertFalse(self.lib.check(lambda u: ('<main>Invalid directory</main>',u)))
        self.assertEqual(self.lib.progress['stage'],'error')
        self.assertLess(self.lib.progress['percent'],100)

    def test_partial_check_progress_is_explicit(self):
        self.assertFalse(self.lib.check(self.fetcher(error=TimeoutError('offline'))))
        self.assertEqual(self.lib.progress['stage'],'partial')
        self.assertIn('部分',self.lib.progress['message'])
        self.assertEqual(self.lib.progress['failed'],1)


if __name__=='__main__':unittest.main()

class AuditRegressionTests(unittest.TestCase):
    setUp = LibraryTests.setUp
    tearDown = LibraryTests.tearDown
    fetcher = LibraryTests.fetcher
    def test_resource_only_changes_create_distinct_digests(self):
        a=page().replace('</article>','<img src="https://www.anthropic.com/a.png"></article>')
        b=a.replace('/a.png','/b.png')
        self.assertNotEqual(Page(a).snapshot(self.url,self.url)['digest'],Page(b).snapshot(self.url,self.url)['digest'])

    def test_corrupt_cache_blocks_distribution(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        digest=self.lib.version('test-article')['source']['digest']
        (self.lib.data/'translations/test-article'/(digest+'.json')).write_text('{}')
        self.assertEqual(self.lib.offline_status()['ready'],0)
        with self.assertRaises(ValueError):self.lib.build_package(require_complete=True)

    def test_failed_image_can_be_retried(self):
        raw=page().replace('</article>','<img src="https://www.anthropic.com/a.png"></article>')
        with patch('originals.fetch_image',side_effect=OSError('unavailable')):
            self.assertFalse(self.lib.check(self.fetcher(body=raw)))
        def image(url,folder):
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'abc.png').write_bytes(b'image fixture')
            return '/original-assets/abc.png'
        with patch('originals.fetch_image',side_effect=image) as loader:
            self.assertTrue(self.lib.check(self.fetcher(body=raw)))
            loader.assert_called_once()
        self.assertEqual(self.lib.offline_status()['ready'],1)

    def test_result_survives_restart(self):
        self.assertFalse(self.lib.check(self.fetcher(error=TimeoutError('offline'))))
        restarted=Library(root=self.root)
        self.assertEqual(restarted.progress['stage'],'partial')
        self.assertFalse(restarted.offline_status()['articles'][0]['last_result']['ok'])

    def test_new_article_goes_to_unclassified(self):
        self.lib.check(self.fetcher(listing=False))
        self.assertEqual(self.lib.manifest['articles']['another']['topic'],8)

class MigrationTests(unittest.TestCase):
    setUp = LibraryTests.setUp
    tearDown = LibraryTests.tearDown
    def test_resource_hash_upgrade_retains_existing_questions(self):
        old=self.lib.version('test-article')
        snap=Page(page()).snapshot(self.url,self.url)
        old['source'].pop('text_digest',None)
        from library import write_json
        write_json(self.lib.version_path('test-article',1),old)
        snap['digest']='f'*64
        self.lib.apply_snapshot('test-article',snap)
        self.assertEqual(self.lib.version('test-article')['lesson']['questions'],old['lesson']['questions'])

class ReaderPackageTests(unittest.TestCase):
    setUp=LibraryTests.setUp
    tearDown=LibraryTests.tearDown
    fetcher=LibraryTests.fetcher
    def test_package_has_prepared_reader_mode_without_model(self):
        self.assertTrue(self.lib.check(self.fetcher()))
        with zipfile.ZipFile(self.root/'output/engineering-library-latest.zip') as z:
            manifest=json.loads(z.read('engineering-library/data/manifest.json'))
            self.assertTrue(manifest['reader_mode'])
            self.assertTrue(manifest['offline_ready'])
            self.assertFalse(any('/translation-models/' in n or 'learning.sqlite3' in n for n in z.namelist()))
            self.assertEqual(manifest['events'],[])
