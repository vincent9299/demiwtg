"""Add preselected explicit visual-requirement conditions, preserving original prompts."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from pathlib import Path
ROOT=_ARCHIVE_ROOT;OUT=ROOT/'state/curation/knowledge_application_v1/version3_20/candidates_objects'
DIAGNOSTICS={
'dovetail':(
'The finished corner must use real interlocking through-dovetail geometry. Show flared trapezoidal tails that are wider at their outer ends than at their roots, fitted into complementary spaces in the other board. The interlocking pieces extend through the full board thickness, with their end grain visible on the outside faces at the corner. The pattern must be the actual connection between the boards, not paint or a surface decal. Preserve the original box shape, wood colors, tabletop and viewpoint. Tail count and exact angles are unrestricted.',
'完成的转角必须具有真实互相咬合的贯通燕尾榫几何。画出向外张开的梯形燕尾，其外端比根部宽，并嵌合进另一块板的互补缺口。交错部件贯穿整个板厚，端面木纹在转角外侧表面可见。该轮廓必须是两板的实际连接，而不是油漆或表面贴花。保持原木盒形状、木材颜色、桌面和视角。燕尾数量及精确角度不作限制。'),
'hurdygurdy':(
'Show one complete modern hurdy-gurdy with a plain rectangular wooden soundbox, a hand crank at one end, strings running lengthwise, and a side keyboard. With the mechanism covers open, make a circular friction wheel visible: its rim must actually touch the strings, rather than sit beside them as a disconnected disk. Beside the melody strings, show a row of key-operated tangents positioned to touch those strings, not a guitar-style fretted fingerboard. Keep all working parts assembled in the same instrument, on dark blue cloth, in the requested three-quarter overhead view. No player, separate bow, labels or exploded parts. String and key counts are unrestricted.',
'画出一件完整的现代手摇弦琴，具有素面长方形木琴箱、一端的手摇柄、纵向延伸的琴弦和侧面的键盘。机械盖打开后，应能看到圆形摩擦轮：其轮缘必须真实接触琴弦，不能是放在弦旁而互不相接的圆盘。旋律弦旁应有一排由琴键操控、能够接触这些弦的切音部件，而不是吉他式带品指板。所有工作部件保持装配在同一乐器上，乐器放在深蓝布上，采用原题要求的斜俯视。无演奏者、独立琴弓、标签或爆炸分解部件。弦数和琴键数不作限制。')}
cs=json.loads((OUT/'candidates.json').read_text())
for c in cs:
 k=c['sources'][0]['source_id'].removesuffix('_wiki')
 c['diagnostic_selected']=k in DIAGNOSTICS
 if k in DIAGNOSTICS:
  c['explicit_target'],c['explicit_target_zh']=DIAGNOSTICS[k]
  c['diagnostic_note']='Preselected before any model output; append explicit_target to unchanged original prompt. Behavioral drawing/execution diagnostic, not knowledge acquisition score.'
 c['score_schema']={'knowledge_items':{x['id']:{'status':'pass | conflict | unobservable','reason':'required visible evidence'} for x in c['knowledge_checks']},'execution_items':{x['id']:{'status':'pass | conflict | unobservable','reason':'required visible evidence'} for x in c['execution_checks']},'quality_items':{x:{'score':'integer 1..5','reason':'required visible evidence'} for x in ['clarity','artifacts','coherence']}}
 (OUT/k/'candidate.json').write_text(json.dumps(c,ensure_ascii=False,indent=2))
(OUT/'candidates.json').write_text(json.dumps(cs,ensure_ascii=False,indent=2))
print('Added two preselected diagnostics and independent scoring schema; no model calls')
