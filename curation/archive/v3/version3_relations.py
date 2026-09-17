"""Build eight source-grounded development candidates; never invoke a model.

Run from repo root with /opt/conda/bin/python -m curation.knowledge_application_v1.version3_relations.
PIL scenes are declared synthetic edit inputs, not factual evidence or target images.
Downloaded originals in the batch directory are immutable inputs to this builder.
"""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
from pathlib import Path
import json, hashlib, math, random
from PIL import Image, ImageDraw, ImageFont

ROOT = _ARCHIVE_ROOT
OUT = ROOT / 'state/curation/knowledge_application_v1/version3_20/candidates_relations'
SOURCES = OUT / 'sources'
REFS = OUT / 'references'
EDIT = OUT / 'edit_sources'
EDIT.mkdir(exist_ok=True)
DATE = '2026-09-13'

def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

URLS = {
 'lens':'https://openstax.org/books/college-physics-2e/pages/25-6-image-formation-by-lenses',
 'pipe':'https://openstax.org/books/university-physics-volume-1/pages/17-4-normal-modes-of-a-standing-sound-wave',
 'cell':'https://openstax.org/books/chemistry/pages/17-2-galvanic-cells',
 'polarization':'https://openstax.org/books/college-physics-2e/pages/27-8-polarization',
 'tide':'https://oceanservice.noaa.gov/facts/springtide.html',
 'eclipse':'https://science.nasa.gov/moon/eclipses/',
 'mobius':'https://www.open.edu/openlearn/mod/oucontent/view.php?id=9288&section=3.1',
 'contour':'https://pubs.usgs.gov/tm/2006/11A02/FGDCgeostdTM11A2_web_all.pdf',
 'euler':'https://openstax.org/books/contemporary-mathematics/pages/12-6-euler-trails',
}

# These two origins denied the local HTTP client; preserve clearly labelled
# browser-tool extracts, not invented downloaded HTML or publisher revisions.
(SOURCES/'tide_web_extract.txt').write_text('''Provider: web.run open, accessed 2026-09-13; original URL: https://oceanservice.noaa.gov/facts/springtide.html
Page: What are spring and neap tides? Author NOAA. Last updated 06/16/24.
Lines 8-11: During full or new moons—which occur when the Earth, sun, and moon are nearly in alignment—average tidal ranges are slightly larger. The moon appears new (dark) when it is directly between the Earth and the sun. The moon appears full when the Earth is between the moon and the sun. In both cases, the gravitational pull of the sun is added to the gravitational pull of the moon on Earth. High tides are a little higher and low tides are a little lower than average. Seven days after a spring tide, the sun and moon are at right angles to each other. The bulge of the ocean caused by the sun partially cancels out the bulge caused by the moon. Neap tides have high tides a little lower and low tides a little higher than average. Neap tides occur during the first and third quarter moon.
Local requests client returned HTTP 403; this is a browser-tool textual extraction, not an HTML byte snapshot.
''')
(SOURCES/'mobius_web_extract.txt').write_text('''Provider: web.run open, accessed 2026-09-13; original URL: https://www.open.edu/openlearn/mod/oucontent/view.php?id=9288&section=3.1
The Open University, Working mathematically, 3.1 The Möbius band; Activity 11 and its Comment, lines 168-186.
Give one end a half twist and then tape it together. This is a Möbius band.
After the first cut, you should have a band that was twice as long as the original and half the width. The second cut produces two connected bands of the same length but half the width.
Activity 15, Table 1, lines 243-247: zero half twists gives two strips; one half twist gives one strip, twice the length and half the width; two half twists gives two linked strips.
Local requests client returned HTTP 403; this is a browser-tool textual extraction, not an HTML byte snapshot. Page revision date not exposed.
''')

def src(k, title, quote, locator, version=None):
    ext = 'pdf' if k == 'contour' else 'html'
    p = SOURCES / f'{k}.{ext}'
    if not p.exists(): p = SOURCES / f'{k}_web_extract.txt'
    return dict(source_id=k, title=title, url=URLS[k], quote=quote,
        locator=locator, accessed_at=DATE,
        version=version or 'OpenStax archive 20260604.144757; captured page SHA256 is authoritative for this batch',
        snapshot_path=str(p), snapshot_sha256=sha(p),
        snapshot_kind='browser_tool_text_extract' if 'web_extract' in p.name else 'downloaded_publisher_bytes',
        license=('CC BY' if k == 'cell' else 'CC BY-NC-SA; see archived page footer') if k in ['lens','pipe','cell','polarization'] else 'Publisher terms apply; source provenance retained',
        reliability='primary institutional educational source; assistant verified selected passage')

def ref(k, scope, region, limitation, transfer):
    p=REFS/f'{k}.png'
    m=json.loads((REFS/'download_manifest.json').read_text())[k]
    return dict(path=str(p),sha256=sha(p),source_id=k,url=m['url'],role='retrieval_reference',
       support_scope=scope,region=region,limitations=limitation,complementarity=transfer,
       original_path=str(REFS/f'{k}.webp'), original_sha256=sha(REFS/f'{k}.webp'),
       figure_caption=m['figure_text'], original_alt=m['alt'],
       assistant_visual_review={'viewed':True,'date':DATE,'method':'view_image, source image pixels inspected'},
       provenance='publisher textbook illustration; downloaded original webp and lossless decoded PNG; not generative AI output')

font_paths=['/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/opt/conda/lib/python3.11/site-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf']
font_path=next(p for p in font_paths if Path(p).exists())
def font(n): return ImageFont.truetype(font_path,n)
def canvas():
    im=Image.new('RGB',(1024,768),'#fafaf6');return im,ImageDraw.Draw(im)
def text(d,xy,s,n=27,fill='#263445'):d.text(xy,s,font=font(n),fill=fill)
def save_scene(k,im,description):
    # Preserve every drawn coordinate relative to its scene; square by padding,
    # never resize or distort. The optical centre is unchanged in x.
    square=Image.new('RGB',(1024,1024),im.getpixel((0,0)))
    square.paste(im,(0,128))
    im=square
    p=EDIT/f'{k}.png';im.save(p)
    return dict(path=str(p),sha256=sha(p),role='edit_source',generation_attribute='declared_synthetic_code_native_diagram',
       source_url=None,source_code=str(Path(__file__).resolve()),source_code_sha256=sha(Path(__file__)),
       generator='Pillow deterministic drawing; no generative-model request',seed=314,
       description_zh=description,not_factual_evidence=True,contains_target_answer=False,
       canvas_transform=dict(original_size=[1024,768],final_size=[1024,1024],translation=[0,128],resized=False))

# Converging lens: original has an object at 1.5 f; no image arrow or answer rays.
im,d=canvas();text(d,(50,45),'THIN-LENS DIAGRAM',33)
d.line((60,360,974,360),fill='#63788b',width=3)
d.ellipse((424,140,476,580),fill='#c4e8ea',outline='#2a7f89',width=4)
for x,label in [(150,'2F'),(300,'F'),(600,'F'),(750,'2F')]:
 d.line((x,348,x,372),fill='#263445',width=3);text(d,(x-15,389),label)
d.line((225,360,225,250),fill='#2269b3',width=9);d.polygon([(225,229),(208,260),(242,260)],fill='#2269b3')
text(d,(144,170),'object',25);text(d,(413,620),'lens',25)
lens_scene=save_scene('lens',im,'简单薄凸透镜示意：物箭头位于镜左1.5f，无像箭头；焦点刻度、镜与主轴可见。')

# Closed end deliberately on the opposite side to the supporting source.
im,d=canvas();text(d,(48,44),'AIR DISPLACEMENT ALONG A TUBE',32)
d.line((175,260,850,260),fill='#455464',width=7);d.line((175,510,850,510),fill='#455464',width=7)
d.rectangle((841,260,865,510),fill='#455464')
for x in range(175,849,24):d.line((x,385,x+12,385),fill='#a1adb6',width=2)
text(d,(124,548),'open',26);text(d,(789,548),'closed',26)
text(d,(187,628),'Plot the requested displacement profile inside the tube.',22)
pipe_scene=save_scene('pipe',im,'左开右闭的一根大管；仅边界及零位移虚轴，不预画任何波形或节点。')

# Cartoon Moon: recognisable cratered full disc; synthetic, never moon evidence.
im=Image.new('RGB',(1024,768),'#08101d');d=ImageDraw.Draw(im)
d.ellipse((272,144,752,624),fill='#b8b8b5',outline='#d4d2c9',width=3)
rng=random.Random(314)
for _ in range(40):
 x,y=rng.randint(303,720),rng.randint(175,591);r=rng.randint(5,27)
 if (x-512)**2+(y-384)**2<(215-r)**2:
  d.ellipse((x-r,y-r,x+r,y+r),fill='#9b9d9c',outline='#c7c7c1',width=2)
for xy in [(108,167),(904,618),(163,603),(865,134)]: d.ellipse((xy[0],xy[1],xy[0]+4,xy[1]+4),fill='#ced3df')
moon_scene=save_scene('eclipse',im,'大幅灰白满月教学插图，固定环形坑纹理和四颗小星；不是天文照片，也不是事实资料。')

# Contours without hachures; target change is purely adding the implicit convention.
im,d=canvas();text(d,(55,42),'TOPOGRAPHIC MAP EXTRACT',32)
for box in [(165,193,859,616),(261,257,760,552),(360,321,657,489)]:
 d.ellipse(box,outline='#916337',width=5)
text(d,(82,675),'Closed dry depression; contours have not been annotated.',24)
contour_scene=save_scene('contour',im,'三条封闭棕色轮廓线尚未画示坡短线；这是待注记底图，不应理解为已正确编码的山丘。')

CONCEPTS=json.loads((ROOT/'datasets/demiwtg/meta/concepts.json').read_text())['concepts']
TREE=json.loads((ROOT/'datasets/demiwtg/meta/taxonomy.json').read_text())['tree']
def mounts(name):
    found=[]
    def walk(n):
      if name in n.get('instances',[]):found.append(n['path'])
      for c in n.get('children',[]):walk(c)
    walk(TREE);return found
def loc(name,proposed):
    match=next((c for c in CONCEPTS if c['name']==name),None)
    return dict(concept_asset_path=str(ROOT/'datasets/demiwtg/meta/concepts.json'),
      concept_record=match,concept_exact_match=bool(match),actual_taxonomy_paths=mounts(name),
      reviewed_taxonomy_path=proposed,reviewed_path_is_existing_tree_node=True,
      mount_status='existing concept; raw mounts retained' if match else 'concept absent; proposed semantic placement only; authoritative assets not modified')

def check(items,source):
 return [dict(id=f'K{i+1}',criterion=s,source_ids=[source],
  pass_rule='要求清楚可见且满足整条判据。',conflict_rule='可见形态或关系与该判据冲突。',
  unobservable_rule='必要部位缺失、遮挡或模糊，无法可靠核验；不计通过。') for i,s in enumerate(items)]
def execution(items):return [dict(id=f'E{i+1}',criterion=s) for i,s in enumerate(items)]
def base(q,name,task,domain,level,types,path):
 return dict(question_id=q,concept=name,domain=domain,task=task,application_level=level,
  knowledge_types=types,taxonomy_path=path,library_locator=loc(name,path),
  status='source_checked_candidate_not_frozen_or_model_tested',split='development',
  source_batch='version3_20/candidates_relations',reference_images=[],
  quality_checks=[dict(id='Q1',criterion='主要考点轮廓与颜色清楚、无严重伪影。'),dict(id='Q2',criterion='图形整体清晰，不因模糊、断裂或遮挡妨碍观察。')],
  review_identity='assistant; not human gold standard',
  scoring_note='K编号不是分数。逐条显示全文、pass/conflict/unobservable和理由；全K通过仅为知识通过；联合成功还要求全部E通过；画质另报。',
  scene_dimensions=dict(complexity_source='单主体或稀疏教材示意；知识条件单独组织',
    core_visual_region='至少画面中央一半；无多人背景和远处小部件',
    complexity_not_established_by_generation=True),
  dataset_isolation=dict(group_by=['concept_or_rule_family','source_work','reference_near_duplicates'],
    official_test_eligibility='development only; no result-based selection',
    training_record_required=['original_question','source_material','selection_application_relation','verified_target_image']))

cases=[]
c=base('v3r01','凸透镜','edit','知识与学科','compositional',['功能与机制','关系与组织'],'demiwtg / 知识与学科 / 物理学')
c.update(prompt='Complete this thin converging-lens teaching diagram. The blue object is 1.5 focal lengths to the left of the lens. Add only one red arrow representing its optical image, at the physically correct location, orientation and relative size. Keep the original object, lens, focal markers, axis and background. Do not add ray constructions or extra labels.',
 prompt_zh='补全这幅薄凸透镜教学图。蓝色物体位于透镜左侧1.5倍焦距处。只添加一支红色箭头表示它的光学像，像的位置、朝向与相对大小应符合成像规律。保留原物箭头、透镜、焦点标记、主轴和背景。不添加光路辅助线或额外文字。',
 knowledge_text='For an ideal thin converging lens in air, a parallel incident ray refracts through the far focal point. A ray through the optical centre continues approximately straight. The thin-lens relation is 1/f = 1/do + 1/di, with a positive image distance for a real image on the opposite side. Transverse magnification is m = -di/do: its sign specifies inversion and its magnitude gives the image-to-object height ratio. Objects outside the focal length can form real inverted images; objects inside it form upright virtual images.',
 knowledge_text_zh='对空气中的理想薄会聚透镜，平行主轴的入射光折射后通过另一侧焦点；经过光心的光线近似直行。薄透镜关系为1/f = 1/do + 1/di；像距为正表示实像在透镜另一侧。横向放大率m = -di/do，其正负表示像的正倒，绝对值表示像高与物高之比。焦距以外的物体可以形成倒立实像；焦距以内形成正立虚像。',
 sources=[src('lens','OpenStax College Physics 2e §25.6','Thin lenses have the same focal length on either side.','Figure 25.29; ray-tracing rules; thin-lens and magnification equations')],
 reference_images=[ref('lens','会聚与发散透镜的形态差异、平行光聚散和焦点侧别。','上半图(a)中央凸透镜与左侧汇聚点；下半图(b)仅用于对照。','本图没有有限物距物体、没有1.5f情境的像，不支持本题具体倍率。','图给出几何与焦点语义；文字给出有限物距成像方程，必须由新物距求像。')],
 edit_source=lens_scene,
 application_chain=dict(given='薄凸透镜，物距1.5f，物体竖直向上。',knowledge='薄透镜公式与带符号放大率。',visible_result='像在镜右3f，倒立，高度约物体2倍。',counterfactual='物距改为3f时像距1.5f、像高约一半。'),
 knowledge_checks=check(['结果仍是以原薄凸透镜为中心的光学成像图，红箭头表示同一物箭头的像，不是第二个镜或物体。','红像箭头位于透镜右侧，明显超过右侧2F刻度，位置约为3f。','红像箭头从主轴朝下，表现倒立实像。','红像箭头高度明显大于原物箭头，约为其2倍；宽松容差1.6–2.4倍。'],'lens'),
 execution_checks=execution(['只新增一支红像箭头，不添加额外物体、镜、辅助光线或说明文字。','原物箭头、透镜轮廓和焦点刻度的位置及颜色保持。','主轴、标题和背景保持；新增像可在空白区占据约220像素高度。']),
 exceptions=['示意图不要求像素级公式测量；焦距位置与倍率容差预先固定。','透镜有限厚度忽略；仅限近轴薄透镜模型。'],gaps=['图像仅支持部分原理，image-only条件不能被称为充分提供全部公式知识。'])
cases.append(c)

c=base('v3r02','驻波','edit','知识与学科','compositional',['功能与机制','关系与组织'],'demiwtg / 知识与学科 / 物理学')
c.update(prompt='In the single air tube shown, the left end is open and the right end is rigidly closed. Add a clear blue curve for the instantaneous longitudinal air-displacement profile at maximum amplitude in the first overtone. Use the dashed centreline as zero displacement. Keep the tube and its labels. This is a displacement graph, not a pressure graph; do not add text or other diagrams.',
 prompt_zh='图中是一根左端开口、右端刚性封闭的空气管。请在管内添加一条清楚的蓝色曲线，表示第一泛音在振幅最大时刻的纵向空气位移沿管长的分布，以虚线中心轴为零位移。保留管与原标记。这是位移图，不是压强图；不要加文字或其他图。',
 knowledge_text='In an ideal uniform air column, a rigidly closed end is a displacement node and an open end is a displacement antinode. Adjacent displacement nodes are separated by half a wavelength; a node and the next antinode are a quarter wavelength apart. An open-closed tube therefore supports odd harmonics: L = n lambda/4 for n = 1, 3, 5, and so on. The first overtone is the next allowed mode above the fundamental. A displacement profile at a maximum-amplitude instant alternates its signed lobes at successive nodes. Pressure nodes and antinodes are the opposite of displacement nodes and antinodes.',
 knowledge_text_zh='理想均匀空气柱中，刚性闭端是位移波节，开端是位移波腹。相邻位移波节间隔半个波长，波节到相邻波腹间隔四分之一波长。因此一端开一端闭的管只支持奇次谐波：L = nλ/4，其中n = 1、3、5……第一泛音是基频以上下一个允许的模式。在振幅最大时刻，位移分布在相邻波节之间交替出现正负波瓣。压强波节与位移波腹对应，压强波腹与位移波节对应。',
 sources=[src('pipe','OpenStax University Physics Volume 1 §17.4','A node exists at the closed end and an antinode at the open end.','Resonance in a Tube Closed at one End; Figures 17.19–17.22')],
 reference_images=[ref('pipe','开闭端位移边界，以及基频位移包络的可视形式。','左端No displacement箭头指向收束点；右端Maximum displacement指向张开端。','只画基频；不是第一泛音答案。源alt声称闭端最大、开端零，与图和正文相反，不输入该错误alt。','图形建立位移边界与管端的对应；文字给出奇次模态。目标闭端在右，且须从基频推广至第一泛音。')],
 edit_source=pipe_scene,
 application_chain=dict(given='均匀左开右闭管，第一泛音，位移而非压强。',knowledge='端点位移边界＋闭管允许奇次模态＋第一泛音的定义。',visible_result='左端位移极值，右端零；内部x≈L/3有一个零点，x≈2L/3有另一个反号极值。',counterfactual='若改两端开口，同一泛音次序允许偶次模式，边界和内节位置随之改变。'),
 knowledge_checks=check(['图中蓝曲线可识别为沿既有单根空气管的位移分布，而非压强密度图或另加一根横向振动绳。','左侧开端位移非零且接近波腹；右侧闭端回到零位移轴。','管内恰有一个位移零点，约在从开端起L/3处；不能画成基频无内节或更高泛音多内节。','零点两侧位移波瓣符号相反，内部另一波腹约在从开端起2L/3处；连续波形对应管长约3/4波长。'],'pipe'),
 execution_checks=execution(['在原管内添加一条清楚的蓝色分布曲线。','保留左开右闭的管形、虚线轴和原文字标签。','不新增公式、节点文字或多幅对照图。']),
 exceptions=['整体正负反相等价，两种相位均可；不要求特定向上波瓣。','忽略端部修正；位置以L为单位允许约±0.08L。'],gaps=['图像参考分辨率369×227，但图内文字和端点可读；它不提供高阶模态。','主库驻波被挂在海洋波浪路径，本题按物理学审核展示并保留原挂载。'])
cases.append(c)

c=base('v3r03','锌铜原电池','t2i','知识与学科','compositional',['功能与机制','过程与变化','关系与组织'],'demiwtg / 知识与学科 / 化学')
c.update(prompt='Draw a clean teaching diagram of a working Daniell cell: a copper strip in copper(II) sulfate is on the left, and a zinc strip in zinc sulfate is on the right. Connect them with an external wire and a potassium-nitrate salt bridge. Use one arrow on the wire labelled e- and small arrows for K+ and NO3- in the bridge to show the physically correct carrier motion during spontaneous discharge. Use large simple beakers and only the necessary chemical labels.',
 prompt_zh='画一幅清楚的工作中丹尼尔电池教学图：左边是浸在硫酸铜溶液中的铜片，右边是浸在硫酸锌溶液中的锌片，以外导线和硝酸钾盐桥连接。导线上用一支标为e-的箭头，盐桥用K+与NO3-的小箭头，表示自发放电时载流子的正确运动。烧杯应大而简单，仅保留必要化学标签。',
 knowledge_text='A galvanic cell produces electrical energy through spontaneous redox reactions. Oxidation occurs at the anode and reduction at the cathode; electrons travel through the external wire from anode to cathode. In the zinc–copper cell, Zn(s) is oxidized to Zn2+ and Cu2+ is reduced to Cu(s), making zinc the anode and copper the cathode. A salt bridge provides ionic conduction rather than an electron path. Its anions migrate toward the anode compartment and its cations toward the cathode compartment to maintain electrical neutrality. These roles depend on the redox pair, not on left–right position or on an electrode metal alone.',
 knowledge_text_zh='原电池通过自发氧化还原反应产生电能。阳极发生氧化、阴极发生还原，电子沿外导线由阳极流向阴极。锌铜电池中，Zn(s)被氧化成Zn2+，Cu2+被还原成Cu(s)，所以锌是阳极、铜是阴极。盐桥提供离子导电通路而非电子通路，其阴离子向阳极室迁移、阳离子向阴极室迁移以维持电中性。这些角色取决于氧化还原配对，不取决于左右位置，也不能只根据电极的金属名称确定。',
 sources=[src('cell','OpenStax Chemistry §17.2','Electrons flow from the anode to the cathode','Figure 17.4 summary; Check Your Learning zinc–copper half reactions')],
 reference_images=[ref('cell','两半电池的外导线/盐桥双通路、盐桥阴阳离子迁移及电极浸没关系。','上方黑外导线；中上U形盐桥及Na+/NO3-箭头；左右浸没电极。','参考是Cu/Ag配对，Cu为阳极，不能直接用于判定本题Zn/Cu角色；源电压0.46V也不适用。','必须迁移连接拓扑，同时用文字的新半反应改变Cu角色，并把方向转到目标左右布局。')],
 application_chain=dict(given='左铜右锌；两种对应硫酸盐溶液；KNO3盐桥；自发放电。',knowledge='锌氧化/铜离子还原＋外线电子方向＋盐桥维持电中性。',visible_result='外线电子由右锌到左铜；K+向左铜室，NO3-向右锌室。',counterfactual='换成资料的Cu/Ag体系时Cu由阴极变阳极，不能背金属固定角色。'),
 knowledge_checks=check(['身份与连接完整：左Cu/CuSO4半电池、右Zn/ZnSO4半电池；各金属浸入相应溶液，导线连接两金属，盐桥两端接触各自溶液。','外导线标e-的箭头从右侧锌极指向左侧铜极；不能把电子画成通过盐桥迁移。','盐桥内K+箭头通向左侧铜半电池。','盐桥内NO3-箭头通向右侧锌半电池。','若画阳极/阴极或正负极，必须锌为阳极负极、铜为阴极正极；若画反应箭头则锌溶解、铜析出，不能反向。'],'cell'),
 execution_checks=execution(['仅一套清楚的大尺寸双烧杯电池，保持题面规定左铜右锌。','必要Cu、Zn、两种溶液与盐桥载流子标签可读，电子箭头不混成电流方向。','无额外电源、第二套电池或无关实验装置。']),
 exceptions=['容许合理的导线、盐桥外形变化；无需测量电压。','不以溶液蓝色深浅推断浓度或反应程度；未画沉积层也不是错误。','极性或半反应属于可选可见内容，未添加不记缺项；添加后有明确冲突则知识项冲突。'],gaps=['概念名称未在权威概念表中精确命中，使用独立外部知识单元，不写回库。','图像单独对锌铜角色知识不充分，须将image-only解释为部分资料条件。'])
cases.append(c)

c=base('v3r04','潮汐','t2i','自然景观','conditional',['功能与机制','属性与状态'],'demiwtg / 自然景观 / 水文水体 / 海洋')
c.update(prompt='Create a minimal two-panel educational chart comparing the idealized astronomical tidal range at the same coast during a full Moon and during a first-quarter Moon. Label the left panel FULL MOON and the right panel FIRST QUARTER. In each panel show one vertical tide gauge with the same height scale and the same mean-water reference line, and mark expected HIGH and LOW water levels. Show the qualitative contrast, without numerical tide predictions, coast scenery or Moon drawings.',
 prompt_zh='画一幅极简双栏教学图，对比同一海岸在满月与上弦月时的理想天文潮差。左栏标FULL MOON，右栏标FIRST QUARTER。每栏画一根高度标尺相同、平均水位基准一致的竖直潮位尺，并标出预期HIGH和LOW水位。只表达定性差异，不给数值预测、不画海岸景物或月球图。',
 knowledge_text='The Sun and Moon both contribute to tides. Around new and full Moon they are nearly aligned with Earth, and the idealized astronomical tidal range is larger: high water is higher and low water lower. These are spring tides, unrelated to the season. Around first and third quarter, the solar and lunar directions make a right angle at Earth; their tidal effects partly oppose, so neap tides have a smaller range, with lower high water and higher low water. This is a qualitative astronomical comparison, not a tide forecast for a particular harbour; coast shape, weather and timing can modify observed tides.',
 knowledge_text_zh='太阳与月球共同影响潮汐。新月和满月附近，太阳、月球与地球近乎共线，理想天文潮差较大，高潮更高、低潮更低，称为大潮，与春季无关。上弦与下弦附近，日月方向在地球处构成直角，其潮汐效应部分抵消，小潮潮差较小，高潮较低、低潮较高。这是定性天文比较，不是某一港口的潮汐预报；海岸形状、天气和时间滞后均会改变实际观测。',
 sources=[src('tide','NOAA: What are spring and neap tides?','Neap tides occur during the first and third quarter moon','Paragraphs 3–5; web lines 8–11','Last updated 2024-06-16; browser textual snapshot 2026-09-13')],
 application_chain=dict(given='同海岸理想天文对照，满月与上弦，统一标尺与平均水位。',knowledge='月相所对应大潮/小潮及高潮低潮相对变化。',visible_result='满月HIGH更高、LOW更低，潮差大；上弦范围更窄。',counterfactual='交换月相标签时大、小潮范围应相应交换。'),
 knowledge_checks=check(['两栏能辨认为相同基准的潮位范围图，各自HIGH在LOW上方，区别于把潮差画成海浪振幅。','满月栏HIGH高于上弦栏HIGH。','满月栏LOW低于上弦栏LOW。','满月HIGH–LOW间距明显大于上弦间距，不能仅把整段水位上移而保持同样潮差。'],'tide'),
 execution_checks=execution(['满月在左、上弦在右，题面指定标签及HIGH/LOW可读。','两潮位尺高度、比例尺和平均水位参考线一致。','没有数值潮汐预报、风暴或具体港口场景。']),
 exceptions=['不要求固定潮差倍数或严格关于均值对称。','不用于实地航海；这里只固定理想天文因素。'],gaps=['只给文字，避免附同月相潮差图成为近重复答案。','主库潮汐存在多个原挂载，本题按海洋现象审核展示，原挂载完整保留。'])
cases.append(c)

c=base('v3r05','月食','edit','自然景观','conditional',['功能与机制','属性与状态'],'demiwtg / 知识与学科 / 天文学')
c.update(prompt='Update this close-up Moon illustration to show the middle of a total lunar eclipse as seen from Earth. The entire lunar disc is inside Earth\'s umbra; assume ordinary terrestrial atmospheric transmission and an exposure that still resolves the eclipsed lunar surface. Preserve the disc size, crater pattern and star background. Change only the illumination and appearance of the Moon to what this situation calls for. Do not add an Earth disc, labels or a diagram of the eclipse.',
 prompt_zh='把这幅月球近景插图更新为从地球看到的月全食中段：整个月面圆盘都位于地球本影中，假定通常的地球大气透射条件，曝光仍能分辨食甚月面。保持月盘尺寸、环形坑图案与星空背景，只改变该情境所要求的月球照明和外观。不要添加地球圆盘、标签或月食示意图。',
 knowledge_text='In a total lunar eclipse, the whole Moon is in Earth\'s umbra, so direct sunlight is blocked. Some sunlight filtered through Earth\'s atmosphere can still reach the lunar surface. Shorter blue and violet wavelengths scatter more readily; longer red and orange wavelengths survive the atmospheric path more effectively. Consequently the full eclipsed lunar disc can remain dimly visible with an orange, copper-red or reddish-brown appearance rather than retaining a bright uneclipsed crescent. The precise brightness and colour vary with atmospheric conditions and exposure; a total lunar eclipse is not an ordinary crescent lunar phase.',
 knowledge_text_zh='月全食时，整个月球进入地球本影，直射阳光被遮挡。部分经过地球大气过滤的阳光仍可到达月面；较短的蓝紫光更容易散射，较长的红橙光较能穿过大气路径。因此整块食甚月盘仍可暗淡可见，呈橙色、铜红色或红棕色，而不是保留一块未被食的明亮月牙。具体亮度与色调随大气条件及曝光而变；月全食也不是普通的月牙相位。',
 sources=[src('eclipse','NASA Science: Eclipses and the Moon','The Moon moves into the inner part of Earth’s shadow, or the umbra.','Total lunar eclipse; Blood Moon: How It Works','article modified 2026-07-29T12:44:33-04:00; captured 2026-09-13')],
 edit_source=moon_scene,
 application_chain=dict(given='整盘本影、普通地球大气透射、月面仍可辨认的曝光。',knowledge='本影遮直射光＋大气选择性散射使红橙光继续照亮月面。',visible_result='完整月盘暗淡偏暖红棕，保留表面纹理，无亮白未食月牙。',counterfactual='无大气天体本影不能直接套用地球红月后果；部分食可有亮的未食区域。'),
 knowledge_checks=check(['仍为可辨认的整块月球圆盘，有原月面/环形坑身份，不是太阳日冕或另一个红色球体。','月盘整体受食变暗，不能留明显未食的亮白月牙或半月区。','可见月面整体呈暖橙、铜红或红棕范围，不能保持原中性亮灰白或变成蓝绿。','暗部仍可分辨月面纹理和完整圆盘，不能全部抹成无可见月面的纯黑遮罩。'],'eclipse'),
 execution_checks=execution(['月盘中心位置与尺寸保持。','环形坑布局及四颗小星和背景保持。','只修改月面照明外观；不增加地球、标签、箭头或日冕。']),
 exceptions=['允许非均匀红棕阴影、不同暖色及曝光；不设精确色值。','极端大气下异常黑暗月食不用于本题，已用题面普通透射及可见月面约束。'],gaps=['与旧航天器本影题共享地球影区知识家族；必须在开发/训练/测试隔离时联组，不能称独立未见本影概念。'])
cases.append(c)

c=base('v3r06','线偏振与马吕斯定律','t2i','知识与学科','compositional',['功能与机制','关系与组织'],'demiwtg / 知识与学科 / 物理学')
c.update(prompt='Create one clean optical teaching diagram: unpolarized light travels left to right through three ideal linear polarizers, with transmission axes vertical, 45 degrees, and horizontal, in that order. Show the electric-field orientation immediately after each sheet using a clear double-headed arrow, and show whether light emerges beyond the last sheet. Use only the three sheets, incident light and those field indicators; label the sheet axes 0°, 45°, 90°. Do not give a numerical intensity formula.',
 prompt_zh='画一幅清楚的光学教学图：非偏振光从左向右依次通过三片理想线偏振片，透光轴依次为竖直、45度、水平。用清楚的双向箭头表示每片后电场方向，并表示最后一片之后是否仍有出射光。只画三片偏振片、入射光和这些电场指示，轴标为0°、45°、90°。不要给数值强度公式。',
 knowledge_text='An ideal linear polarizer transmits the electric-field component along its transmission axis, so its output is linearly polarized along that axis. Unpolarized light loses half its mean intensity at the first ideal polarizer. For already polarized input, Malus\'s law gives Iout = Iin cos²(theta), where theta is the angle between the incoming polarization and the next transmission axis. The incoming direction for each stage is the output direction of the preceding stage. Two crossed sheets alone block ideal polarized transmission; introducing an intermediate axis changes the successive projections, so nonzero light can pass even when the first and final axes are crossed.',
 knowledge_text_zh='理想线偏振片只透过沿透光轴的电场分量，因此出射光沿该轴线偏振。非偏振光经过第一片理想偏振片后平均强度减半。已经偏振的入射光遵循马吕斯定律Iout = Iin cos²θ，其中θ是入射偏振方向与下一片透光轴的夹角。每一级的入射方向，是前一级的出射方向。仅两片正交偏振片会阻断理想透射；插入中间方向的偏振片会改变逐级投影，即使首尾轴正交，也可能有非零出射光。',
 sources=[src('polarization','OpenStax College Physics 2e §27.8','When the second is perpendicular to the first, no light is passed.','Figure 27.41; Malus law equation 27.44')],
 reference_images=[ref('polarization','透光轴、电场双向箭头和两片夹角变化时的透射表现。','(a)两片平行、(b)第二片45度、(c)两片正交；箭头在片后沿透光轴方向。','图中没有目标三片级联；不能直接抄(c)当成最后无光。图片不提供精确级联强度。','须把图中双片关系应用两次，并更新中间出射偏振方向；文字给出逐级投影法。')],
 application_chain=dict(given='非偏振入射；三个轴0、45、90顺序。',knowledge='输出沿当前轴＋下一片使用更新方向的马吕斯投影。',visible_result='片后E方向竖直、斜45、水平，最后仍有较弱光。',counterfactual='抽去中间片后首尾正交，理想出射为零。'),
 knowledge_checks=check(['光学身份明确：三片是具有不同透光轴的线偏振片，不是棱镜、反射镜或仅颜色滤片。','第一片之后有竖直电场双向箭头。','中片之后电场双向箭头转为45度斜向。','最后一片之后电场双向箭头为水平，明确仍有非零出射光；不能画为完全阻断。','如果箭头大小或光束粗细用于表示强弱，后级不能比前级更强；不要求按精确比例作图。'],'polarization'),
 execution_checks=execution(['三片的轴按从左至右0°、45°、90°排列，标签可读。','仅一条串联光路，没有绕过中片的旁路。','不添加数值强度公式、第二套对照或无关场景。']),
 exceptions=['双向箭头的正负端无区别；同一透光轴相差180度等价。','可用相同大小的方向箭头，不强制箭头长度编码振幅。'],gaps=['知识图中双片规则足够支持方向机制，但完整三片结论需要组合，不是单图查表。','精确概念无库内条目，作为独立物理规则单元保存。'])
cases.append(c)

c=base('v3r07','等高线地形图','edit','文字与信息图形','direct',['规则与约定'],'demiwtg / 文字与信息图形 / 高程图')
c.update(prompt='Finish the relief notation on this unannotated map extract, which represents a closed dry depression. Use the USGS/FGDC topographic depression-contour convention, first brown-line option, to make the landform unambiguous. Preserve all three existing closed contour lines and the rest of the map. Add only the required cartographic marks, with no new words or elevation numbers.',
 prompt_zh='这幅未注记地图底图表示一处封闭干洼地。请按USGS/FGDC地形洼地等高线规约的第一种棕色线方案补全地貌注记，让地形含义无歧义。保留三条现有封闭等高线和地图其余内容，只添加所需地图符号，不加新文字或高程数字。',
 knowledge_text='The FGDC Digital Cartographic Standard for Geologic Map Symbolization (2006), section 30.1, uses hachures on depression contours to indicate closed areas of low elevation. Short ticks project toward the lower, interior side of each closed depression contour. In the first option the contour and hachure symbols are brown. These are short attached cartographic strokes rather than radial lines spanning the depression. Their purpose is to distinguish a closed low area from an unmarked closed-contour high area. The standard provides alternatives in black; those are a different option from the brown-line option specified here.',
 knowledge_text_zh='FGDC《地质图数字制图符号标准》（2006）第30.1节用洼地等高线上的示坡短线表示封闭低地。短线由每条封闭洼地等高线向较低的内部一侧伸出。第一种方案中，等高线与示坡短线都为棕色。这些是附着于等高线的短制图笔画，不是横贯洼地的长放射线；其作用是区分封闭低地与未加洼地标记的封闭高地。标准也给出黑色替代方案，但那不是本题指定的棕色方案。',
 sources=[src('contour','USGS TM 11-A2 / FGDC-STD-013-2006','Hachures are added to indicate closed areas of low values.','Printed A–30–1; PDF page 152, symbols 30.1.7–30.1.10','FGDC-STD-013-2006; 2006; PDF sha256 captured')],
 edit_source=contour_scene,
 application_chain=dict(given='封闭干洼地，棕色第一方案，尚未注记的三条封闭等高线。',knowledge='标准将向内的附着短线用作低地标志。',visible_result='三圈线上规律附着向内的棕色短线。',counterfactual='若地形为丘顶，不应加洼地示坡短线。'),
 knowledge_checks=check(['结果仍是三条封闭等高线构成的二维地形符号图，不是把轮廓画成立体山丘或坑洞照片。','三条洼地等高线上都有清楚可见的附着短线。','短线指向各自闭合区域的内部低地方向，不能朝外。','短线采用棕色，与第一方案一致，且为短笔画而非相连长辐条。'],'contour'),
 execution_checks=execution(['原三条封闭轮廓形状、位置、线色保持，不切断或删除。','标题、原说明、底色与画幅保持。','只增添标准所需短线，不增高程数字、湖水、箭头或其他文字。']),
 exceptions=['不以印刷0.5毫米或具体短线间距作为像素级硬门；方向、附着、短线身份是核心。'],gaps=['本题模型资料只给文字；已亲自核验PDF符号图，但它作为判分证据保留，不作为接近目标答案的检索图输入。'])
c['sources'][0]['evaluation_evidence_image']=str(SOURCES/'contour_A30.png')
cases.append(c)

c=base('v3r08','莫比乌斯带','t2i','知识与学科','relational',['过程与变化','关系与组织'],'demiwtg / 知识与学科 / 数学')
c.update(prompt='A long orange paper strip was given exactly one half-turn before its short ends were taped together. It has now been cut completely along its centreline once, all the way around, without cutting across its width or undoing the tape. Draw only the resulting paper object on a plain white tabletop. Arrange it loosely so that its continuity and any twists are easy to inspect; show no scissors, hands, uncut original, labels or diagrams.',
 prompt_zh='一条长橙色纸带恰好扭转半圈后，将两个短边用胶带接合；现在沿其中线完整剪了一圈，没有横断纸带，也没有拆开接头。请只画剪切后得到的纸带物体，放在白色桌面上，松散展开以便看清连续性和扭转。不画剪刀、手、未剪原物、标签或示意图。',
 knowledge_text='A paper strip joined after one half-turn forms a Möbius band. Cutting such a band once along its complete centreline leaves one connected closed band, not two separate loops. The resulting band has twice the original length and half the original width. This is different from an untwisted cylindrical band, whose centreline cut produces two separate bands. The cut product is a ribbon with two boundary edges, so its continuity must be judged by tracing the whole ribbon rather than counting apparent loops in a folded view. This source establishes the number of connected components and the relative dimensions; it does not prescribe a unique pose.',
 knowledge_text_zh='纸带扭转半圈后接合成为莫比乌斯带。沿它的完整中线剪切一次，结果仍是一条相连的闭合带，而不是两个分离环；长度是原来的两倍、宽度为原来的一半。这与没有扭转的圆柱带不同，后者沿中线剪开会分成两条带。剪切结果是具有两条边界的带子，应通过追踪整条带的连续性判断，不能数折叠视图中看似有几个圈。该来源确定连通分支数和相对尺寸，但不规定唯一摆放姿态。',
 sources=[src('mobius','The Open University, Working mathematically §3.1','one strip, twice the length of original, half the width','Activity 11 Comment; Activity 15 Table 1','undated course section; web text snapshot 2026-09-13')],
 application_chain=dict(given='一半扭转接合的带，完整沿中线剪一次，未拆接头。',knowledge='莫比乌斯带中线剪切的连通性后果。',visible_result='一条连续闭合较窄长带，不是两个独立环，连接处无自由断端。',counterfactual='原带不扭转会得到两个分離闭带。'),
 knowledge_checks=check(['物体可识别为剪切得到的扁平纸带，有宽度、边界和可见表面；不是绳圈、实心环或胶带卷。','沿图中纸带可以追踪为一个连续闭合分支，不能出现两个互不相连的独立纸环。','纸带没有自由断端、断裂缺口或新粘接成的分叉，符合只沿中线剪而未横断的过程。'],'mobius'),
 execution_checks=execution(['仅展示橙色结果纸带与白色桌面。','安排足够开放，关键交叠前后关系和连续性可观察。','没有手、工具、原始未剪带、文字标签或第二幅图。']),
 exceptions=['任意不破坏连通性的摆放均可；不能按视觉外圈数量直接判两个分支。','因无原物对照，不评分绝对长度翻倍和宽度减半。'],gaps=['当前来源未提供可直接审计的完整扭结/扭转数，因此K只核验纸带身份、闭合与连通性；不能把这3项通过称为完整拓扑身份成功。','建议仅作备用候选；若主批要求完整拓扑判据，应另补可靠扭转/连结证据和可观测视角后再冻结。','概念未在主库精确命中；不修改库资产。'])
c['status']='reserve_candidate_requires_topological_rubric_completion_before_freeze'
dump(OUT/'reserve_mobius_not_for_freeze.json',c)

c=base('v3r08','欧拉迹','t2i','知识与学科','relational',['规则与约定','关系与组织'],'demiwtg / 知识与学科 / 数学')
c.update(prompt='Draw one simple undirected graph on a white background. Vertices A, B and C form a triangle, and one extra vertex D lies to the right of C, joined only to C. The four edges are AB, BC, CA and CD. Fill in red exactly those vertices at which an open Euler trail using every edge once could start; leave every other vertex white with a black outline. Keep the vertex labels readable, use straight black edges and do not draw a route, arrows or an explanation.',
 prompt_zh='在白底上画一个简单无向图：顶点A、B、C组成三角形，另一个顶点D位于C右方且只与C相连。四条边为AB、BC、CA、CD。把恰好可以作为开放欧拉迹起点的顶点涂红，该迹要恰好经过每条边一次；其他顶点白色黑边。顶点标签清楚，边用黑色直线，不画行走路线、箭头或文字解释。',
 knowledge_text='A trail follows adjacent edges without using any edge more than once. An Euler trail uses every edge of the graph exactly once. An open Euler trail has different starting and ending vertices. For a connected undirected graph with exactly two vertices of odd degree, open Euler trails exist and their endpoints are exactly those two odd-degree vertices. Either endpoint can serve as the start by reversing a valid trail. All intermediate vertices have even degree because each arrival must be paired with a departure. The degree of a vertex counts its incident edges, not its position in a drawing.',
 knowledge_text_zh='迹沿相邻边行进，不重复使用任何边；欧拉迹恰好使用图中每条边一次。开放欧拉迹的起点与终点不同。连通无向图若恰有两个奇数度顶点，就存在开放欧拉迹，其两个端点恰好是这两个奇度顶点；将有效迹反向行走，两端中的任意一个都可作为起点。每个中间顶点的进入与离开成对，所以度数为偶数。顶点的度数统计关联边数，而不是图上位置。',
 sources=[src('euler','OpenStax Contemporary Mathematics §12.6','Begin at either of the two vertices of odd degree.','The Five Rooms Puzzle; Euler trail theorem; Fleury algorithm Step 1')],
 application_chain=dict(given='连通无向三角形ABC外接一条CD边，寻找所有开放欧拉迹的可用起点。',knowledge='顶点度数与开放欧拉迹奇度端点定理。',visible_result='C度3、D度1涂红；A与B度2留白。',counterfactual='若去掉CD，全部顶点偶度，只能形成闭合欧拉回路，不能沿用开放迹两奇点结论。'),
 knowledge_checks=check(['图的身份和关系完整：无向简单图，四个顶点A/B/C/D和且仅有AB、BC、CA、CD四条边；无箭头、重边、自环或缺边。','顶点C涂红，因为其三条关联边使它可以作为开放欧拉迹起点。','顶点D涂红，因为其一条关联边使它可以作为另一个开放欧拉迹起点。','顶点A保持白色，不能误标为开放欧拉迹起点。','顶点B保持白色，不能误标为开放欧拉迹起点。'],'euler'),
 execution_checks=execution(['三角形ABC与C右侧的D清楚分离，各标签对应正确节点。','节点足够大，红/白填色及黑边清楚。','仅一幅白底图，边为黑色直线；没有路线、箭头、额外节点或解释。']),
 exceptions=['A/B/C三角形的旋转或长宽比不影响知识；只保持D在C右侧并无意外交叉。','欧拉迹有多条，题目只标全部合法起点，不要求指定行走顺序。'],
 gaps=['来源图全部不输入：本题依赖可转用的一般定理，附带同形着色图会接近答案。','精确概念名不在主库；独立外部数学规则单元未写回权威表。'])
cases.append(c)

for c in cases:
    if c['question_id']=='v3r01':
      c['knowledge_checks'][1]['criterion']='红像箭头位于透镜右侧，明显超过右侧2F刻度，像距为2.7–3.3f（理想3f）；原镜心x=450、f=150像素，允许像箭头x=855–945，均在1024画布内。'
    elif c['question_id']=='v3r05':
      c['knowledge_checks'][0]['criterion']='仍为可辨认的整块月球圆盘，具有月面/环形坑身份，不是太阳日冕或另一个红色球体。'
      c['knowledge_checks']=c['knowledge_checks'][:3]
      c['execution_checks'].append(dict(id='E4',criterion='符合题面曝光条件：暗部仍可分辨月面纹理和完整圆盘，不能全部抹成无可见月面的纯黑遮罩。'))
    elif c['question_id']=='v3r06':
      c['prompt']+=' Use a clear 3D schematic in which the cross-sectional plane and axis of each sheet are distinguishable. Each field indicator lies in the plane of its sheet, perpendicular to the light path.'
      c['prompt_zh']+=' 采用清楚的三维示意，让每片偏振片的横截面平面与透光轴均可辨识。每个电场指示箭头位于对应片的平面内，与光传播路径垂直。'
      c['execution_checks'].append(dict(id='E4',criterion='三维图中各片横截面平面及透光轴清楚；每个电场指示箭头均位于对应片平面内，与光传播路径垂直，不能把电场箭头画成沿光路的传播箭头。'))
    c['diagnostic_selected']=c['question_id'] in ['v3r01','v3r02']
    if c['question_id']=='v3r01':
      c['explicit_target']='Explicit visual target: add a red image arrow on the right side of the lens, at about three focal lengths from its centre (beyond the right-hand 2F mark). Its base is on the optical axis and its tip points downward. Make its height about twice that of the original blue object arrow. Keep every original element fixed and add no rays or labels.'
      c['explicit_target_zh']='明确视觉目标：在透镜右侧距镜心约三倍焦距处（右侧2F标记以外）添加红色像箭头，底端在主轴上、尖端朝下，箭头高度约为原蓝色物箭头的两倍。所有原有元素保持不动，不加光线或标签。'
    elif c['question_id']=='v3r02':
      c['explicit_target']='Explicit visual target: draw a single smooth blue displacement curve starting at a nonzero extremum at the open left end, crossing the dashed zero axis once at roughly one third of the tube length from the left, reaching the opposite-signed extremum at roughly two thirds, and returning to zero at the rigid closed right end. Either overall sign is acceptable. Keep the original tube and labels and add no text.'
      c['explicit_target_zh']='明确视觉目标：画一条光滑蓝色位移曲线，在左侧开端为非零极值；距左端约三分之一管长处穿过零位移虚轴一次；约三分之二管长处到达反号极值；到右侧刚性闭端归零。整体正负反相均可。保留原管及标签，不添文字。'
    for s in c['sources']:
      s['support_scope']='支持本题知识陈述及逐项判据；具体原文位置：'+s['locator']
      if s['source_id']=='euler':s['license']='CC BY-NC-SA; see archived page footer'
    for r in c['reference_images']:
      r['visual_review']=dict(reviewed=True,reviewer='assistant',reason=r['support_scope']+' 限制：'+r['limitations'])
    c['reference_modality']='image+text' if c['reference_images'] else 'text'
    c['premise_types']=['scientific_idealization' if c['question_id']!='v3r07' else 'named_standard','explicit_state_or_boundary']
    c['combination_type']='independent_mechanism_and_condition_composition' if c['application_level']=='compositional' else 'single_rule_or_process_application'
    if 'edit_source' in c:
      c['edit_source_path']=c['edit_source']['path']
      c['edit_source']['visual_review']=dict(reviewed=True,reviewer='assistant',date=DATE,
        reason='原图已用view_image逐张检查：考点区域足够大，固定元素清楚，无目标答案；合成属性明确。')
    c['model_outputs']=[]
    c['assistant_review']=dict(source_text_verified=True,reference_images_viewed=bool(c['reference_images']),
      target_answer_generated=False,model_benefit_unknown=True,freeze_ready=True)
    dump(OUT/f"{c['question_id']}.json",c)
dump(OUT/'candidates.json',cases)
dump(OUT/'source_urls.json',URLS)
dump(OUT/'candidate_audit.json',dict(count=len(cases),edit=4,t2i=4,image_text=4,text_only=4,
   compositional=['v3r01','v3r02','v3r03','v3r06'],reserve=['reserve_mobius_not_for_freeze.json'],
   warning='Incomplete Möbius candidate retained separately; replaced before generation by Euler-trail rule application due to rubric completeness, never model benefit.',
   reference_errata=[dict(source='pipe',field='publisher alt',issue='alt reverses open/closed displacement; image pixels, caption and body agree that closed is node and open antinode',action='preserve original alt as provenance; never use erroneous alt as model knowledge')],
   source_scope='Only selected passages/figures verified; no claim of auditing entire textbooks.',
   generation='no BAGEL/Gemini calls; no GPU service changes'))
print(json.dumps({'output':str(OUT),'count':len(cases),'reserve':['reserve_mobius_not_for_freeze.json']},ensure_ascii=False))
