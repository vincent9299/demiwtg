"""Production block filtering: same selected rules as the glass cleaning trial."""
from .cleaning_trials import QualityBranches, normalized
from .repair_source_structure import RepairSourceBlocks
from .quality_policy import material_disposition


class FilterDocumentBlocks:
    """CleanDocument row -> repaired clean_text/blocks, with raw offsets and audit.

    No language-specific metric ablations, fuzzy deletion, model calls or refetch.
    """
    def __call__(self, row):
        filtered = QualityBranches(compare_metrics=False)(row)
        repaired = RepairSourceBlocks()(filtered)
        blocks = repaired['repaired_blocks']
        texts, seen = [], {}
        offset = 0
        for block in blocks:
            block.pop('clean_start', None)
            block.pop('clean_end', None)
            if block['decision'] != 'keep' or not block['text'].strip():
                continue
            key = (tuple(block.get('section', [])), normalized(block['text']))
            if block['kind'] != 'heading' and key in seen:
                block.update(decision='exclude', reason='normalized_duplicate_same_section', duplicate_of=seen[key])
                continue
            seen[key] = block['block_id']
            if texts:
                offset += 2
            block['clean_start'] = offset
            offset += len(block['text'])
            block['clean_end'] = offset
            texts.append(block['text'])
        text = '\n\n'.join(texts)
        counts = {**row['clean_counts'], 'output_chars':len(text),
                  'excluded':sum(b['decision']=='exclude' for b in blocks),
                  'excluded_nonblank':sum(b['decision']=='exclude' and bool(b['raw_text'].strip()) for b in blocks),
                  'deferred':sum(b['decision']=='defer' for b in blocks)}
        status = row['clean_status'] if text else 'unavailable'
        audit = {'document_reason':filtered['filter_diagnostics']['document_reason'],
                 'transaction_page_signal':filtered['filter_diagnostics']['transaction_page_signal'],
                 'repairs':repaired['repairs'], 'unresolved_structure':repaired['unresolved_structure']}
        return {**row, 'clean_text':text, 'clean_blocks':blocks, 'clean_counts':counts,
                'clean_status':status, 'clean_version':row['clean_version']+'+block-filter/1',
                'clean_filter':audit,
                'knowledge_eligibility':material_disposition({'text':text, 'status':status, 'warnings':row['clean_warnings']})}
