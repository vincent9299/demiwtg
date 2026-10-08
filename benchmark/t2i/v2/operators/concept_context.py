"""T2I 审定资料适配：保留来源引用，按当前行读取原文并确定性排版，不重新审定或检索。"""
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pyarrow as pa

from demiflow.collect.contracts import SELECTED_BLOCK
from demiflow.collect.documents import read_document
from demiflow.execution.artifacts import resolve_local_artifact
from preparation.concepts.operators.schema import AUTHORING_CONTEXT as BASE_CONTEXT, STRINGS


# 出题侧的消费投影；上游 assessment/adopted 契约不变，正文仍保存在独立对象中。
AUTHORING_CONTEXT = pa.struct([
    *BASE_CONTEXT,
    ('identity_evidence_ids', STRINGS), ('gaps', STRINGS),
    ('evidence', pa.list_(SELECTED_BLOCK)),
])


class ConceptEvidenceError(ValueError):
    """已声明的审定证据无法完整交付，属于技术失败而非业务否决。"""


def evidence_ids(record):
    """按身份、事实的首次引用顺序编号，不受 selected 行顺序影响。"""
    return list(dict.fromkeys([
        *(record['identity_evidence_ids'] or []),
        *(eid for fact in record['core_facts'] for eid in (fact['evidence_ids'] or [])),
    ]))


def project_context(row, *, source):
    assessment = row['assessment']
    record = {key: row[key] for key in ('source_record_id', 'concept_id', 'original_name', 'assessment_id')}
    record.update(adopted_source=source, **{key: assessment[key] for key in (
        'canonical_name', 'definition', 'concept_kind', 'qualifiers', 'core_facts', 'task_sketch',
        'identity_evidence_ids', 'gaps',
    )})
    needed = set(evidence_ids(record))
    # 此时只传递引用；选样完成后才读取正文。保留重复引用供读前校验，不静默覆盖冲突。
    record['evidence'] = sorted([
        {field.name: selected[field.name] for field in SELECTED_BLOCK}
        for selected in row['selected'] or [] if selected['evidence_id'] in needed
    ], key=lambda item: item['evidence_id'])
    return record


def _read_evidence(record, ids):
    """只读本行实际引用的不可变块；同文档读一次，由平台核验内容 SHA 和文档结构。"""
    selected = {}
    for item in record['evidence']:
        eid = item['evidence_id']
        if eid in selected:
            raise ConceptEvidenceError(f'duplicate_evidence_id: {eid}')
        selected[eid] = item
    documents, evidence = {}, []
    for eid in ids:
        if eid not in selected:
            raise ConceptEvidenceError(f'missing_evidence_reference: {eid}')
        item = selected[eid]
        ref, block_id = item['document_ref'], item['block_id']
        if not ref or eid != f'{ref["sha256"]}:{block_id}':
            raise ConceptEvidenceError(f'evidence_id_reference_mismatch: {eid}')
        key = (ref['uri'], ref['sha256'])
        if key not in documents:
            try:
                document = read_document(ref)
            except (OSError, ValueError, TypeError) as error:
                raise ConceptEvidenceError(f'evidence_document_unavailable: {eid}: {error}') from error
            documents[key] = (document['source'], {block['block_id']: block for block in document['blocks']})
        source, blocks = documents[key]
        if block_id not in blocks:
            raise ConceptEvidenceError(f'missing_evidence_block: {eid}')
        evidence.append((source, blocks[block_id]))
    return evidence


def document_resources(record):
    """本行可补读的固定文档；编号按已有证据首次引用顺序稳定生成。"""
    by_id = {item['evidence_id']: item for item in record['evidence']}
    refs = []
    for eid in evidence_ids(record):
        ref = by_id[eid]['document_ref']
        if ref not in refs:
            refs.append(ref)
    return {f'D{i}': {'document_ref': dict(ref), 'url': '', 'bindings': [], 'eligible': True}
            for i, ref in enumerate(refs, 1)}


def render_concept_context(record, *, include_document_ids=False, include_document_paths=False):
    """给作者概念定义和材料入口；事实、附加限定、缺口及方向仅留存溯源。"""
    ids = evidence_ids(record)
    if not ids or not record['identity_evidence_ids'] or any(
            not fact['evidence_ids'] for fact in record['core_facts']):
        raise ConceptEvidenceError('missing_identity_or_fact_evidence')
    evidence = _read_evidence(record, ids)
    resources = document_resources(record) if include_document_ids or include_document_paths else {}
    references = {item['evidence_id']: item['document_ref'] for item in record['evidence']}
    lines = ['## 概念审定资料', '', '### 概念说明', '',
             f'本次概念是“{record["canonical_name"]}”。{record["definition"]}']
    if record['original_name'] != record['canonical_name']:
        lines.append(f'来源中的名称是“{record["original_name"]}”，审定后使用上述规范名称。')
    # 原审定记录完整保留；不把事实/待核实清单或任务限定作为候选考点交给作者。
    lines.extend(['', '### 依据材料'])
    for i, (source, block) in enumerate(evidence, 1):
        lines.extend(['', f'#### 审定材料 E{i}', '', f'来源：{source["title"]}',
                      f'链接：{source["url"]}'])
        if include_document_ids or include_document_paths:
            document_id = next(name for name, item in resources.items() if item['document_ref'] == references[ids[i-1]])
            lines.append(f'可补读文档：{document_id}（同一固定版本的完整文档）')
        if source['final_url'] != source['url']:
            lines.append(f'实际页面：{source["final_url"]}')
        if block['headings']:
            lines.append('所在章节：' + ' / '.join(block['headings']))
        lines.append(f'原文块：{block["block_id"]}')
    if include_document_paths:
        lines.extend(['', '### 可按需查阅的本地文档', '',
                      '以下是审定时保存的 JSON 文档快照；source 记录来源，blocks 包含 block_id、headings 和 text。',
                      '引用的原文未在本次输入中展开，可用文件工具读取这些固定快照；网络页面可能已更新，应区分两者。'])
        for name, item in resources.items():
            ref = item['document_ref']
            uri = urlsplit(ref['uri'])
            lines.extend(['', f'#### {name}', f'SHA256：{ref["sha256"]}'])
            if uri.scheme == 'file':
                path = resolve_local_artifact(Path(unquote(uri.path)))
                lines.append('固定文档文件：' + json.dumps(str(path), ensure_ascii=False))
            else:
                lines.append('固定文档 URI：' + ref['uri'])
    return '\n'.join(lines)
