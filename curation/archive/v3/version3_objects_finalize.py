"""Attach reviewed source scenes, first-party source support, and audit metadata."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json,hashlib
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=_ARCHIVE_ROOT;OUT=ROOT/'state/curation/knowledge_application_v1/version3_20/candidates_objects'
urls={'clinker':'https://www.vikingeskibsmuseet.dk/en/professions/boatyard/building-projects/gislingeboat-2015/the-gislinge-boats-hull','buttress':'https://learning.canterbury-cathedral.org/how-did-they-build-that/building-the-cathedral/','dovetail':'https://www.woodmagazine.com/woodworking-tips/techniques/joinery/dovetail-bits'}
quotes={'clinker':'The clinker-built boards, or the overlap between two boards, act as a longitudinal strengthening element in the hull.','buttress':'These are arches that lean or push against the walls','dovetail':'The pin is the part that fits into the socket, which is formed by two tails.'}
why={'dovetail':'已亲自查看：大转角两外侧木面清晰，当前是单一直线角缝，无燕尾轮廓。不从外观断言内部是butt而非mitre。','clinker':'已亲自查看：单船轮廓完整，外侧大面积连续光顺木板面和纵向板缝，未见叠搭凸缘，支架简洁。','splitpin':'已亲自查看：银色横轴、径向孔、上侧环头、下侧并拢的两条直腿均大而清楚；适宜只修改自由腿。'}
result=[]
for k in ['dovetail','clinker','splitpin','buttress','hurdygurdy','shishiodoshi']:
 d=OUT/k;c=json.loads((d/'candidate.json').read_text());wiki=c['sources'][0]
 if k in urls:
  p=d/'primary_source.html';c['sources'].append({'source_id':k+'_primary','url':urls[k],'quote':quotes[k],'snapshot_path':str(p),'snapshot_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_version':'web snapshot retrieved 2026-09-13; published revision not exposed','source_type':'first-party craft instruction / institution educational material'})
  if k in ['clinker','buttress']:
   wiki['quote']='the edges of longitudinal (lengthwise-running) hull planks overlap each other.' if k=='clinker' else 'extends from the upper portion of a wall to a pier of great mass'
  for ch in c['knowledge_checks']:ch['source_ids'].append(k+'_primary')
 if k in why:
  p=OUT.parent/'edit_sources'/f'{k}.png';c['edit_source']={'path':str(p),'role':'edit_source','source_kind':'generated','generator':'built-in imagegen (gpt-image C2PA metadata)','source_fact_evidence':False,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'generation_record_path':str(OUT.parent/'edit_sources/generation_prompts.json'),'visual_review':{'reviewed':True,'reviewer':'assistant','reason':why[k]}}
 if k=='clinker':c['domain']='交通工具'
 if k=='hurdygurdy':
  c['domain']='声音';c['prompt']=c['prompt'].replace('one French-type hurdy-gurdy','one modern French-type hurdy-gurdy with a plain rectangular wooden body');c['prompt_zh']=c['prompt_zh'].replace('一件放在深蓝布上的法式','一件具有素面长方形木琴箱、放在深蓝布上的现代法式')
  c['sources'].append({'source_id':'hurdygurdy_met','url':'https://www.metmuseum.org/art/collection/search/501646','quote':'Classification: Chordophone-Bowed keyboard','source_version':'object 89.4.1059; web tool retrieved 2026-09-13','snapshot_path':str(d/'met_web_tool_response.json'),'source_type':'museum collection record','local_http_note':'Local full-page fetch returned 429; excerpt preserved from successful web tool read.'})
  (d/'met_verified_excerpt.txt').write_text('Source: https://www.metmuseum.org/art/collection/search/501646\nVerified via web tool 2026-09-13. Object number 89.4.1059. French, second half 18th century. Classification: Chordophone-Bowed keyboard. Photograph is marked Public Domain. The dimensions list a friction wheel, keyboard span, and string vibration lengths.\n')
  p=d/'met_full.jpg';c['reference_images'].append({'path':str(p),'source_id':'hurdygurdy_met','source_url':'https://collectionapi.metmuseum.org/api/collection/v1/iiif/501646/1021852/main-image','role':'retrieval_reference','support_scope':'完整馆藏实物支持琴箱、手摇柄、摩擦轮、琴弦和键盘整体连接关系；与前一局部图互补。','region':'左端白手柄、中央竖直轮面与经过的弦、右侧长条键盘及琴体。','limitations':'馆藏是弧形历史琴箱，目标明确现代素面矩形琴箱，不复制轮廓或装饰；不固定弦数。','assistant_inspected':True,'generation_status':'museum collection photograph, public domain per museum record','sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
  c['gaps']=[x for x in c['gaps'] if not x.startswith('现有近景')];c['execution_checks'].append({'id':'E5','criterion':'使用现代素面长方形木琴箱，不照搬馆藏曲线琴箱与雕刻装饰。'});c['knowledge_checks'][0]['source_ids'].append('hurdygurdy_met')
 for r in c['reference_images']:
  r['sha256']=hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()
  if r['source_id'].endswith('_wiki'):
   from urllib.parse import unquote
   original=c['sources'][0]['image_url'].split('/')[-1]
   if k=='splitpin':original='CotterPins.svg'
   r['commons_file_page']='https://commons.wikimedia.org/wiki/File:'+unquote(original)
  r['support_criteria']=[ch['id'] for ch in c['knowledge_checks']]
 c['audit_status']='assistant source and reference image review complete; edit source reviewed where applicable; no evaluation model outputs generated by this subtask'
 (d/'candidate.json').write_text(json.dumps(c,ensure_ascii=False,indent=2));result.append(c)
(OUT/'candidates.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('Finalized',len(result),'cards')
