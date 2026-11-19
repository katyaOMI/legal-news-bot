import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from checks.model_eval import InputError, evaluate_response, main


class ModelEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.examples = [
            dict(id='a', title='Опрос и контакты', text='Ответы доступны без входа.\nКонтакты открыты.'),
            dict(id='b', title='Расчёт выборки', text='Подбор числа участников.'),
            dict(id='c', title='Условия сервиса', text='Приложение не предоставлено.'),
        ]
        self.gold = dict(items=[dict(id='a', expected='accept'),
                                dict(id='b', expected='reject'),
                                dict(id='c', expected='review')])
        self.rows = [dict(id='a', decision='accept', reason='Доступ к контактам.',
                          evidence=['Ответы доступны без входа.'], russia_basis=None),
                     dict(id='b', decision='reject', reason='Методология.', evidence=[], russia_basis=None),
                     dict(id='c', decision='review', reason='Нет приложения.', evidence=[], russia_basis=None)]

    def evaluate(self, rows=None, **kwargs):
        return evaluate_response(json.dumps(self.rows if rows is None else rows),
                                 self.examples, self.gold, **kwargs)

    def test_metrics_and_unknown_duration(self):
        result = self.evaluate()
        self.assertEqual((result['correct'], result['valid'], result['invalid']), (3, 3, 0))
        self.assertIsNone(result['duration_seconds'])
        self.assertEqual(result['confusion']['review']['review'], 1)
        self.assertEqual(result['semantic_review'], 'required_separately')
        self.assertTrue(result['comparison_eligible'])
        self.assertEqual(result['metrics_status'], 'complete')

    def test_fences_ndjson_and_duplicate_json_keys_are_not_repaired(self):
        raw_array = json.dumps(self.rows)
        bad = ['```json\n' + raw_array + '\n```',
               '\n'.join(json.dumps(row) for row in self.rows),
               '[{"id":"a","id":"b"}]', '[NaN]', '{']
        for raw in bad:
            with self.subTest(raw=raw):
                result = evaluate_response(raw, self.examples, self.gold)
                self.assertEqual(result['invalid'], 3)
                self.assertEqual(result['correct'], 0)
                self.assertFalse(result['json_valid'])

    def test_non_array_and_bad_coverage_invalidate_packet(self):
        for rows in [self.rows[0], self.rows[:-1], self.rows + [self.rows[0]],
                     self.rows + [dict(self.rows[0], id='unknown')], [None],
                     [dict(self.rows[0], id=['a'])]]:
            with self.subTest(rows=rows):
                result = self.evaluate(rows)
                self.assertEqual(result['invalid'], 3)
                self.assertTrue(result['packet_errors'])

    def test_field_types_empty_reason_and_extra_fields(self):
        changes = [dict(extra=True), dict(reason=' '), dict(reason=7),
                   dict(decision=['accept']), dict(decision='maybe'),
                   dict(evidence='Ответы доступны без входа.'), dict(evidence=[]),
                   dict(evidence=[123]), dict(evidence=['']), dict(russia_basis=8)]
        for change in changes:
            with self.subTest(change=change):
                rows = [dict(self.rows[0], **change), *self.rows[1:]]
                result = self.evaluate(rows)
                self.assertEqual(result['invalid'], 1)
                self.assertEqual(result['correct'], 2)
        rows = [dict(self.rows[0]), *self.rows[1:]]
        del rows[0]['russia_basis']
        result = self.evaluate(rows)
        self.assertEqual(result['invalid'], 1)
        self.assertIn('missing_russia_basis', result['items'][0]['errors'])

    def test_quotes_are_exact_substrings_and_only_crlf_is_normalized(self):
        self.rows[0]['evidence'] = ['без входа.\r\nКонтакты']
        self.assertEqual(self.evaluate()['invalid'], 0)
        for bad in ['Ответы доступны  без входа.', 'ответы доступны без входа.',
                    'Источник утверждает: Ответы доступны без входа.']:
            self.rows[0]['evidence'] = [bad]
            self.assertEqual(self.evaluate()['invalid'], 1)
        self.rows[0]['evidence'] = ['Ответы доступны без входа.']
        self.rows[0]['russia_basis'] = 'В России'
        self.assertEqual(self.evaluate()['invalid'], 1)

    def test_unsafe_accepts_and_excess_review_are_distinct(self):
        self.rows[1].update(decision='review')
        self.rows[2].update(decision='accept', evidence=['Приложение не предоставлено.'])
        result = self.evaluate()
        self.assertEqual((result['correct'], result['unsafe_accepts'], result['unnecessary_review']), (1, 1, 1))
        self.rows[2]['evidence'] = ['выдуманная цитата']
        result = self.evaluate()
        self.assertEqual(result['unsafe_accepts'], 1)
        self.assertEqual(result['confusion']['review']['invalid'], 1)

    def test_missing_accept_is_counted_when_quote_is_invalid(self):
        self.rows[0]['evidence'] = []
        self.assertEqual(self.evaluate()['missed_accepts'], 1)

    def test_duplicate_row_cannot_hide_an_unsafe_accept(self):
        duplicate = dict(self.rows[1], decision='accept', evidence=['Подбор числа участников.'])
        result = self.evaluate(self.rows + [duplicate])
        self.assertEqual(result['invalid'], 3)
        self.assertEqual(result['correct'], 0)
        self.assertEqual(result['unsafe_accepts'], 1)

    def test_invalid_packet_metrics_are_not_eligible_for_comparison(self):
        duplicate = dict(self.rows[1], decision='accept', evidence=['Подбор числа участников.'])
        for rows in [self.rows + [duplicate], self.rows + [dict(self.rows[0], id='unknown')]]:
            with self.subTest(rows=rows):
                result = self.evaluate(rows)
                self.assertFalse(result['comparison_eligible'])
                self.assertEqual(result['metrics_status'], 'incomplete_not_for_comparison')
                self.assertEqual(result['unsafe_accepts_basis'],
                                 'observed_known_ids_including_invalid_rows')
                self.assertEqual(result['missed_accepts'], 1)
        self.assertEqual(self.evaluate(self.rows + [duplicate])['unsafe_accepts'], 1)

    def test_invalid_review_is_not_counted_as_valid_excess_review(self):
        self.rows[1].update(decision='review', evidence=['выдуманная цитата'])
        result = self.evaluate()
        self.assertEqual(result['invalid'], 1)
        self.assertEqual(result['unnecessary_review'], 0)
        self.assertEqual(result['confusion']['reject']['invalid'], 1)
        self.assertFalse(result['comparison_eligible'])

    def test_subset_excludes_unpaired_examples(self):
        result = self.evaluate(self.rows[:1], selected_ids=['a'])
        self.assertEqual((result['total'], result['correct']), (1, 1))
        for selected in [[], ['a', 'a'], ['absent']]:
            with self.assertRaises(InputError):
                self.evaluate(selected_ids=selected)

    def test_invalid_dataset_and_gold_cannot_define_a_score(self):
        for examples, gold in [([], self.gold), (self.examples + [self.examples[0]], self.gold),
                               (self.examples, dict(items=[])),
                               (self.examples, dict(items=[dict(id='a', expected='other')])),
                               (self.examples, dict(items=self.gold['items'] + [self.gold['items'][0]]))]:
            with self.assertRaises(InputError):
                evaluate_response('[]', examples, gold)

    def test_duration_rejects_guesses_and_preserves_measured_zero(self):
        self.assertEqual(self.evaluate(duration_seconds=0)['duration_seconds'], 0)
        self.assertEqual(self.evaluate(duration_seconds=2.5)['duration_status'], 'measured')
        for duration in [True, -1, float('nan'), float('inf'), 'unknown']:
            with self.assertRaises(InputError):
                self.evaluate(duration_seconds=duration)

    def test_cli_reads_local_files_and_reports_invalid_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in [('inputs', self.examples), ('gold', self.gold), ('answer', self.rows)]:
                (root / name).write_text(json.dumps(data), encoding='utf-8')
            args = ['--inputs', str(root/'inputs'), '--gold', str(root/'gold'),
                    '--response', 'test', str(root/'answer'), '--output', str(root/'report')]
            self.assertEqual(main(args), 0)
            report = json.loads((root/'report').read_text(encoding='utf-8'))
            self.assertEqual(report['model_api_calls'], 0)
            self.assertIsNone(report['results']['test']['duration_seconds'])
            (root/'answer').write_text('```json\n[]\n```', encoding='utf-8')
            self.assertEqual(main(args), 1)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(args[:-2] + ['--response', 'test', str(root/'answer')])
            self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
