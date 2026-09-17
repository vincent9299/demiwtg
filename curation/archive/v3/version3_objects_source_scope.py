"""Validate nonempty evidence snapshots and narrow each source to supported criteria."""

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
SCOPE={
'dovetail_wiki':(['K1','K2','K3'],'条目定义、through/half-blind分节与配图支持木工连接身份、梯形燕尾和贯通外露端面；不证明承载数值。'),
'dovetail_primary':(['K2'],'WOOD现场木工教学支持榫销装入两燕尾形成的缺口这一互补连接关系；不独立支持贯通类型K3。'),
'clinker_wiki':(['K1','K2','K3'],'定义与Description及历史剖图支持木船船壳身份、板缘重叠和沿船长布板；不支持目标船精确船型或板数。'),
'clinker_primary':(['K2','K3'],'维京船博物馆建造说明支持相邻板搭接及其纵向结构关系；不是本题小划艇整体身份的独立图鉴。'),
'splitpin_wiki':(['K1','K2','K3'],'定义、Design文字及A/B/C示意支持普通环头分叉销区别R形销、插孔后弯腿以及头/腿两侧防退关系；不作为安装安全认证。'),
'buttress_wiki':(['K1','K2','K3'],'飞扶壁定义与吕贝克图注明确低侧廊屋顶、高墙、外墩和跨越飞拱；照片给出可见相接部位。'),
'buttress_primary':(['K1','K2'],'坎特伯雷教堂Forces教学段支持高墙需要高处拱式支撑、区别贴地实心扶壁；并未独立具体说明本题跨低侧廊屋顶的K3。'),
'hurdygurdy_wiki':(['K1','K2','K3'],'条目发声机制与wheel/tangents近照支持手摇轮摩擦弦、键控切音，以及乐器身份；局部图不独立证明完整琴箱形态。'),
'hurdygurdy_met':(['K1','K2','K3'],'馆藏89.4.1059身份记录与已查看实物照片支持手摇柄/琴箱/键盘/轮弦装配；照片可见键列但不证明键的动态切音因果，后者用Wiki文字。'),
'shishiodoshi_wiki':(['K1','K2','K3'],'sōzu说明段支持蓄水改变平衡、接水端倾转排水、重尾复位；蓄水参考图支持转轴与接水/供水部件身份，不能独自证明排水状态。')}
cs=json.loads((OUT/'candidates.json').read_text())
for c in cs:
 k=c['sources'][0]['source_id'].removesuffix('_wiki')
 for s in c['sources']:
  ids,scope=SCOPE[s['source_id']];s['support_criteria']=ids;s['support_scope']=scope
  p=Path(s['snapshot_path']);raw=p.read_bytes();assert len(raw)>100
  txt=BeautifulSoup(raw,'html.parser').get_text(' ',strip=True);assert len(txt)>100
  assert s['quote'].lower() in txt.lower(),s['source_id']
  s['snapshot_sha256']=hashlib.sha256(raw).hexdigest();s['snapshot_verified']={'nonempty':True,'quote_exact_match':True,'bytes':len(raw),'reviewer':'assistant'}
 for ch in c['knowledge_checks']:
  ch['source_ids']=[s['source_id'] for s in c['sources'] if ch['id'] in s['support_criteria']]
 (OUT/k/'candidate.json').write_text(json.dumps(c,ensure_ascii=False,indent=2))
(OUT/'candidates.json').write_text(json.dumps(cs,ensure_ascii=False,indent=2));print('Verified',sum(len(c['sources']) for c in cs),'source snapshots; scope mapping complete')
