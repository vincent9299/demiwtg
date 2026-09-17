"""Normalize independently reviewed third-batch material cards before freezing."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from version3_20 import RUN
from bagel_runner import sha


def main():
    cases=[]
    for group in ['nature','objects','relations']:
        p=RUN/f'candidates_{group}'/'candidates.json'
        obj=json.loads(p.read_text())
        rows=obj if isinstance(obj,list) else obj.get('cases',obj.get('candidates'))
        for original in rows:
            c=dict(original)
            loc=c.get('taxonomy_record',c.get('library_location',c.get('library_locator',{})))
            c['taxonomy_record']=dict(loc)
            c['taxonomy_record'].setdefault('taxonomy_paths',loc.get('actual_taxonomy_paths',c.get('taxonomy_paths',[])))
            c['taxonomy_record'].setdefault('suggested_semantic_path','demiwtg / '+c['domain']+' / '+c['concept']+'（本批语义建议；非权威树新路径）')
            c['candidate_record']={'path':str(p),'sha256':sha(p.read_bytes()),'original_question_id':c['question_id']}
            if 'application_chain' not in c:c['application_chain']=c.get('application_links',[])
            c['knowledge_family_id']=c.get('knowledge_family_id',c.get('family_id',c['concept']))
            c['diagnostic_selected']=bool(c.get('diagnostic_selected',bool(c.get('explicit_target'))))
            for im in c['reference_images']:
                if not isinstance(im.get('visual_review'),dict):
                    prior=im.get('visual_review')
                    assert prior or im.get('assistant_inspected') is True
                    im['visual_review']={'reviewed':True,'reviewer':'assistant material curator',
                        'reason':str(prior) if prior else im['support_scope']+' 区域：'+im['region']}
                im.setdefault('complementarity','图片支持：'+im['support_scope']+'；未覆盖部分由文字说明，限制：'+im['limitations'])
            dims=c.get('scene_dimensions',{})
            c['scene_dimensions_original']=dims
            c['scene_dimensions']={'scene_types':dims.get('scene_types',[dims.get('complexity_source','单主体／单系统，大目标区域')]),
                'combo_type':dims.get('combo_type',dims.get('combination_type',dims.get('composition_type','见应用关系'))),
                'premise_types':dims.get('premise_types',[dims.get('premise_type','概念、阶段、视角或规约，详见题面')]),
                'note':'描述性分类；不按对象数、标签数或跳数推定难度。'}
            c['preflight_review']={'accepted':True,'reviewer':'assistant /root with independent material curators',
                'scope':'Development eligibility: source support, identity and conditions, image support regions, large simple source scenes, separable criteria and full bilingual inputs checked before evaluation outputs.',
                'not_claimed':'No human-gold admission; no empirical difficulty or retrieval/finetuning benefit established.',
                'date':'2026-09-13'}
            cases.append(c)
    assert len(cases)==20
    cases.sort(key=lambda c:(c['task']!='edit',c['question_id']))
    (RUN/'prepared_cases.json').write_text(json.dumps({'cases':cases},ensure_ascii=False,indent=2)+'\n')
    print('Prepared',len(cases),'independent cases')


if __name__=='__main__':main()
