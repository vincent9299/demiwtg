"""Behavioral tests: fake evidence, immutable results, holdout isolation, approval gates."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
import copy
import tempfile
import unittest
import argparse
import contextlib
import hashlib
import io
import json
from unittest import mock
from pathlib import Path
from curation.core import (task_record, write, read, ingest, facts, review, core_records,
    validate_docs, validate_evidence, safe_file, digest, rules_hash)
from curation.pipeline import old_stratum, domain_map, validate_local_endpoint, prepare, run_model

class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.source = {'source_id':'s1','text':'A standard triangle has exactly three straight sides.', 'url':'https://example.org/test','title':'Triangle'}
        self.task = task_record('docs', {'concept':'三角形','split':'calibration','sources':[self.source],'domains':['知识与学科']}, 'test prompt')
        self.fact = {'statement':'标准三角形由三条直边围成。','conditions':[], 'related_concepts':['三角形'],
            'content_types':['特征与结构'],'knowledge_domains':['知识与学科'],
            'citations':[{'source_id':'s1','quote':self.source['text']}], 'visualizable':True,
            'visual_consequence':'三条直边闭合','core_reason':'边数错误会改变图形身份'}
        self.doc_result = {'source_decisions':[{'source_id':'s1','relation':'same_concept','reason':'标题与正文讨论目标概念'}],'facts':[self.fact],'gaps':[]}
        self.evidence = {'representation':'示意图', 'observations':[{'anchor':'中央','visible':'三条直边闭合'}],
            'evidence_status':'supports','condition_status':'not_applicable','coverage':'full','reason':'边数可数',
            't2i':{'status':'usable','target':'绘制标准三角形','checks':['三条直边闭合'],'reason':'结构可判'},
            'edit':{'status':'unusable','reason':'此例未构造知识编辑'}}
        write(self.run/'tasks/docs'/f"{self.task['task_id']}.json",self.task)

    def envelope(self,t,result):
        return {'task_id':t['task_id'],'input_sha256':t['input_sha256'],'model':'test-fixture','result':result}

    def setup_evidence(self):
        ingest(self.run,'docs',self.envelope(self.task,self.doc_result))
        f = next(facts(self.run))
        t = task_record('evidence',{'fact_id':f['fact_id'],'fact':f['fact'],'split':'calibration','image':{'sha256':'abc'}},'test')
        write(self.run/'tasks/evidence'/f"{t['task_id']}.json",t)
        ingest(self.run,'evidence',self.envelope(t,self.evidence))
        return f,t

    def test_quote_must_be_real_and_source_disambiguated(self):
        bad = copy.deepcopy(self.doc_result)
        bad['facts'][0]['citations'][0]['quote'] = 'A triangle has four sides, according to this source.'
        with self.assertRaisesRegex(ValueError,'substring'):validate_docs(self.task,bad)
        bad = copy.deepcopy(self.doc_result)
        bad['source_decisions'][0]['relation']='unrelated'
        with self.assertRaisesRegex(ValueError,'same_concept'):validate_docs(self.task,bad)

    def test_unknown_condition_cannot_be_usable(self):
        bad = copy.deepcopy(self.evidence); bad['condition_status']='unknown'
        with self.assertRaisesRegex(ValueError,'known conditions'):validate_evidence({},bad)

    def modern_evidence(self):
        r=copy.deepcopy(self.evidence)
        r['observations'][0]['id']='V1';r['unsupported_aspects']=[]
        r['edit'].update(source_state='clear',source_conditions='sufficient',source_anchors=['V1'])
        return {'input':{'evidence_protocol':3}},r

    def test_partial_coverage_cannot_qualify_full_t2i(self):
        t,r=self.modern_evidence();r['coverage']='partial';r['unsupported_aspects']=['连接结构不可见']
        with self.assertRaisesRegex(ValueError,'partial evidence'):validate_evidence(t,r)
        r['t2i']['status']='needs_more'
        validate_evidence(t,r)

    def test_different_variant_is_not_factual_conflict(self):
        t,r=self.modern_evidence();r['evidence_status']='conflicts';r['condition_status']='mismatched'
        r['t2i']['status']='unusable'
        with self.assertRaisesRegex(ValueError,'applicability'):validate_evidence(t,r)

    def test_edit_can_change_a_known_initial_structure(self):
        t,r=self.modern_evidence();r['evidence_status']='not_applicable';r['condition_status']='mismatched';r['coverage']='none'
        r['t2i']['status']='unusable'
        r['edit'].update(status='usable',initial_state='可见旧结构',instruction='按功能要求调整',expected_change='必要结构变化',knowledge_dependency='目标功能要求决定连接方式',preserve=['位置'])
        validate_evidence(t,r)
        r['edit']['source_anchors']=['V99']
        with self.assertRaisesRegex(ValueError,'anchors'):validate_evidence(t,r)

    def test_uncertain_edit_source_cannot_be_usable(self):
        t,r=self.modern_evidence()
        r['edit'].update(status='usable',source_conditions='uncertain',initial_state='状态',instruction='指令',expected_change='变化',knowledge_dependency='知识',preserve=['位置'])
        with self.assertRaisesRegex(ValueError,'source state'):validate_evidence(t,r)

    def test_stale_and_conflicting_results_rejected(self):
        envelope = self.envelope(self.task,self.doc_result)
        bad = dict(envelope,input_sha256='stale')
        with self.assertRaisesRegex(ValueError,'stale'):ingest(self.run,'docs',bad)
        ingest(self.run,'docs',envelope)
        ingest(self.run,'docs',envelope)  # Exact retries are idempotent.
        bad = copy.deepcopy(envelope); bad['result']['gaps']=['new gap']
        with self.assertRaisesRegex(ValueError,'immutable'):ingest(self.run,'docs',bad)

    def test_misnested_edit_repairs_structure_only(self):
        original = copy.deepcopy(self.evidence)
        self.evidence['t2i']['edit'] = self.evidence.pop('edit')
        f,t=self.setup_evidence()
        stored=read(self.run/'results/evidence'/f"{t['task_id']}.json")
        self.assertEqual(stored['result'],original)
        self.assertEqual(stored['transport_original_result'],self.evidence)

    def test_ambiguous_edit_branches_rejected(self):
        self.evidence['t2i']['edit']=copy.deepcopy(self.evidence['edit'])
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            self.setup_evidence()

    def test_both_human_approvals_required_for_export(self):
        f,t = self.setup_evidence()
        self.assertEqual(list(core_records(self.run)),[])
        review(self.run,'fact',f['fact_id'],'accept','tester','Verified source and conditions')
        self.assertEqual(list(core_records(self.run)),[])
        review(self.run,'evidence',t['task_id'],'accept','tester','Verified pixels and task',t2i='usable',edit='unusable')
        output=list(core_records(self.run))
        self.assertEqual(len(output),1); self.assertEqual(output[0]['task'],'t2i')
        review(self.run,'fact',f['fact_id'],'uncertain','tester','Need another source')
        self.assertEqual(list(core_records(self.run)),[])

    def test_human_cannot_accept_unknown_evidence(self):
        self.evidence['condition_status']='unknown'; self.evidence['t2i']['status']='needs_more'
        f,t=self.setup_evidence()
        with self.assertRaisesRegex(ValueError,'unknown'):review(self.run,'evidence',t['task_id'],'accept','tester','test',t2i='usable',edit='unusable')

    def test_human_can_correct_false_rejection_without_rewriting_model(self):
        corrected=copy.deepcopy(self.evidence)
        self.evidence['evidence_status']='indeterminate';self.evidence['t2i']['status']='needs_more'
        f,t=self.setup_evidence()
        review(self.run,'fact',f['fact_id'],'accept','tester','source checked')
        review(self.run,'evidence',t['task_id'],'accept','tester','visible structure checked',t2i='usable',edit='unusable',corrected_result=corrected)
        self.assertEqual(list(core_records(self.run))[0]['evidence'],corrected)
        stored=read(self.run/'results/evidence'/f"{t['task_id']}.json")
        self.assertEqual(stored['result']['evidence_status'],'indeterminate')
        corrected['observations']=[]
        with self.assertRaisesRegex(ValueError,'visible evidence'):
            review(self.run,'evidence',t['task_id'],'accept','tester','bad correction',t2i='usable',edit='unusable',corrected_result=corrected)

    def test_chinese_review_requires_input_and_preserves_task_decisions(self):
        from curation.review import ReviewQueue
        f, t = self.setup_evidence()
        q = ReviewQueue(self.run, reviewer='tester')
        q.current = ('fact', f['fact_id'])
        with mock.patch('builtins.print'):
            q.save_judgment('', 'not ready')
            self.assertEqual(len(list(core_records(self.run))), 0)
            self.assertIsNotNone(q.current)
            q.save_judgment('认同', 'source checked')
            q.current = ('evidence', t['task_id'])
            q.save_judgment('认同', 'image and both task judgments checked')
        output = list(core_records(self.run))
        self.assertEqual([r['task'] for r in output], ['t2i'])
        self.assertIsNone(q.current)

    def test_missing_reviewer_keeps_card_for_retry(self):
        from curation.core import reviewed
        from curation.review import ReviewQueue
        f, t = self.setup_evidence()
        q = ReviewQueue(self.run)
        q.current = ('fact', f['fact_id'])
        with mock.patch('builtins.print'):
            q.save_judgment('有问题', 'missing premise')
            self.assertEqual(q.current, ('fact', f['fact_id']))
            self.assertIsNone(reviewed(self.run, 'fact', f['fact_id'], f))
            q.save_judgment('有问题', 'missing premise', reviewer='researcher')
        self.assertIsNone(q.current)
        self.assertEqual(reviewed(self.run, 'fact', f['fact_id'], f)['decision'], 'reject')

    def test_notebook_correction_cannot_cross_records(self):
        from curation.review import ReviewQueue
        f,t=self.setup_evidence()
        q=ReviewQueue(self.run,reviewer='tester');q.current=('evidence',t['task_id'])
        correction=q.correction();correction['target_id']='wrong-record'
        with self.assertRaisesRegex(ValueError,'different record'):
            q.save('accept','checked',t2i='usable',edit='unusable',corrected_result=correction)

    def test_repair_mode_never_starts_untouched_tasks(self):
        args=argparse.Namespace(run=self.run,stage='docs',base_url='http://127.0.0.1:8000/v1',model='qwen3.8-27b',limit=1,split='calibration',compare_with=None,concept=None,repair_invalid=True,one_per_fact=False)
        with mock.patch('curation.pipeline.call_model') as call:
            run_model(args)
            call.assert_not_called()

    def test_holdout_external_ingest_also_requires_freeze(self):
        task=copy.deepcopy(self.task); task['input']['split']='holdout'
        task=task_record('docs',task['input'],'test prompt')
        write(self.run/'tasks/docs'/f"{task['task_id']}.json",task)
        with self.assertRaises(FileNotFoundError):ingest(self.run,'docs',self.envelope(task,self.doc_result))
        write(self.run/'manifest.json',{'test':True})
        write(self.run/'frozen_rules.json',{'rules_sha256':rules_hash(),'manifest_sha256':digest({'test':True})})
        ingest(self.run,'docs',self.envelope(task,self.doc_result))

    def test_unknown_scores_do_not_become_rejections(self):
        self.assertEqual(old_stratum({'quality':None,'identity':True}),'unannotated')
        self.assertEqual(old_stratum({'quality':9,'identity':None}),'unannotated')
        self.assertEqual(old_stratum({'quality':9,'identity':False}),'old_reject')

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(ValueError,'escapes'):safe_file(self.run,'../secret')

    def test_paid_gateways_and_external_endpoints_blocked(self):
        for url in ('http://127.0.0.1:4001/v1', 'https://example.org/v1',
                    'http://user@127.0.0.1:8001/v1', 'http://127.0.0.1:8001/v1?target=remote'):
            with self.assertRaises(ValueError):
                validate_local_endpoint(url, 'qwen3.8-27b')
        with self.assertRaises(ValueError):
            validate_local_endpoint('http://127.0.0.1:8001/v1', 'openrouter/qwen/qwen3.8-27b')
        validate_local_endpoint('http://127.0.0.1:8001/v1', 'qwen3.8-27b')

    def test_multiple_domain_mounts_preserved(self):
        m, domains=domain_map({'children':[{'name':'A','instances':['x'],'children':[{'instances':['x']}]},{'name':'B','instances':['x']}]})
        self.assertEqual(m['x'],['A','B'])

    def test_prepare_excludes_missing_bytes_and_preserves_unannotated(self):
        dataset = self.run / 'dataset'
        write(dataset/'meta/concepts.json', {'concepts':[{'name':n,'aliases':[]} for n in ['甲','乙','丙']]})
        write(dataset/'meta/taxonomy.json', {'tree':{'children':[{'name':'域','instances':['甲','乙','丙']}]}})
        pages = self.run/'pages.jsonl'
        pages.write_text('\n'.join(json.dumps({'page_sha':n,'text':n*150,'concepts':[n], 'url':'https://example.org/'+n}) for n in ['甲','乙','丙']))
        rows=[]
        for name in ['甲','乙','丙']:
            sha=hashlib.sha256(name.encode()).hexdigest()
            relative=f'blobs/{sha[:2]}/{sha}.jpg'
            if name != '丙':
                f=dataset/relative;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(name.encode())
            rows.append({'sha256':sha,'instances':[name],'path':relative,'quality':None,'identity':None})
        (dataset/'meta/images.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        args=argparse.Namespace(run=self.run/'pilot',dataset=dataset,clean_pages=pages,concepts=3,holdout=.5,images_per_stratum=3,pages=2,seed=10,source_chars=12000)
        with contextlib.redirect_stdout(io.StringIO()):prepare(args)
        manifest=read(args.run/'manifest.json')
        self.assertEqual({c['name'] for c in manifest['concepts']},{'甲','乙'})
        self.assertEqual({c['split'] for c in manifest['concepts']},{'calibration','holdout'})
        self.assertTrue(all(im['old_stratum']=='unannotated' for c in manifest['concepts'] for im in c['images']))
        args.run=self.run/'repeat'
        with contextlib.redirect_stdout(io.StringIO()):prepare(args)
        self.assertEqual(manifest,read(args.run/'manifest.json'))

if __name__=='__main__': unittest.main()
