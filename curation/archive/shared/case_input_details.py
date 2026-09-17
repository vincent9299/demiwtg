"""Read-only presentation of classification audits and recorded model inputs."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import base64
import json
from pathlib import Path
from pipeline import ROOT, STATE, read
from review import esc
from bagel_runner import sha

# Presentation audit: original frozen case labels remain unchanged.
TAGS = {
'dev_direct_pignose': ('特征与结构', '概念自身结构', '鳍状肢和鼻部结构，代表直接应用对象知识。'),
'dev_direct_youmiank': ('特征与结构', '概念自身结构', '食物的卷筒构型与排列，代表制作形态知识。'),
'dev_direct_cashew': ('特征与结构、关系与组织', '概念自身结构', '果实与附属部分的外部位置关系。'),
'dev_conditional_tapir': ('特征与结构、属性与状态', '状态与阶段', '年龄条件选择对应体表花纹；单张幼体图不等于考查完整发育过程。'),
'dev_conditional_puffin': ('特征与结构、属性与状态', '状态与阶段', '季节／繁殖状态选择外观。'),
'dev_conditional_lady_larva': ('特征与结构、属性与状态', '状态与阶段', '龄期选择幼体形态。'),
'dev_relational_pangolin': ('关系与组织', '生物规律、可组合关系', '亲幼携带行为与空间关系；仅画携幼不等于解释机制。不因亲幼互动就认定为跨领域知识。'),
'dev_relational_gargoyle': ('关系与组织、功能与机制', '概念自身结构、环境交互、物理规律', '排水功能约束水流出口和建筑位置。'),
'dev_relational_lady_food': ('关系与组织', '生物规律、可组合关系', '捕食／食物关系；两个生物同域也能有关系知识，不强凑跨域。'),
'dev_compositional_pignose_sexes': ('特征与结构、属性与状态', '概念自身结构、状态与阶段', '同时应用成年雌雄差异，代表比较式组合。'),
'dev_conditional_nasa_argon_oxygen_18': ('规则与约定', '规约与典故', '按指定版本标准及氧含量选表行；属于规约，不是典故。'),
'dev_conditional_nasa_argon_oxygen_21': ('规则与约定', '规约与典故', '与18%题构成阈值对照；不把同一家族算成两种新知识。'),
'dev_nonobject_tir': ('功能与机制、属性与状态', '物理规律、环境交互', '折射率和入射角决定传播结果；自然定律不归为人为规则与约定。'),
'dev_nonobject_rainbow': ('属性与状态、关系与组织', '地理气象、物理规律、环境交互', '主副虹空间关系和反向色序；没有直接考查全部光学机制。'),
'dev_nonobject_wave': ('关系与组织、属性与状态', '物理规律、状态与阶段', '谐波模式与相位决定瞬时弦形；虽背景涉及振动过程，本题只评单时刻，过程变化覆盖较弱。'),
'dev_nonobject_capillary': ('功能与机制、关系与组织、属性与状态', '物理规律、环境交互', '润湿及半径决定平衡高度与曲率；平衡终态不是动态上升过程。'),
}


def scene_data(c):
    d=c.get('scene_dimensions')
    if d is None:
        q=c['question_id']
        if q=='dev_compositional_pignose_sexes':
            scene,combo,premises=['多实例对比'],'多实例',['年龄/生长阶段','数量/编组']
        elif q=='dev_nonobject_capillary':
            scene,combo,premises=['多实例对比','视点剖示'],'多实例',['数量/编组','环境场所','使用状态']
        elif q=='dev_nonobject_rainbow':
            scene,combo,premises=['环境作用'],'光学传播',['环境场所','视角/暴露']
        elif q=='dev_relational_gargoyle':
            scene,combo,premises=['环境作用'],'因果',['使用状态','环境场所']
        elif q in ['dev_relational_lady_food','dev_relational_pangolin']:
            scene,combo,premises=['交互链'],'生态互动',['使用状态']
        else:
            scene,combo,premises=[],None,[]
        d=dict(scene_types=scene,combo_type=combo,premise_types=premises,
               note='回顾性场景标注，仅明确的适用项列出；未列出不等于没有前提或没有知识。简单图也可有复杂知识，标签不是难度等级。')
    return d


def scene_details(c):
    d=scene_data(c)
    out='<details><summary>场景维度：复杂性来自哪里（独立于知识分类）</summary>'
    for title,key in [('场景复杂度来源','scene_types'),('组合主类','combo_type'),('前提类别','premise_types')]:
        value=d.get(key);value='、'.join(value) if isinstance(value,list) else value
        out+='<p><b>'+title+'：</b>'+esc(value or '未标为复杂场景来源／不强行套类')+'</p>'
    out+='<p>'+esc(d['note'])+'</p>'
    if c['question_id']=='dev_scene_ecology':
        out+='<p>严格口径提醒：这里的交互链标签仅指两组局部捕食关系，尚未形成A影响B、再影响C的连续因果链；不要据该标签计多跳难度。</p>'
    if d.get('paired_simple_cases'):out+='<p>可对照的已有简单题：'+esc('、'.join('第'+str(i)+'题' for i in d['paired_simple_cases']))+'。</p>'
    out+='<p>这些描述沿用原 prompt 词表；不按标签数、对象数或跳数自动加难度分。需要同时满足的知识约束见本题应用链与逐项判据，指令执行另审。</p></details>'
    return out


def classification_details(c):
    audit=c.get('classification_audit')
    content, mining, reason = (audit['content'],audit['mining'],audit['reason']) if audit else TAGS[c['question_id']]
    original = '、'.join(c['knowledge_types'])
    return ('<p><b>知识内容分类（本次审核）：</b>'+esc(content)+'</p>'
            '<p><b>知识应用层次：</b>'+esc(c['application_level'])+'</p>'
            '<p><b>代表性与边界：</b>'+esc(reason)+'</p>'+scene_details(c)+
            '<details><summary>分类口径与原标签</summary><p>六类内容回答知识描述什么；领域回答主题归属；应用层次描述如何调用知识。三者不相互替代，也不是题目难度分数。</p>'
            '<p>冻结内容标签：'+esc(original)+'。本次只追加展示审核，不覆盖原记录；标签齐全不表示每类已通过实验验证。</p>'
            '<p>定义：<a href="'+str(ROOT/'curation/DESIGN.md')+':41">六类知识内容</a>；'
            '本次按实际考点归类，没有照搬旧版跨度或跳数门槛。</p></details>')


def actual_input_details(c, batch):
    run = STATE/batch
    jobs = [json.loads(line) for line in (run/'jobs.jsonl').read_text().splitlines() if line.strip()]
    jobs = [j for j in jobs if j['question_id']==c['question_id']]
    out = ['<details><summary>实际给 BAGEL／Gemini 的完整 prompt 与图片输入（全部已记录条件）</summary>',
           '<p>直接读取本次运行保存的输入，不根据题面重新拼装。图片先按顺序传入，再传完整任务及知识文字。edit_source 是待编辑原图；retrieval_reference 是知识参考图。URL引用不会自动变成图像输入。</p>',
           '<p>BAGEL 接收交错的角色文字、解码图片和末尾 prompt；Gemini 的 user 消息接收角色文字、image_url 图片字节和末尾 prompt。下面仅将冗长的 base64 字节替换为经哈希核对的原图路径；文字不省略。右侧有知识图不是由左侧生成图继续编辑。</p>']
    for j in jobs:
        jid=j['job_id']; out.append('<details><summary>'+esc(j['condition']+' · '+jid)+'</summary>')
        out.append('<p>输入图片数量：'+str(len(j['images']))+'。'+('无任何图片输入。' if not j['images'] else '顺序和角色如下；点击路径可打开实际原图。')+'</p>')
        for n,im in enumerate(j['images'],1):
            assert sha(Path(im['path']).read_bytes())==im['sha256']
            out.append('<p><b>Image '+str(n)+' · '+esc(im['role'])+'</b>：<a href="'+esc(im['path'])+'">'+esc(im['path'])+'</a><br>SHA256：'+esc(im['sha256'])+'</p>')
        for model in ['bagel','gemini']:
            d=run/model/'jobs'/jid
            out.append('<details><summary>'+model.upper()+'：完整实际输入与参数</summary>')
            if model=='bagel':
                p=d/'actual_inputs.json'
                if not p.exists():
                    out.append('<p>未找到已保存的实际输入，不能把作业计划冒称实际调用。</p></details>');continue
                payload=read(p)
                assert [x['text'] for x in payload if x['type']=='text'][-1]==j['prompt']
                ims=[x for x in payload if x['type']=='image']
                assert [(x['path'],x['sha256'],x['role']) for x in ims]==[(x['path'],x['sha256'],x['role']) for x in j['images']]
                req=read(d/'request.json')
                params={k:req.get(k) for k in ['model','seed','inference_config','input_protocol','output_geometry_override']}
            else:
                p=d/'request.json'
                if not p.exists():
                    out.append('<p>无已保存请求，未声明调用。</p></details>');continue
                req=read(p); payload=req['messages']; image_i=0
                for msg in payload:
                    for block in msg['content']:
                        if block['type']=='image_url':
                            im=j['images'][image_i]; image_i+=1
                            uri=block['image_url']['url']; assert sha(base64.b64decode(uri.split(',',1)[1]))==im['sha256']
                            block['image_url']['url']='[实际传入 '+uri.split(';',1)[0]+' 字节；展示替换为文件] '+im['path']
                assert image_i==len(j['images'])
                assert payload[-1]['content'][-1]['text']==j['prompt']
                params={k:v for k,v in req.items() if k!='messages'}
            out.append('<pre>'+esc(json.dumps(payload,ensure_ascii=False,indent=2))+'</pre><p>调用参数：</p><pre>'+esc(json.dumps(params,ensure_ascii=False,indent=2))+'</pre><p>原始记录：<a href="'+esc(str(p))+'">'+esc(str(p))+'</a></p></details>')
        out.append('</details>')
    out.append('<p>这里是提供已选定资料的对照实验，未运行自动检索器。Gemini 不保证采用 BAGEL 的随机种子；同题同条件只代表输入条件对应，不代表同一噪声。实际模型名以每次请求记录为准。</p></details>')
    return ''.join(out)
