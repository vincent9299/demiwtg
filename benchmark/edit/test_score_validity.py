"""Regression: model failures contribute zero; unusable questions are excluded in pairs."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from benchmark.edit.eval_codex_score import aggregate, compare, EDIT_DIMS


class ValidityPolicyTest(unittest.TestCase):
    def test_failure_is_zero_and_unusable_candidates_remove_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            qids = ['good', 'failure', 'invalid', 'unscorable']
            def write(path, rows):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            write(base / 'questions.jsonl', [dict(qid=q, edit_type='add') for q in qids])
            manifest = [dict(qid=q, candidate_id=q, edit_type='add', inputs={}) for q in qids]
            write(base / 'manifest.jsonl', manifest)
            rows = []
            for qid, status in zip(qids, ['ok', 'model_failure', 'invalid_question', 'judge_unscorable']):
                rows.append(dict(schema='edit-codex-v2-qib', qid=qid, candidate_id=qid,
                                 edit_type='add', validity={'status': status},
                                 raw_dimensions=[dict(key=f'd{i}', label=label, tier=2, mapped=100)
                                                 for i, label in enumerate(EDIT_DIMS['add'], 1)]))
            write(base / 'parts/part_001.jsonl', rows)
            with contextlib.redirect_stdout(io.StringIO()):
                aggregate(argparse.Namespace(manifest=base / 'manifest.jsonl', scores_dir=base / 'parts',
                                             questions=base / 'questions.jsonl', out_dir=base))
            scores = [json.loads(s) for s in (base / 'scores.jsonl').read_text().splitlines()]
            rep = json.loads((base / 'report.json').read_text())
            self.assertEqual((rep['n_valid'], rep['overall']), (2, 50))
            failure = next(s for s in scores if s['qid'] == 'failure')
            self.assertEqual(failure['official_total'], 0)
            self.assertEqual(failure['official_dimensions'], dict(d1=0, d2=0, d3=0))
            self.assertEqual(failure['raw_dimensions'][0]['tier'], 2)  # Preserve diagnosis.
            right = [dict(s, validity={'status': 'ok'}, official_total=60) for s in scores]
            # Exercise compare against an older raw official_total too: zero is policy, not caller convention.
            failure['official_total'] = 100
            write(base / 'left.jsonl', scores)
            write(base / 'right.jsonl', right)
            with contextlib.redirect_stdout(io.StringIO()):
                compare(argparse.Namespace(questions=base / 'questions.jsonl', left_scores=base / 'left.jsonl',
                                           right_scores=base / 'right.jsonl', left_name='a', right_name='b',
                                           out_dir=base / 'comparison'))
            paired = json.loads((base / 'comparison/report.json').read_text())
            self.assertEqual(paired['overall']['n'], 2)
            self.assertEqual(paired['overall']['a'], 50)
            self.assertEqual(paired['overall']['wins_ties_losses'], dict(win=1, tie=0, loss=1))
            self.assertEqual(paired['excluded_by_status']['invalid_question'], ['invalid'])
            self.assertEqual(paired['excluded_by_status']['judge_unscorable'], ['unscorable'])


    def test_legacy_v1_failure_keeps_score_and_stays_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            def write(path, rows):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            manifest = [dict(qid=q, candidate_id=q, edit_type='add', inputs={})
                        for q in ('good', 'failure')]
            write(base / 'questions.jsonl', manifest)
            write(base / 'manifest.jsonl', manifest)
            rows = [dict(**m, schema='edit-codex-v1',
                         validity={'status': 'ok' if m['qid'] == 'good' else 'model_failure'},
                         raw_dimensions=[dict(key=f'd{i}', label=label, score=score)
                                         for i, (label, score) in enumerate(zip(EDIT_DIMS['add'], [3, 5, 4]), 1)])
                    for m in manifest]
            write(base / 'parts/part_001.jsonl', rows)
            with contextlib.redirect_stdout(io.StringIO()):
                aggregate(argparse.Namespace(manifest=base / 'manifest.jsonl', scores_dir=base / 'parts',
                                             questions=base / 'questions.jsonl', out_dir=base))
            scores = [json.loads(s) for s in (base / 'scores.jsonl').read_text().splitlines()]
            report = json.loads((base / 'report.json').read_text())
            self.assertEqual((report['n_valid'], report['n_invalid'], report['overall']), (1, 1, 3))
            failure = next(s for s in scores if s['qid'] == 'failure')
            self.assertEqual(failure['official_total'], 3)
            self.assertEqual(failure['official_dimensions'], dict(d1=3, d2=3, d3=3))
            write(base / 'right.jsonl', [dict(s, validity={'status': 'ok'}) for s in scores])
            with contextlib.redirect_stdout(io.StringIO()):
                compare(argparse.Namespace(questions=base / 'questions.jsonl', left_scores=base / 'scores.jsonl',
                                           right_scores=base / 'right.jsonl', left_name='a', right_name='b',
                                           out_dir=base / 'comparison'))
            paired = json.loads((base / 'comparison/report.json').read_text())
            self.assertEqual(paired['overall']['n'], 1)
            self.assertEqual(paired['excluded'], [dict(qid='failure', left_status='model_failure', right_status='ok')])


if __name__ == '__main__':
    unittest.main()
