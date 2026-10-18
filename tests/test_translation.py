import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from translation import translate_text


class TranslationTests(unittest.TestCase):
    def test_placeholder_corruption_retries_without_losing_identifiers(self):
        calls=[]
        def model(text):
            calls.append(text)
            return '占位符已被模型改写' if 'ZXQ' in text else '译文：'+text
        result,fallback=translate_text('Use Claude and MCP with Anthropic.',model)
        self.assertTrue(fallback)
        for term in ['Claude','MCP','Anthropic']:
            self.assertEqual(result.count(term),1)
        self.assertNotIn('ZXQ',result)
        self.assertGreater(len(calls),1)

    def test_intact_placeholders_use_whole_sentence(self):
        calls=[]
        def model(text):calls.append(text);return '译文：'+text
        result,fallback=translate_text('Claude uses an API.',model)
        self.assertFalse(fallback)
        self.assertIn('Claude',result)
        self.assertIn('API',result)
        self.assertEqual(len(calls),1)

    def test_actual_model_error_is_not_hidden(self):
        def fail(text):raise RuntimeError('model unavailable')
        with self.assertRaises(RuntimeError):translate_text('Claude works.',fail)

    def test_empty_fallback_does_not_silently_drop_surrounding_text(self):
        def model(text):return '模型改写了占位符' if 'ZXQ' in text else ''
        with self.assertRaises(ValueError):translate_text('Read with Claude.',model)

class TimeoutTests(unittest.TestCase):
    def test_translation_timeout_is_actionable(self):
        import tempfile,subprocess
        from unittest.mock import patch
        from translation import translate_html
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            python=root/'.translation-env/bin/python'
            python.parent.mkdir(parents=True)
            python.touch()
            with patch('translation.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',600)) as run:
                with self.assertRaisesRegex(ValueError,'10 分钟'):
                    translate_html('<p>Test</p>',root)
                self.assertEqual(run.call_args.kwargs['timeout'],600)

class StructureTests(unittest.TestCase):
    def test_missing_paragraph_and_changed_code_are_rejected(self):
        from translation import validate_structure
        with self.assertRaises(ValueError):validate_structure('<p>A</p><p>B</p>','<p>甲</p>')
        with self.assertRaises(ValueError):validate_structure('<pre><code>x=1</code></pre>','<pre><code>x=2</code></pre>')
        validate_structure('<p>A</p><code>x=1</code>','<p>甲</p><code>x=1</code>')

class NonLanguageTests(unittest.TestCase):
    def test_invisible_characters_and_punctuation_are_preserved(self):
        def forbidden(text):raise AssertionError('no model needed')
        for text in ['\u200b',' · ','123', '→']:
            self.assertEqual(translate_text(text,forbidden),(text,False))

class QualityTests(unittest.TestCase):
    def test_agent_case_and_plural_are_protected(self):
        result,_=translate_text('Agents use agent tools.',lambda text:text)
        self.assertEqual(result,'agent use agent tools.')

    def test_corruption_and_lost_terms_are_rejected(self):
        from translation import validate_quality
        for text in ['<p>文档。 。 。</p>','<p>代理人</p>','<p>agent\ufffd</p>']:
            with self.assertRaises(ValueError):validate_quality('<p>Agent</p>',text)
        validate_quality('<p>Agents work.</p><code>agent()</code>', '<p>agent工作。</p><code>agent()</code>')

    def test_invented_page_marker_is_rejected(self):
        from translation import validate_quality
        with self.assertRaises(ValueError):
            validate_quality('<p>Read the documentation.</p>', '<p>阅读文档。页:1</p>')
        validate_quality('<p>See page 1.</p>', '<p>参见页:1。</p>')
