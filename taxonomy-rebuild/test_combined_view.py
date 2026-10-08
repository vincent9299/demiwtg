"""Meaningful preservation and fail-closed checks for fixed-batch exports."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from combined_view import BASE, build_combined, digest, load_and_validate


class CombinedViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ('combined-manifest.json', 'combined-decisions.json', 'concept-records.json'):
            shutil.copy2(BASE / name, self.base / name)
        shutil.copytree(BASE / 'inputs', self.base / 'inputs')

    def update_decisions(self, change):
        path = self.base / 'combined-decisions.json'
        rows = json.loads(path.read_text())
        change(rows)
        path.write_text(json.dumps(rows, ensure_ascii=False))
        manifest_path = self.base / 'combined-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['decision_sha256'] = digest(path)
        manifest_path.write_text(json.dumps(manifest))

    def test_source_change_fails_before_overwriting_outputs(self):
        sentinel = self.base / '08-最终版视觉分类树.md'
        sentinel.write_text('preserve previous output')
        with (self.base / 'inputs/batch2-original-table.txt').open('a') as stream:
            stream.write('\nchanged')
        with self.assertRaisesRegex(ValueError, '原始输入哈希变化'):
            build_combined(self.base)
        self.assertEqual(sentinel.read_text(), 'preserve previous output')
        self.assertFalse((self.base / 'combined-validation.json').exists())

    def test_unreviewed_decision_change_is_rejected(self):
        with (self.base / 'combined-decisions.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, '分类判断快照哈希变化'):
            load_and_validate(self.base)

    def test_renaming_is_rejected_even_with_updated_hash(self):
        self.update_decisions(lambda rows: rows[0].update(concept='an invented name'))
        with self.assertRaisesRegex(ValueError, '概念原文'):
            load_and_validate(self.base)

    def test_lost_overlap_source_is_rejected(self):
        def change(rows):
            next(r for r in rows if len(r['sources']) == 2)['sources'].pop()
        self.update_decisions(change)
        with self.assertRaisesRegex(ValueError, '来源、组别'):
            load_and_validate(self.base)

    def test_group_change_is_rejected(self):
        self.update_decisions(lambda rows: rows[0]['sources'][0].update(group=3))
        with self.assertRaisesRegex(ValueError, '来源、组别'):
            load_and_validate(self.base)

    def test_duplicate_id_is_rejected(self):
        self.update_decisions(lambda rows: rows[1].update(id=rows[0]['id']))
        with self.assertRaisesRegex(ValueError, 'ID 重复'):
            load_and_validate(self.base)

    def test_empty_path_is_rejected(self):
        self.update_decisions(lambda rows: rows[0].update(path=[]))
        with self.assertRaisesRegex(ValueError, '路径不是三层'):
            load_and_validate(self.base)

    def test_cannot_silently_promote_to_verified_species(self):
        self.update_decisions(lambda rows: rows[0]['name_audit'].update(status='已核验到种'))
        with self.assertRaisesRegex(ValueError, '未知名称粒度状态'):
            load_and_validate(self.base)

    def test_replay_is_deterministic_and_preserves_histories(self):
        csv = self.base / 'historical.csv'
        csv.write_text('history,keep\n')
        old_hash = digest(self.base / 'concept-records.json')
        stats = build_combined(self.base)
        files = [self.base / name for name in (
            '08-最终版视觉分类树.md', '09-合并与质量检查.md',
            '10-需确认与物种粒度.md', '08-最终版视觉分类树.json', 'combined-validation.json')]
        first = [digest(p) for p in files]
        build_combined(self.base)
        self.assertEqual(first, [digest(p) for p in files])
        self.assertEqual(stats['concepts'], 3315)
        self.assertEqual(stats['occurrences'], 3439)
        self.assertEqual(digest(self.base / 'concept-records.json'), old_hash)
        self.assertEqual(csv.read_text(), 'history,keep\n')
        self.assertEqual(len(list(self.base.glob('*.csv'))), 1)
        self.assertFalse(list(self.base.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
