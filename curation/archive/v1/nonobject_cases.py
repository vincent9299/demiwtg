"""Freeze four source-grounded non-object development cases; notebook consumer only."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from pathlib import Path
from pipeline import ROOT, STATE, read, check_case
from bagel_runner import encoded, publish, sha, validate_jobs

OUT = STATE / 'nonobject_v1'


def main():
    concepts_path = ROOT / 'datasets/demiwtg/meta/concepts.json'
    concepts = read(concepts_path)['concepts']
    lookup = {c['name']: (i, c) for i, c in enumerate(concepts)}
    pages_path = ROOT / 'state/collect/docs_clean/pages_clean.jsonl'
    pages = {n: json.loads(line) for n, line in enumerate(pages_path.open(), 1) if n in [591, 1366, 1676, 4170]}

    def concept_record(name, suggested=None):
        if name in lookup:
            i, c = lookup[name]
            return {'concept_name': name, 'status': 'existing_concept', 'taxonomy_paths': c['taxonomy'],
                    'concepts_path': str(concepts_path), 'json_pointer': f'/concepts/{i}',
                    'concept_record_sha256': sha(encoded(c)), 'suggested_semantic_path': suggested}
        return {'concept_name': name, 'status': 'proposed_new_concept_not_written_to_taxonomy',
                'taxonomy_paths': [], 'concepts_path': str(concepts_path), 'json_pointer': None,
                'lookup_scope': 'Exact name and capillary/毛细 aliases searched in current concept list; related instrument/blood-vessel names are not this phenomenon.',
                'suggested_semantic_path': suggested}

    def local(sid, line, start, end, scope):
        p = pages[line]; a = p['text'].index(start); b = p['text'].index(end, a) if end else len(p['text'])
        excerpt = p['text'][a:b].strip(); dest = OUT / 'sources' / (sid + '.json')
        publish(dest, encoded(p))
        return {'source_id': sid, 'origin': 'existing_local_clean_document', 'url': p['url'],
                'quote': excerpt, 'support_scope': scope, 'library_path': str(pages_path), 'library_line': line,
                'page_sha': p['page_sha'], 'associated_concepts': p.get('concepts', []),
                'text_char_start': a, 'text_char_end': b, 'snapshot_path': str(dest),
                'snapshot_sha256': sha(dest.read_bytes())}

    def external(sid, url, title, locator, quote, scope):
        data = {'url': url, 'title': title, 'locator': locator, 'quote': quote,
                'verified': 'Assistant read official page via web on 2026-09-12; short excerpt snapshot, not full HTML.'}
        dest = OUT / 'sources' / (sid + '.json'); publish(dest, encoded(data))
        return {'source_id': sid, 'origin': 'external_verified_excerpt', 'url': url, 'title': title,
                'locator': locator, 'quote': quote, 'support_scope': scope, 'snapshot_path': str(dest),
                'snapshot_sha256': sha(dest.read_bytes()), 'library_path': None}

    tir_local = local('tir_library',591,'When light propagates' if 'When light propagates' in pages[591]['text'] else 'If the angle of incidence','When light strikes', '库内支持高折射率到低折射率界面的临界角与全反射；不能仅凭概念挂载推定材料充分。')
    reflection = local('reflection_library',1366,'Here, the angle of the reflected light','The common law', '入射角和反射角均相对法线，且相等。')
    tir_external = external('tir_openstax','https://openstax.org/books/university-physics-volume-3/pages/1-4-total-internal-reflection','OpenStax University Physics Vol.3 §1.4','Equation 1.5; Figure 1.14','Total internal reflection occurs for any incident angle greater than the critical angle', '临界角 sin(theta_c)=n2/n1，仅n1>n2适用；几何光学下无传播折射光。')
    wave_local = local('wave_library',4170,'A standing wave,','A soliton or', '库内支持两反向波叠加、固定端节点和相邻节点之间波腹；未单独给出第二泛音计数。')
    wave_external = external('wave_openstax','https://openstax.org/books/university-physics-volume-1/pages/16-6-standing-waves-and-resonance','OpenStax University Physics Vol.1 §16.6','Equations 16.15–16.16; paragraph following Figure 16.29','The frequency of the n=3 normal mode is the second overtone (or third harmonic) and so on.', '两端固定弦第二泛音对应第三谐波；lambda_n=2L/n，与库内节点知识合用。')
    cap_local = local('capillary_library',1676,'Of the many hydrostatic phenomena','It follows from equations', '库内支持润湿时凹形弯月面与液面上升；清洗稿公式缺失，不用它证明半径比例。该段是正文，未使用页面AI问答。')
    cap_external = external('capillary_openstax','https://openstax.org/books/college-physics-2e/pages/11-8-cohesion-and-adhesion-in-liquids-surface-tension-and-capillary-action','OpenStax College Physics 2e §11.8','Equation 11.51 and Figure 11.32','The smaller the tube, the greater the height reached.', 'h=2 gamma cos(theta)/(rho g r)，同液体同接触角下高度与内半径成反比。')
    rainbow = external('rainbow_nws','https://www.weather.gov/fgz/Rainbow','NWS Flagstaff: How Do Rainbows Form?','What makes the bow? / What makes a double rainbow?','This effect produces the secondary rainbow, with the colors reversed from the primary rainbow.', '主虹蓝色在内红色在外；副虹角半径约50°大于主虹约42°且色序反转。')

    definitions = [
        ('dev_nonobject_tir', '全反射', '属性与状态', 'conditional', ['属性与状态','规则与约定'],
         '绘制一幅清楚的侧视几何光学示意图：水平水面下方为水（折射率1.33），上方为空气（折射率1.00）。一束细光线从水下左侧射向水面，入射方向与该处竖直法线夹角为60°。展示光线到达界面后的传播路径，保留入射光线、水面和该处虚线法线。画面只表示传播光线，不表示近场。',
         'Draw a clear side-view geometrical-optics illustration. Water (refractive index 1.33) is below a horizontal surface and air (index 1.00) is above it. A thin ray arrives at the surface from the lower left, at 60 degrees to the vertical surface normal. Show its subsequent propagation path, retaining the incident ray, surface, and a dashed normal at the incidence point. Depict propagating rays only, not near fields.',
         '[tir_library / tir_openstax] For light travelling from refractive index n1 to a lower index n2, the critical angle satisfies sin(theta_c)=n2/n1. At incidence above that angle the light undergoes total internal reflection; geometrical optics has no transmitted propagating ray. [reflection_library] Reflection angles are measured from the normal; the reflected angle equals the incident angle.',
         '由高折射率向低折射率介质传播时，临界角满足sin(theta_c)=n2/n1；超过临界角发生全反射。反射角与入射角相等，均从法线量起。这里不把本题数值算出的答案写进资料。',
         [tir_local, reflection, tir_external],
         [('K1','界面后传播光线留在水中，不画进入空气的传播折射光。','水面入射点与上方空气区'),('K2','反射线从入射点向右下方离开，与法线的夹角近似60°。','水下入射与反射两段')],
         '1.33→1.00，入射角60°；只考传播光线', '临界角约48.8°，60°超过它；再应用反射定律，得到水下向右下方反射。',
         ['允许绘图角度小误差，不以像素量角判罚；把60°误当相对水面则是条件应用错误。','不考倏逝场；前提已排除近场图。'],
         ['这是原理示意图题，不是自然照片形态题。库内知识在“镜面反射”文档，未直接挂在“全反射”概念下。']),
        ('dev_nonobject_rainbow','彩虹','自然景观','relational',['属性与状态','关系与组织'],
         '一张乡村开阔草地上的自然天气照片：低角度太阳位于摄影者身后，前方较均匀的雨幕中同时出现主虹和副虹，两道弧均清楚可辨，地平线较低。保留自然的连续色带，不添加文字。',
         'A natural weather photograph over an open rural meadow. The low sun is behind the photographer. Both a primary and a secondary rainbow are clearly visible in a fairly uniform rain curtain ahead, above a low horizon. Preserve natural continuous color bands and add no text.',
         '[rainbow_nws] A primary rainbow has red on its outer edge and blue on its inner portion, with an angular radius near 42 degrees. A secondary rainbow has an angular radius near 50 degrees and reverses the color order of the primary. These bows are seen with sunlight arriving from behind the observer.',
         '主虹的外缘偏红、内缘偏蓝，角半径约42°；副虹角半径约50°，色序相反。两者的大小关系和色序需同时成立。',
         [rainbow],
         [('K1','副虹位于主虹外侧，不是两条半径相同或相互交叉的弧。','两道虹的整体位置'),('K2','主虹外红内蓝／紫，副虹内红外蓝／紫，色序反向。','两道虹的内外边缘')],
         '背后低太阳、前方雨幕，主虹和副虹均可辨', '由两类虹不同角半径和相反色序，得到外侧副虹与反转色带。',
         ['连续光谱不要求七条等宽色带；颜色可自然渐变。','不把副虹亮度和虹间暗带作为本轮强制判据；云层及曝光可能影响比较。'],
         ['概念来自库内；净版全文按双虹英文和中文词检索未找到足够支持，知识使用外部NWS。','主虹副虹同属“彩虹”概念的关系情境，不声明新增实体或全域覆盖。']),
        ('dev_nonobject_wave','驻波','知识与学科','conditional',['过程与变化','关系与组织'],
         '绘制实验室中一根均匀细弦的侧视高速瞬时照片。两端固定，张力近似恒定，弦已形成稳定的纯第二泛音横向振动；在各波腹达到最大位移的同一瞬间拍摄。完整展示两固定端之间的一根弦，背景简洁，不加位移包络、轨迹叠影或文字。',
         'A side-view high-speed instantaneous laboratory photograph of one uniform thin string, fixed at both ends under approximately constant tension. It vibrates transversely in its pure second overtone. Capture the instant when the antinodes reach maximum displacement. Show the whole single string between its fixed ends against a simple background, without envelopes, multiple-exposure trails, or text.',
         '[wave_library] At a fixed end, two opposed waves cancel to form a node. Halfway between adjacent nodes is an antinode. [wave_openstax] For a uniform string fixed at both ends, lambda_n=2L/n and f_n=n*f_1. The fundamental is the first harmonic; the second overtone is the third harmonic.',
         '固定端是节点，相邻节点间有波腹。均匀、两端固定弦满足lambda_n=2L/n；基频是第一谐波，第二泛音是第三谐波。资料给通则，题目需要把名称换成模态并画瞬时形状。',
         [wave_local,wave_external],
         [('K1','弦沿长度有三个波腹、两个内部节点，加上两端共四个节点。','两端之间全部弦'),('K2','最大位移瞬间相邻波腹在平衡线两侧交替，形成上-下-上或下-上-下的单根连续弦。','三个波腹与固定端连线')],
         '两端固定均匀弦；纯第二泛音；最大位移瞬间', '第二泛音→n=3→三个半波；边界节点与相邻波腹反相→三个交替弯曲区。',
         ['上下镜像均正确；不要求固定振幅、弦色或器材品牌。','节点位置允许小误差；单张照片只核验给定模态的瞬时形状，不声称由照片证明稳定运动。'],
         ['库内“驻波”只挂海洋波浪；本题是弦上驻波，主考领域另审为知识与学科，原挂载不改。','库内Wave正文包含节点，但第二泛音的阶次映射由外部教材补足。']),
        ('dev_nonobject_capillary','毛细现象','知识与学科','compositional',['过程与变化','关系与组织'],
         '一幅实验室侧视放大剖面插图：三根竖直、洁净、两端开口的圆形玻璃毛细管，插在同一个宽大的室温纯水槽中，下端浸入水中，三根管口均高于可能到达的液面。玻璃被水充分润湿，忽略蒸发，系统已静止达到平衡。三管从左到右内半径为0.25、0.50、1.00毫米。清楚展示槽内自由液面、各管内部水柱和弯月面，不加文字。',
         'A magnified side-view cutaway laboratory illustration of three clean vertical circular glass capillary tubes, open at both ends, dipping into the same broad reservoir of pure water at room temperature. Their lower ends are submerged and their tops extend above any equilibrium liquid level. Water completely wets the glass; neglect evaporation and show static equilibrium. Their inner radii, from left to right, are 0.25, 0.50, and 1.00 millimeters. Clearly show the reservoir free surface, water columns, and menisci, without text.',
         '[capillary_library] For a liquid that wets a narrow glass tube, the meniscus is concave and surface tension lifts it above the external liquid surface. [capillary_openstax] At equilibrium the capillary height measured above the reservoir is h=2*gamma*cos(theta)/(rho*g*r). Here gamma is surface tension, theta contact angle, rho density, g gravity, and r inner tube radius.',
         '润湿玻璃时，管内形成凹形弯月面，液面升高。平衡高度相对于槽内自由液面量起，h=2 gamma cos(theta)/(rho g r)。同液体、同润湿条件下，内半径越小，上升越高。',
         [cap_local,cap_external],
         [('K1','三根管内水面均高于槽内自由液面，且左最高、中次之、右最低。','共同槽液面与三管顶端水面'),('K2','各管内弯月面边缘沿玻璃较高、中央较低，呈凹形。','三处气液界面')],
         '同槽同液体、完全润湿、静止平衡；半径从左到右增大', '润湿→凹弯月面与上升；高度反比于内半径→左高中间次之右低。',
         ['理想高度比例4:2:1仅作辅助；主判据不要求从插图精确量取毫米。','不把玻璃折射造成的管壁外观变化当液面错；界面看不清记无法观察。'],
         ['未找到精确“毛细现象”概念键，作为建议新增概念；没有写回taxonomy或concepts。','库内流体力学文档正文支持润湿与弯月面，公式清洗丢失，外部教材补齐半径规律。'])
    ]
    cases=[];jobs=[]
    for qid, concept, domain, level, types, zh, prompt, knowledge, knowledge_zh, sources, checks, given, result, exceptions, gaps in definitions:
        meta=concept_record(concept,'知识与学科 / 物理学（建议语义归属，未写回）' if concept in ['驻波','毛细现象'] else None)
        c={'question_id':qid,'concept':concept,'taxonomy_record':meta,'domain':domain,'application_level':level,'knowledge_types':types,'task':'t2i','prompt':prompt,'prompt_zh':zh,'knowledge_text':knowledge,'knowledge_zh':knowledge_zh,'sources':sources,'reference_images':[], 'edit_source':None,
           'application_links':[{'given':given,'facts':[s['source_id'] for s in sources],'visual_result':result}],
           'knowledge_checks':[{'id':i,'criterion':v,'observable_region':region,'source_ids':[s['source_id'] for s in sources],'exceptions':[]} for i,v,region in checks],
           'execution_checks':['遵守题面指定的介质／器材／数量和视角；所需界面或弦全貌可观察。','不添加题面排除的文字或轨迹叠影；知识结果独立判断。'],
           'exceptions':exceptions,'gaps':gaps,'knowledge_family_id':concept,'evidence_mode':'text_primary','readiness':'assistant_source_checked_development_not_human_gold','quality_notes':'新增开发小样；概念、知识来源、模型难度分开。不因无资料失败或有资料成功准入正式题。参考资料为文字，没有图片知识输入。','evidence_sufficiency':{'text':[k[0] for k in checks],'image':[],'multimodal':[k[0] for k in checks]}}
        # This batch intentionally has two text conditions, not the four-arm image pilot.
        assert c['sources'] and c['knowledge_checks'] and not c['reference_images']
        for s in sources:
            assert s['quote'] and Path(s['snapshot_path']).exists()
            assert sha(Path(s['snapshot_path']).read_bytes()) == s['snapshot_sha256']
        cases.append(c)
        for cond in ['baseline','text']:
            p=prompt+'\n\nProduce one image fulfilling the task. Use relevant supplied source knowledge to determine the required visible result; do not turn the response into a source-page reproduction.'
            if cond=='text':p+='\n\nSOURCE KNOWLEDGE (source-grounded paraphrase, not verbatim full pages)\n'+knowledge
            jobs.append({'job_id':qid+'__'+cond+'__r1','question_id':qid,'task':'t2i','condition':cond,'prompt':p,'images':[],'seed':20260912})
    publish(OUT/'cases.json',encoded({'version':'nonobject_v1','cases':cases}))
    publish(OUT/'protocol.json',encoded({'conditions':['baseline','text'],'cases':4,'jobs_per_model':8,'gemini_request_cap':8,'repeat_count':1,'scope':'new user-requested non-object supplement, separate from prior60-request study','review':'assistant, source-bound K; all outputs retained; difficulty/gain not guaranteed','knowledge_input':'source-grounded paraphrases, not full original excerpts or raw image references','no_summary_html':True}))
    publish(OUT/'jobs.jsonl',''.join(json.dumps(j,ensure_ascii=False)+'\n' for j in jobs).encode())
    validate_jobs(OUT/'jobs.jsonl')
    # Metadata for the original12, without rewriting their frozen records.
    original=[]
    aliases={'七星瓢虫·四龄幼体':'七星瓢虫','七星瓢虫食物关系':'七星瓢虫','猪鼻龟·成年雌雄比较':'猪鼻龟','穿山甲携幼':'穿山甲','功能性滴水嘴兽':'滴水兽'}
    for c in read(STATE/'pilot/cases.json')['cases']:
        name=aliases.get(c['concept'],c['concept'])
        if 'NASA' in name:name='NASA GSFC-STD-8006A 气体警示颜色规则'
        original.append({'question_id':c['question_id'],'taxonomy_record':concept_record(name)})
    publish(OUT/'original_case_taxonomy.json',encoded(original))
    print(json.dumps({'cases':4,'jobs_per_model':8,'out':str(OUT)}))


if __name__=='__main__':main()
