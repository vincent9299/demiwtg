"""Assemble auditable development accounting; never changes frozen inputs or scores."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import STATE, read
from bagel_runner import sha
from review import esc, picture


def main():
    runs = ['pilot', 'image_primary', 'repeat']
    accounting = {}
    rows = []
    for name in runs:
        run = STATE / name
        jobs = [json.loads(line) for line in (run / 'jobs.jsonl').read_text().splitlines()]
        scores = read(run / 'scores_summary.json')
        assert scores['reviewed'] == len(jobs) * 2 and not scores['missing']
        count = Counter(); cost = 0.0
        for model in ['bagel', 'gemini']:
            for job in jobs:
                d = run / model / 'jobs' / job['job_id']
                result = read(d / 'result.json')
                count[model + '_requests'] += 1
                count[model + '_accepted_single_images'] += bool(result['ok'])
                if result['ok']:
                    assert sha(Path(result['image']).read_bytes()) == result['output_sha256']
                if model == 'gemini':
                    assert (d / 'request.json').exists()
                    auxiliary = d / 'auxiliary_manifest.json'
                    if auxiliary.exists():
                        retained = read(auxiliary)['images']
                        for im in retained:
                            assert sha(Path(im['path']).read_bytes()) == im['sha256']
                        count['gemini_returned_images'] += len(retained)
                    else:
                        count['gemini_returned_images'] += result.get('returned_image_count', int(result['ok']))
                    cost += result.get('usage', {}).get('cost', 0)
        accounting[name] = dict(count, gemini_cost_usd=round(cost, 7),
                                bagel_preannotation_restored=read(run / 'bagel/session/session.json')['preannotation_restored'])
        rows.extend(dict(r, run=name) for r in scores['per_output'])
    assert sum(x['gemini_requests'] for x in accounting.values()) == 60
    assert all(x['bagel_preannotation_restored'] for x in accounting.values())
    rep_ids = read(STATE / 'repeat/protocol.json')['selected_cases']
    repeats = defaultdict(Counter)
    for r in rows:
        if r['question_id'] in rep_ids and r['condition'] in ['baseline', 'multimodal']:
            c = repeats[r['question_id'] + '/' + r['model'] + '/' + r['condition']]
            c['total'] += 1
            c['local_knowledge_pass'] += r['knowledge_pass']
            c['original_joint_pass'] += r['joint_pass']
    replicated = []
    for qid in rep_ids:
        a = repeats[qid + '/bagel/baseline']; b = repeats[qid + '/bagel/multimodal']
        if b['local_knowledge_pass'] > a['local_knowledge_pass']:
            replicated.append(qid)
    result = {
        'version': 'knowledge_application_v1.findings.1',
        'scope': 'Pre-formal development; all assistant judgments, no human gold, no fine-tuning.',
        'accounting': accounting,
        'total_gemini_cost_usd': round(sum(x['gemini_cost_usd'] for x in accounting.values()), 7),
        'pilot_model_conditions': read(STATE / 'pilot/scores_summary.json')['by_model_condition'],
        'pilot_execution_supplement': read(STATE / 'execution_supplement_reviews.json')['by_model_condition'],
        'image_primary_model_conditions': read(STATE / 'image_primary/scores_summary.json')['by_model_condition'],
        'adaptive_three_repeats_including_initial': dict(repeats),
        'replicated_bagel_local_feature_gain_cases': replicated,
        'local_feature_gate_met': len(replicated) >= 2,
        'semantic_warning': 'Frozen local feature checks are insufficient to certify complete species/product correctness. Replication is not SOTA parity and positive families were adaptively selected.',
        'pool': read(STATE / 'material_pool.json')['summary'],
        'retrieval_support': read(STATE / 'pilot/retrieval_support_review.json')['summary'],
        'formal_200_compiled': False,
        'formal_ready_unconditionally': False,
        'remaining_gates': ['Minimal sufficient identity rubric and independent review calibration',
                            'Genuinely complementary image+text evidence cases',
                            'Semantic and source review of candidate pool; 29 domains not experimentally validated',
                            'Independent formal family/source/near-duplicate split and track/count decision',
                            'Actual retrieval generation and retrieval-vs-oracle comparison before RAG performance claims'],
    }
    (STATE / 'findings.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    blocks = ['<!doctype html><meta charset="utf-8"><title>新版出题前验证结论</title>',
              '<style>body{max-width:1120px;margin:30px auto;padding:20px;font:16px/1.65 sans-serif}figure{display:inline-block;width:46%;vertical-align:top;margin:1%}img{max-width:100%;max-height:500px}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:9px}pre{white-space:pre-wrap}</style>',
              '<h1>图文知识获取与应用：出题前开发验证</h1>',
              '<p><b>已找到能暴露知识依赖、且提供正常源资料后改善结果的出题方式。尚未证明整个BAGEL与闭源模型的差距只剩知识，也未编制正式200题。</b></p>',
              '<p>新版本独立保存。13个开发题、两家族追加复验；120次请求，119个符合单图契约的结果，另一次返回3图全部保留。全部主结果由助手逐图审核，不是人工金标准。</p>',
              '<h2>首轮12题：冻结的局部知识判据</h2>',
              '<table><tr><th>模型</th><th>无资料</th><th>文字</th><th>图片</th><th>图文</th></tr>']
    for model in ['bagel', 'gemini']:
        blocks.append('<tr><td>' + model + '</td>' + ''.join('<td>' + str(result['pilot_model_conditions'][model+'/'+c]['knowledge_pass']) + '/12</td>' for c in ['baseline', 'text', 'image', 'multimodal']) + '</tr>')
    blocks += ['</table><p>局部知识通过不等于对象整体正确或整题通过。Gemini图文有一次多图契约失败；图像臂在部分题并无完整知识证据，答对可能来自模型已有知识，不能全归因于参考图。</p>',
               '<h2>完整案例：NGT VX1型号外形迁移</h2>',
               '<p>原题只指定型号与新的湖边木桌场景。正常商品文字没有描述双叉槽、圆面板、底部螺杆布局；实际商品照片支持这些结构。两模型无资料均未全通过，图文均通过这两项结构及场景要求。这是一例受支持的成功，不是对所有型号或完整产品复刻准确度的保证。</p>']
    c = read(STATE / 'image_primary/cases.json')['cases'][0]
    blocks.append('<pre>' + esc(c['prompt']) + '</pre>')
    blocks.append(picture(c['reference_images'][0]['path'], '正常源产品照片：检索参考资料'))
    for model, condition in [('gemini', 'baseline'), ('bagel', 'baseline'), ('bagel', 'multimodal'), ('gemini', 'multimodal')]:
        image_path = STATE / 'image_primary' / model / 'jobs' / f'dev_image_primary_ngt_vx1__{condition}__r1/image.png'
        blocks.append(picture(str(image_path), model + ' · ' + condition))
    blocks += ['<h2>复验与评分校准</h2>',
               '<p>两个已选开发家族各重复3次，BAGEL局部知识均由0/3到3/3。但瓢虫结果仍有毛虫式体形，猪鼻龟仍有鳍/蹼足边界。必须加入来源支持的最低身份结构组合，不能拿局部6/6宣称整体追平。Gemini猪鼻龟有资料仍未稳定全对，保留失败。</p>',
               '<p>执行补充审计纠正了原题没有要求管段两端完整的判分越界，并分离身份与通用执行。原分及补充分均保留；BAGEL首轮图文局部知识与通用执行联合仍为0/12。</p>',
               '<h2>扩量与检索</h2><p>29域×20候选槽=580槽，560个唯一概念；104个有净版docs。候选未批量核验身份，不能当作560道合格题。29域各有材料样例或具体缺口，实测仅覆盖6域。实际BM25前5返回资料完全支持5/12题；该审计没有运行检索后生成。</p>',
               '<h2>完整证据与后续规则</h2><ul>']
    for label, path in [('出题操作规则', Path(__file__).parent/'AUTHORING.md'), ('文字结论与验收边界', STATE/'REPORT.md'),
                        ('原12题全部上下文及输出', STATE/'pilot/review.html'), ('图片主导案例全对照', STATE/'image_primary/review.html'),
                        ('复验全部输出', STATE/'repeat/review.html'), ('评分方法审计', STATE/'method_audit.json'),
                        ('复验语义复核', STATE/'repeat/semantic_audit.json'), ('统计与预算', STATE/'findings.json')]:
        blocks.append('<li><a href="' + esc(str(path.resolve())) + '">' + label + '</a></li>')
    blocks += ['</ul><p>Gemini共60次，报告费用 $' + str(result['total_gemini_cost_usd']) + '；每次GPU实验后均验证Qwen与图片预标注恢复。没有微调或修改老版本。</p>']
    (STATE / 'overview.html').write_text('\n'.join(blocks))
    print(json.dumps({'accounting': accounting, 'local_feature_gate_met': result['local_feature_gate_met'],
                      'formal_200_compiled': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
