"""Protect diagnostic/primary boundaries and immutable evidence links."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import copy
import tempfile
import unittest
from pathlib import Path
from knowledge_diagnostics import OUT, STATE, read, immutable, validate_record, validate_batch

class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record=read(OUT/'expansion20_v1/imports.json')['records'][0]
        cls.case=next(c for c in read(STATE/'expansion20_v1/cases.json')['cases'] if c['question_id']==cls.record['question_id'])
    def test_no_official_score(self):
        r=copy.deepcopy(self.record);r['official_total']=100
        with self.assertRaises(AssertionError):validate_record(r,self.case,False)
    def test_legacy_cannot_be_called_fresh(self):
        r=copy.deepcopy(self.record);r['output_viewed']=True
        with self.assertRaises(AssertionError):validate_record(r,self.case,False)
    def test_missing_knowledge_item(self):
        r=copy.deepcopy(self.record);r['items'].pop()
        with self.assertRaises(AssertionError):validate_record(r,self.case,False)
    def test_wrong_source(self):
        r=copy.deepcopy(self.record);r['items'][0]['source_ids']=['invented']
        with self.assertRaises(AssertionError):validate_record(r,self.case,False)
    def test_cannot_pass_unrealized_condition(self):
        r=copy.deepcopy(self.record);r['items'][0].update(status='met',reasons=['prerequisite_not_realized'])
        with self.assertRaises(AssertionError):validate_record(r,self.case,False)
    def test_cannot_call_inapplicable_without_evidence(self):
        r=copy.deepcopy(self.record);r['items'][0]['status']='not_applicable'
        with self.assertRaises(AssertionError):validate_record(r,self.case,False)
    def test_output_hash_mismatch(self):
        r=copy.deepcopy(self.record);r['output_ref']['sha256']='0'*64
        with self.assertRaises(AssertionError):validate_record(r,self.case)
    def test_immutable_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.json';immutable(p,{'x':1});immutable(p,{'x':1})
            with self.assertRaises(ValueError):immutable(p,{'x':2})
            self.assertEqual(read(p),{'x':1})
    def test_frozen_inputs_and_primary_protocols(self):
        self.assertEqual(validate_batch('pilot')['outputs'],96)
        self.assertEqual(validate_batch('expansion20_v1')['fresh_visual_audits'],12)

if __name__=='__main__':unittest.main()
