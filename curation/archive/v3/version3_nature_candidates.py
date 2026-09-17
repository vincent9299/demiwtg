"""Build reviewed nature candidate cards; read-only taxonomy, no generation calls."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import hashlib,json
from pathlib import Path
ROOT=_ARCHIVE_ROOT
OUT=ROOT/'state/curation/knowledge_application_v1/version3_20/candidates_nature'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def build():
 urls=json.loads((OUT/'source_urls.json').read_text()); imageurls=json.loads((OUT/'image_urls.json').read_text())
 concepts=json.loads((ROOT/'datasets/demiwtg/meta/concepts.json').read_text())['concepts']
 tree=json.loads((ROOT/'datasets/demiwtg/meta/taxonomy.json').read_text())['tree']
 def locate(name):
  rows=[r for r in concepts if r['name']==name];paths=[]
  def walk(n):
   if name in n.get('instances',[]): paths.append(n['path'])
   for c in n.get('children',[]): walk(c)
  walk(tree)
  return {'concept_lookup_name':name,'concept_record':rows[0] if rows else None,'taxonomy_paths':paths,'status':'exact_name_found' if rows else 'exact_concept_missing','authority':'datasets/demiwtg/meta/taxonomy.json instances; concepts.json identity','taxonomy_sha256':sha(ROOT/'datasets/demiwtg/meta/taxonomy.json')}
 specs=[
 dict(key='pillow',question_id='v3_n01',concept='枕状熔岩',local='枕状熔岩',domain='自然景观',task='t2i',application_level='direct',knowledge_types=['特征与结构','功能与机制'],
 prompt='Create a close geological field photograph of a small exposed pillow-basalt outcrop on a dry hillside. The rock fills most of the image, with a plain gravel foreground and no people or labels.',
 prompt_zh='创作一张地质野外近景照片：干燥山坡上一小片出露的枕状玄武岩，岩石占据大部分画面，前景是简单碎石地面，无人物和文字。',
 knowledge_text='Pillow lava consists of packed, irregular sack-like or rounded elongated lobes. The lobes form when fluid lava is rapidly chilled by water; advancing lobes can stack against and over earlier ones. Ancient submarine pillow basalts may later be uplifted and exposed on dry land. The reference is an exposed pillow-basalt outcrop, not a photograph of an eruption. Its rounded lobe boundaries are structural evidence; present-day surface colour and vegetation are not universal properties of pillow lava.',
 knowledge_text_zh='枕状熔岩由紧密排列的不规则囊状或圆钝长形熔岩叶瓣组成。流动熔岩被水迅速冷却时可形成这些叶瓣；新推进的叶瓣可贴靠、堆叠在先前叶瓣上。古老海底枕状玄武岩后来可以被抬升，在干燥陆地出露。参考图是已出露的枕状玄武岩，并非喷发现场照片。圆钝叶瓣的边界提供结构证据；当前表面颜色与植被不是枕状熔岩的普遍属性。',
 quote='these stacked protrusions are sack- or pillow-shaped in cross section',version='USGS Eruptions of Hawaiian Volcanoes online edition; fetched 2026-09-13',
 checks=['岩体可辨为多个圆钝、囊状或短长形叶瓣聚集的枕状结构，而非柱状节理或尖锐碎渣堆。','叶瓣彼此接触或叠压并构成连续露头；不能只是画在平整石面上的圆斑。'],
 region='800×600照片的上半部及人物两侧：可辨大尺度圆钝枕体与分界。',support='支持枕体轮廓、接触排列和陆上出露；文字提供水下形成过程。',limits='风化露头不能证明冷却瞬间或玻璃质新鲜表皮；不要求参考图色泽。',exceptions=['枕体可拉长、不规则和局部断裂；不要求等大球体或固定数量。','不以今天是否在水中判定枕状岩石身份。'],gaps=['库中未找到枕状熔岩精确概念；保留外部补充概念，不改权威树。'],chain='已知枕状玄武岩与陆上露头 → 调用枕体结构及古海底岩可陆上出露 → 画出互相接触的圆钝叶瓣，不把干地条件误画成火山渣。'),
 dict(key='horseshoe',question_id='v3_n02',concept='大西洋鲎（Limulus polyphemus）腹面书鳃',local='马蹄蟹',domain='动物',task='t2i',application_level='relational',knowledge_types=['特征与结构','关系与组织'],
 prompt='Create a clean scientific illustration of one adult Atlantic horseshoe crab (Limulus polyphemus), viewed directly from underneath on a pale blue background. Show its whole body and tail and make the respiratory appendages clearly visible. No labels or inset diagrams.',
 prompt_zh='创作一幅清晰科学插画：一只成年大西洋鲎（Limulus polyphemus）的正腹面，浅蓝背景，完整显示身体与尾剑，让呼吸附肢清楚可见。不添加文字或局部小图。',
 knowledge_text='An Atlantic horseshoe crab has a broad horseshoe-shaped front shield, an abdomen, and a long telson. On its underside, the walking legs are associated with the front body region. The book gills are overlapping plate-like appendages on the underside of the abdomen, behind the walking legs and before the base of the telson. The NPS account states five sets of book gills. The photograph supports their layered arrangement and position but does not expose the microscopic gill pages. Attached shells on this photographed individual are incidental organisms, not horseshoe-crab anatomy.',
 knowledge_text_zh='大西洋鲎具有宽阔马蹄形前部甲壳、腹部和长尾剑。腹面步足位于身体前部；书鳃是腹部下方相互叠置的板状附肢，处在步足后方、尾剑基部之前。NPS资料记载有五组书鳃。照片支持其分层排列与所在位置，但没有揭示微观鳃页。该个体身上附着的贝壳是偶然附生生物，不属于鲎的解剖结构。',
 quote='The abdomen contains five sets of “book” gills',version='NPS updated 2019-12-11; fetched 2026-09-13',
 checks=['保持鲎的基本身份：宽马蹄形前甲、较小腹部和单根细长尾剑，而非真蟹或蜘蛛。','呼吸附肢为腹面成列叠置的宽板片；不能用羽毛、鱼鳃裂或裸露肺代替。','书鳃位于步足之后、尾剑基部之前的腹面，不能安放在背甲外面或腿末端。'],
 region='照片中部偏下：行走足上方、长尾剑下方的五层左右配对板片。照片尾端朝上。',support='支持腹面书鳃的宽片形态及相对步足、尾剑的位置。',limits='片层有重叠，故本题不把精确五组外轮廓计数列为通过条件；不证明微观鳃页及交换机制。',exceptions=['不要求附着贝类、污泥或沙粒。','因合理重叠不要求全部腿、鳃页可独立计数，但鳃区域必须能辨。'],gaps=['库内马蹄蟹别名准确含Limulus Polyphemus；鲎条目还混有其他物种别名，本题固定学名避免借用混合身份。'],chain='已知大西洋鲎腹面及呼吸附肢可见 → 调用书鳃形态与腹面身体分区关系 → 在步足与尾剑之间画叠置板片。'),
 dict(key='fern',question_id='v3_n03',concept='西部剑蕨（Polystichum munitum）幼叶展开',local='Polystichum Munitum',domain='植物',task='edit',application_level='conditional',knowledge_types=['过程与变化','特征与结构'],
 prompt='In the source image, add one new western sword fern (Polystichum munitum) frond emerging from the central crown, in the early stage before its tip has unfurled. Keep the pot, the existing mature fronds, the viewpoint and background. Make the new growth large and unobscured.',
 prompt_zh='在原图植株中央根冠处添上一枚西部剑蕨（Polystichum munitum）的新叶，处于叶尖尚未展开的早期阶段。保持花盆、原有成熟叶、视角和背景，让新生叶足够大且无遮挡。',
 source_scene_prompt='A simple close botanical photograph of a potted western sword fern. One terracotta pot, two mature fully expanded arching once-pinnate green fronds on the left and right. Clear exposed central crown with empty space above it. Neutral light grey background. No young shoots, no flowers, no text. The plant fills the image.',
 knowledge_text='Western sword fern fronds develop from tight fiddleheads. The main tip is curled inward during early growth and progressively unrolls into an expanded frond. The NPS photograph identifies the shown new growth as sword fern fiddleheads. Its coiled terminal growth and partly unfolding lateral divisions illustrate this stage. Mature western sword fern fronds are green, divided into pinnae and arch outward. Fiddleheads are developing leaves, not flower buds; their exact amount of unfolding varies with age.',
 knowledge_text_zh='西部剑蕨的叶由紧卷的拳卷状幼叶发育而来。早期主叶尖向内卷曲，随后逐渐舒展为展开的叶。NPS照片将图中的新生结构明确标为剑蕨拳卷状幼叶；卷曲顶端及部分展开的侧生分部展示了这一阶段。成熟的西部剑蕨叶绿色、有羽片，并向外弯拱。拳卷状幼叶是发育中的叶，不是花芽；展开程度随年龄而变化。',
 quote='the western sword fern fronds develop from small, tight fiddleheads',version='NPS updated 2026-07-15; fetched 2026-09-13',
 checks=['新增结构仍是从根冠长出的幼蕨叶，具有连续叶轴，而非花、蜗牛或另一个盆栽。','尚未展开的主叶尖呈向内卷曲的拳卷/螺卷，而非一根笔直封尖或完全展开成熟叶。'],
 region='照片中央细长新生叶轴及中上部卷曲顶端；两侧成熟羽片用于辅助识别幼叶而非动物。',support='提供卷曲的三维形态及局部舒展过渡，不只是同种远景。',limits='不证明固定展开天数；细小鳞毛不做判据。',exceptions=['旋向、卷曲角度、浅绿至褐绿颜色可变。','可以已有少量侧羽片展开；主尖必须仍处于未展开阶段。'],gaps=['合成原图待准备并检查成熟叶身份与中心新增区域。','库内Polystichum Munitum跨挂冬青蕨/盾蕨路径；保留原始路径，不据中文挂载重新定义物种。'],chain='早期且叶尖未展开 → 调用剑蕨拳卷状叶发生 → 原有成熟叶间出现主尖内卷的新幼叶。'),
 dict(key='lichen',question_id='v3_n04',concept='Ricasolia virens 的子囊盘',local='Ricasolia virens',domain='真菌与微生物',task='edit',application_level='direct',knowledge_types=['特征与结构'],
 prompt='The source shows a foliose Ricasolia virens lichen on bark without developed fruiting bodies. Edit it to show a fertile specimen with several mature apothecia, keeping the same thallus, bark, close viewpoint and background. Make the reproductive structures clearly visible.',
 prompt_zh='原图为树皮上尚无发育完成子实体的叶状地衣 Ricasolia virens。编辑为带有数个成熟子囊盘的可育个体，保持原地衣体、树皮、近景视角与背景，让生殖结构清楚可见。',
 source_scene_prompt='Close macro botanical photograph of a single small green foliose lichen patch on plain dark bark, broad irregular leafy lobes with gently textured green upper surfaces, no discs, no dots, no cups, no reproductive structures. Lichen fills most of frame. Soft even light, no text.',
 knowledge_text='In lichens, apothecia are fungal fruiting bodies whose spore-bearing surface is exposed as a disc or shallow saucer, usually surrounded by a rim. The British Lichen Society identifies the reference as Ricasolia virens, formerly Lobaria virens, with brown apothecia. In this photograph the brown exposed discs have greenish raised margins and arise directly from the green foliose thallus. These external structures must not be confused with tall stalked mushrooms, flowers or powdery vegetative propagules. Their number and exact outline vary.',
 knowledge_text_zh='在地衣中，子囊盘是孢子形成表面以圆盘或浅碟形式外露的真菌子实体，通常有边缘环绕。英国地衣学会将参考图鉴定为 Ricasolia virens（旧名 Lobaria virens），具有棕色子囊盘。照片中棕色外露盘面带有略隆起的绿色边缘，直接生于绿色叶状地衣体表面。这些外部结构不应与高柄蘑菇、花或粉状营养繁殖体混淆。其数量和确切轮廓存在变化。',
 quote='Ricasolia virens (formally Lobaria virens) with brown apothecia',version='British Lichen Society living page, revision ID unavailable; fetched 2026-09-13',
 checks=['保留为具有不规则叶状裂片的地衣体，新增部分不能将主体替换成苔藓或普通叶片。','新增可辨为数个直接着生于地衣体的浅碟/盘状子囊盘，具有暴露盘面而非闭口球形孢子囊或高柄蘑菇。','成熟盘面为棕至红棕色并有与绿色地衣体相连的隆起边缘；不能仅画无立体边界的平面棕斑。'],
 region='照片右半与下部：多枚棕色外露盘面和绿色隆起边缘。',support='支持此种子囊盘色泽、盘缘/盘面关系和叶状体上的着生形式。',limits='照片不证明微观子囊与孢子，不检验内部剖面或生殖是否实际发生。',exceptions=['盘面圆、椭圆或挤压不规则均合法；数量非固定。','绿色深浅与湿度光照有关，不能要求复制照片饱和度。'],gaps=['精确物种概念未在权威库找到；外部概念保留明确缺失。','合成编辑原图只作简单场景，不能反向充当本物种身份事实；待核验。'],chain='指定成熟子囊盘且固定Ricasolia virens → 调用该结构开放棕盘与绿色盘缘 → 在原叶状地衣体上增生有浅碟体积的棕盘。'),
 dict(key='shark',question_id='v3_n05',concept='杰克逊港鲨（Heterodontus portusjacksoni）卵鞘与岩隙固定',local='Heterodontus portusjacksoni',domain='动物',task='t2i',application_level='compositional',knowledge_types=['特征与结构','功能与机制','关系与组织'],
 prompt='Create a close underwater photograph of one recently deposited Port Jackson shark (Heterodontus portusjacksoni) egg case in its normal secure position at a simple rocky crevice. Keep most of the egg case visible. No adult shark, no other eggs, and no labels.',
 prompt_zh='创作一张水下近景照片：一枚杰克逊港鲨（Heterodontus portusjacksoni）新近产下的卵鞘，在简单岩隙中处于正常的稳固位置。让卵鞘大部分可见。不画成年鲨、其他卵或文字。',
 knowledge_text='The Australian Museum describes the Port Jackson shark egg case as a tough dark-brown spiral. Its specimen AMS I.1417 shows broad spiral flanges winding around an elongated central capsule. After laying, the female wedges the soft case into a rock crevice using her mouth; it then hardens there. The museum contrasts this with the crested horn shark, whose similar case has additional long twisted tendrils and is often attached to seaweed. The specimen photograph demonstrates the case shape, while the text supplies the rock-crevice placement relationship.',
 knowledge_text_zh='澳大利亚博物馆将杰克逊港鲨的卵鞘描述为坚韧的深棕色螺旋结构。馆藏标本 AMS I.1417 展示了围绕长形中央囊体旋绕的宽螺旋翼缘。产卵后，雌鲨用嘴把柔软卵鞘楔入岩石缝隙，随后卵鞘在那里硬化。馆方将其与冠状虎鲨作对照：后者相似卵鞘另有长而扭曲的卷须，常附着海藻。标本照片证明卵鞘外形，文字提供岩隙中的安置关系。',
 quote='She uses her mouth to wedge the egg case into a rock crevice',version='Australian Museum updated 2023-12-08; fetched 2026-09-13',
 checks=['主体为长形卵鞘，宽而连续的翼缘绕中央囊体成明显螺旋，不能是光滑鸡蛋、海螺硬壳或四角方形鳐卵。','卵鞘主体呈棕至深棕色、有机质外观；不能是金属螺丝。','卵鞘至少部分嵌入并接触岩隙两侧，具有楔固关系；不能悬浮、水面漂流或仅靠长卷须绑住海藻。'],
 region='馆藏照片整枚卵鞘：中央长囊与连续宽翼缘。',support='图像独立贡献为螺旋翼缘厚薄、宽度和囊体关系；文字补岩隙固定方式。',limits='单独标本照不能证明固定关系；固定由同页文字支持。',exceptions=['螺旋可因视角而部分重叠，不要求精确圈数或旋向。','允许少量磨损；固定处可遮挡卵鞘一端，但大部分外形应可见。'],gaps=['精确学名未在概念库找到；不能用泛鲨鱼概念冒充精确定位。','馆藏图保留©Australian Museum/Paul Ovendon归属；未确认可供公开重分发或模型训练的许可，当前仅本地研究参考。'],chain='固定物种且要求正常稳固位置 → 组合卵鞘宽螺旋翼缘形态 + 产卵后岩隙楔固行为 → 可见螺旋棕卵鞘部分嵌在岩隙中，而非泛鲨卵绑海草。'),
 dict(key='monarch',question_id='v3_n06',concept='帝王蝶（Danaus plexippus）蛹的早期外观',local='帝王蝶',domain='动物',task='edit',application_level='conditional',knowledge_types=['过程与变化','属性与状态','特征与结构'],
 prompt='Edit the single hanging monarch caterpillar in the source image into the same animal during its healthy early pupal stage, after the chrysalis has formed and firmed up and well before adult emergence. Keep its attachment location, the twig, camera view and background. Show the pupa clearly at macro scale.',
 prompt_zh='把原图中唯一悬挂的帝王蝶幼虫编辑为同一个体的健康早期蛹：蛹已形成并硬化，距离成蝶羽化尚早。保持附着位置、枝条、视角与背景，以微距尺度清晰展示蛹。',
 source_scene_prompt='Clean macro photograph of one mature monarch caterpillar hanging in a J shape from the underside of a simple horizontal twig, attached at its rear. The caterpillar has black white and yellow stripes, no chrysalis. Pale neutral blurred background, one twig, no leaves obscuring the animal, no text.',
 knowledge_text='An attached monarch caterpillar sheds its last larval skin and becomes a greenish chrysalis bearing metallic-looking gold spots. It remains suspended from its attachment point. The NPS photograph shows a compact green body, a gold-and-dark ridge near the upper region, and isolated gold spots lower down. This healthy early chrysalis is not the transparent late stage: near adult emergence the chrysalis becomes transparent and the butterfly is visible inside. The water droplets in the reference photograph are incidental, not pupal anatomy.',
 knowledge_text_zh='已附着的帝王蝶幼虫脱去最后一层幼虫皮后，成为带金属般金色斑点的绿色蛹，并继续悬挂于附着点。NPS照片展示了紧凑的绿色蛹体、上部的金色与暗色横向棱边，以及较下方分散的金色斑点。健康早期蛹不同于透明的晚期阶段：临近成蝶羽化时，蛹变透明，可以看见内部蝴蝶。参考照片上的水珠是偶然环境因素，不属于蛹的结构。',
 quote='a magnificent greenish chrysalis with gold spots',version='NPS updated 2021-11-23; fetched 2026-09-13',
 checks=['幼虫已变为紧凑悬挂蛹体，没有外露毛虫足、触角或条纹毛虫躯干，且未出现展开成蝶。','健康早期蛹体以不透明绿色为主，不是棕色丝茧或能直接看见橙黑蝶翅的透明晚期蛹。','可见与身体一体的上部金黑边及分散金色小斑，而不是金属项链或覆满金片的外壳。'],
 region='照片中央蛹体：上部金黑横边和下半部金点，顶端黑色附着结构。',support='直接提供阶段色泽、整体蛹形和金色标记空间分布。',limits='水珠会遮部分小金点，不要求金点逐个计数；照片不证明精确化蛹天数。',exceptions=['金点可因角度明暗不等；不要求参考图相同水珠。','绿色可偏浅或深；只要未透明显翅即可，不以固定天数判阶段。'],gaps=['合成毛虫原图待检查条纹身份、单个体与悬挂锚点。','本题同时有外形变化的执行负担，失败须与阶段知识冲突分别报告。'],chain='已形成并硬化、健康早期且未近羽化 → 从生命周期资料选绿色不透明蛹期 → 将毛虫改成紧凑绿蛹并表现特有金色标记。')]
 cards=[]
 for r in specs:
  k=r.pop('key');local=r.pop('local');quote=r.pop('quote');version=r.pop('version');checks=r.pop('checks');chain=r.pop('chain');region=r.pop('region');support=r.pop('support');limits=r.pop('limits')
  loc=locate(local);r['library_location']=loc;r['taxonomy_paths']=loc['taxonomy_paths'];r['taxonomy_path']=loc['taxonomy_paths'][0] if loc['taxonomy_paths'] else '库内精确概念缺失（外部补充；主域：'+r['domain']+'）'
  r['knowledge_family_id']='v3_nature_'+k;r['reference_modality']='image+text';r['split']='development';r['status']='assistant_evidence_reviewed_candidate_not_generated';r['reviewer']='assistant /root/evidence_candidates';r['retrieved_at']='2026-09-13'
  source_id='n_'+k;r['sources']=[{'source_id':source_id,'url':urls[k],'quote':quote,'version':version,'snapshot_path':str(OUT/(k+'.html')),'snapshot_sha256':sha(OUT/(k+'.html')),'source_type':'institutional_expert_article','support_scope':'知识输入及参考图身份；特定限制见图片条目'}]
  p=OUT/(k+'.jpg');iu=imageurls[k];sid=source_id
  if k=='pillow':
   p=OUT/'pillow_large.jpg';iu='https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/s3fs-public/thumbnails/image/oly3.jpg';sid='n_pillow_outcrop'
   snap=OUT/'pillow_large_source_web_extract.json'
   snap.write_text(json.dumps({'url':'https://www.usgs.gov/media/images/a-photo-basalt-pillows','retrieved_at':'2026-09-13','retrieval_method':'web.run open; excerpt transcribed from tool result turn9view0 lines 4-15','title':'A photo of basalt "pillows".','displayed_image_date':'June 1990 (approx.)','verbatim_excerpt':'The rocks in this photograph are pillow basalts exposed along the Hurricane Ridge Road near the tunnels area.','paraphrase_zh':'图注说明枕状构造由熔岩水下快速冷却形成，并讨论奥林匹克山脉海洋沉积与火山岩的抬升、变形。','original_link':iu,'note':'Direct HTTP snapshot was empty; this nonempty JSON preserves the web-tool verified caption excerpt, not a claimed full HTML snapshot.'},ensure_ascii=False,indent=2))
   r['sources'].append({'source_id':sid,'url':'https://www.usgs.gov/media/images/a-photo-basalt-pillows','quote':'pillow basalts exposed along the Hurricane Ridge Road','version':'image approx June 1990; page fetched 2026-09-13','snapshot_path':str(snap),'snapshot_sha256':sha(snap),'snapshot_note':'web-tool verified caption excerpt saved as JSON; direct HTTP returned empty body.','source_type':'USGS photograph caption','support_scope':'Supports identity and dry-land exposure of the pictured pillow-basalt outcrop; see main USGS text for formation and morphology.'})
  r['reference_images']=[{'path':str(p),'sha256':sha(p),'source_id':sid,'url':iu,'role':'retrieval_reference','support_scope':support,'region':region,'limitations':limits,'visual_review':{'reviewed':True,'reviewer':'assistant','reason':support+' '+limits,'observations':region,'reviewed_at':'2026-09-13'},'supports_checks':['K'+str(i+1) for i in range(len(checks))] if k!='shark' else ['K1','K2'],'cannot_support_checks':['K3'] if k=='shark' else [],'image_generation_status':'institution-credited photograph, no declared AI generation; identity via caption; photographic provenance is not an independent forensic authenticity certification'}]
  r['knowledge_checks']=[{'id':'K'+str(i+1),'criterion':c,'source_ids':[s['source_id'] for s in r['sources']],'scoring':'pass=清晰满足；conflict=可见相反；unobservable=遮挡、太小或模糊不能可靠判断'} for i,c in enumerate(checks)]
  if k=='shark':
   r['knowledge_checks'].append({'id':'K4','criterion':'保持杰克逊港鲨卵鞘身份：不带冠状虎鲨卵鞘特有的长扭曲卷须；即便同时楔固在岩隙中，也不能混入该他种结构。','source_ids':['n_shark'],'support_type':'text_primary_species_contrast','scoring':'pass=可见卵鞘末端与周边未出现他种长扭曲卷须；conflict=清楚出现该结构；unobservable=末端和周边严重遮挡或出框，无法判断','evidence_note':'同页文字明确对照Crested Horn Shark卵鞘另有long twisted tendrils；单张标本照片未见卷须不能独立证明物种一般排除规则。'})
   r['reference_images'][0]['cannot_support_checks'].append('K4')
   r['reference_images'][0]['limitations']+=' 单张标本未见卷须不能独立证明K4的物种一般排除规则；K4由馆方文字物种对照主支持。'
  e=['主体数量遵循题面，没有额外复制主要目标。','遵循题面要求的观察视角。','知识部位足够大且无遮挡，可以可靠观察。','没有添加文字、标签或局部拼图。']
  if r['task']=='edit':
   e+=['完成题面指定的核心新增或状态改变。','保留原图背景和整体构图。',{'fern':'保留原图花盆与根冠位置。','lichen':'保留原图树皮。','monarch':'保留原图枝条。'}[k],{'fern':'保留两枚成熟叶的形状与位置。','lichen':'保留原地衣体的裂片轮廓和整体结构。','monarch':'保留原悬挂附着点的位置。'}[k],'未把原场景整体替换为参考照片。']
  else: e+=['背景与题面情境一致。','没有添加题面禁止的无关人物、成年动物或其他主体。']
  r['execution_checks']=[{'id':'E'+str(i+1),'criterion':c,'scoring':'pass / conflict / unobservable，逐项附画面理由'} for i,c in enumerate(e)]
  r['quality_checks']=[{'id':'Q1','criterion':'独立记录自然度/解剖或结构连贯性/伪影；不以画质替代知识评分。'}]
  r['application_chain']=chain;r['scene_dimensions']={'subject_count':'one principal target','knowledge_region':'large close-up','complexity_source':'one structural or state application; no added background obligations','composition_type':'形态与固定关系组合' if k=='shark' else '单知识主体','premise_type':'explicit lifecycle stage' if k in ['fern','monarch'] else 'named concept and visible viewpoint'}
  r['leakage_review']='参考为源资料原图，目标为不同背景/视角的新实例或编辑场景；不以本题目标图或近重复目标作资料。所有概念家族保留为开发数据。'
  if r['task']=='edit':
   r['prompt']+=' No labels or inset diagrams.';r['prompt_zh']+='不添加文字或局部小图。'
   ep=OUT.parent/'edit_sources'/f'{k}.png';gp=OUT.parent/'edit_sources/generation_prompts_nature.json'
   obs={'fern':'已亲自看图：单陶盆、左右两枚成熟羽状叶、中央根冠露出且上方留空，未见拳卷状新叶。细粒度物种身份不能由合成图认证；用作题面指定的简化植株场景。','lichen':'已亲自看图：树皮上单块绿色叶状体，裂片宽而有浅网纹，未见棕色盘状子实体。只能确认初始可编辑形态，不能认证Ricasolia virens物种身份。','monarch':'已亲自看图：单条黑白黄条纹幼虫以J形悬挂于横枝下，附着点清晰，头端上弯，无蛹或成蝶。作为声明生成的初始场景，不作真实个体或解剖事实证据。'}[k]
   if ep.exists():
    r['edit_source']={'path':str(ep),'sha256':sha(ep),'role':'edit_source','origin':'new_synthetic_source_scene','generated':True,'source_generator':'imagegen tool; underlying model not independently recorded in candidate card','generation_prompt_path':str(gp),'generation_prompt':json.loads(gp.read_text())[k],'reviewed':True,'visual_review':{'reviewed':True,'reviewer':'assistant','reason':'简单初态、核心编辑位置及可观察性满足本题；不得用作知识事实证据。','observations':obs,'reviewed_at':'2026-09-13'}}
    r['gaps']=[g for g in r['gaps'] if '待准备' not in g and '待检查' not in g and '待核验' not in g]
    r['gaps'].append('编辑原图已核验可用性；其合成属性与细粒度身份限制保留，不等同实拍事实证据。')
   if k=='lichen':
    r['prompt']='Treat the synthetic green foliose thallus in the source as a simplified model of Ricasolia virens before its fruiting bodies have developed. Add several mature apothecia appropriate to that species, keeping the existing thallus lobes, bark, close viewpoint and background. Make the reproductive structures clearly visible. No labels or inset diagrams.'
    r['prompt_zh']='将原图的合成绿色叶状体视作尚未形成子实体的 Ricasolia virens 简化模型。添加适合该物种的数个成熟子囊盘，保持原有地衣裂片、树皮、近景视角和背景，让生殖结构清楚可见。不添加文字或局部小图。'
  if k=='fern':
   r['explicit_target']='Additional diagnostic visual requirements: Add one continuous green young fern stalk growing upward from the visible central crown. Its main terminal tip must curl inward into a clearly visible tight spiral or fiddlehead, with the youngest tip inside the coil. A few small lateral pinnae may begin to unfold below it. It is a developing fern leaf, not a flower, snail or straight closed spear. Retain both mature fronds and the pot.'
   r['explicit_target_zh']='显式诊断追加视觉要求：从可见中央根冠向上新增一枚连续的绿色幼蕨叶轴。主叶尖必须向内卷为清楚可见的紧螺卷或拳卷，最幼嫩顶端位于卷内。下方可有少量开始展开的小羽片。它是发育中的蕨叶，不能画成花、蜗牛或笔直封尖。保留两枚成熟叶和花盆。'
  if k=='shark':
   r['explicit_target']='Additional diagnostic visual requirements: Draw one elongated dark-brown organic egg capsule with broad continuous ribbon-like flanges winding helically around its central body. Show the raised spiral wings clearly from an oblique side view. The capsule must be partly wedged into a narrow rock crevice, contacting rock on both sides so that it is held in place, while most of the spiral body remains visible. Do not substitute a smooth oval egg, a square four-horned skate egg, a metal screw or an egg tied to seaweed by long tendrils.'
   r['explicit_target_zh']='显式诊断追加视觉要求：画一枚长形深棕色有机质卵鞘，宽而连续的带状翼缘沿中央囊体螺旋环绕。从斜侧视角清楚展示隆起的螺旋翼。卵鞘部分楔进狭窄岩隙，并接触两侧岩石而被固定，大部分螺旋囊体仍可见。不能替换为光滑椭圆蛋、四角长角的方形鳐卵、金属螺丝或由长卷须绑在海草上的卵。'
  r['pre_freeze_review']={'reviewed':True,'reviewer':'assistant','decision':'candidate_ready_for_parent_freeze_review','reason':'原题保留概念/阶段/关系，未逐字写出关键视觉答案；逐项知识判据有文本或照片支持；编辑初态已声明合成。五对书鳃计数未做强制判据。','reviewed_at':'2026-09-13','remaining_limits':r['gaps']}
  (OUT/(k+'_candidate.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2));cards.append(r)
 (OUT/'candidates.json').write_text(json.dumps(cards,ensure_ascii=False,indent=2))
 print('wrote',len(cards),'candidate cards',OUT)
if __name__=='__main__':build()
