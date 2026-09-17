"""Prepare/validate business data around native demiflow map_prompt_async calls."""
from curation.v4.ops.knowledge_stages import (Stage, OrganizeMaterials, ExportCandidates, ResolveIdentity, ExtractKnowledge,
                               ConsolidateKnowledge, CheckImageSupport)

class KnowledgeRow:
    """Native actor: business validation only; map_cached owns row persistence."""
    async def __call__(self,row):
        if row.get('blocked') and self.label!='export':
            return {**row,'skipped_stages':row.get('skipped_stages',[])+[self.label]}
        try:return await self.process(row)
        except (ValueError,KeyError,TypeError) as error:
            return {**row,'blocked':{'stage':self.label,'reason':type(error).__name__,'detail':str(error)}}
    async def aclose(self):pass

class SelectPassagesAndImages(KnowledgeRow, OrganizeMaterials):
    """Deduplicate accepted sources and select whole text blocks/available images."""

class BuildCandidateRecords(KnowledgeRow, ExportCandidates):
    """Build final candidates, deferred items, and remaining evidence tasks."""
    async def process(self,row):return self.build(row)

class PrepareIdentity(KnowledgeRow, ResolveIdentity):
    label='identity_prepare'
    async def process(self,row):return await self.prepare(row)

class PrepareExtraction(KnowledgeRow, ExtractKnowledge):
    label='extract_prepare'
    async def process(self,row):return await self.prepare(row)

class PrepareConsolidation(KnowledgeRow, ConsolidateKnowledge):
    label='consolidate_prepare'
    async def process(self,row):return await self.prepare(row)

class PrepareImageSupport(KnowledgeRow, CheckImageSupport):
    label='evidence_prepare'
    async def process(self,row):return await self.prepare(row)


def apply_prompt_result(op,row):
    """Keep model failures as blocked rows, never drop the concept batch."""
    if row.get('prompt_error'):
        return {**row,'blocked':{'stage':op.label,'reason':row['prompt_error']['type'],**row['prompt_error']}}
    if 'prompt_result' not in row:return row  # e.g. no pixels/facts, explicitly not_run
    clean={k:v for k,v in row.items() if k not in {'prompt_result','prompt_call','prompt_error'}}
    return op.apply(clean,row['prompt_result'],row['prompt_call'])

class ApplyIdentity(KnowledgeRow, ResolveIdentity):
    async def process(self,row):return apply_prompt_result(self,row)

class ApplyExtraction(KnowledgeRow, ExtractKnowledge):
    async def process(self,row):return apply_prompt_result(self,row)

class ApplyConsolidation(KnowledgeRow, ConsolidateKnowledge):
    async def process(self,row):return apply_prompt_result(self,row)

class ApplyImageSupport(KnowledgeRow, CheckImageSupport):
    async def process(self,row):return apply_prompt_result(self,row)
