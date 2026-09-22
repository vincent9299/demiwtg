"""Prepare and validate identity data around native map_prompt_async calls."""
from curation.preparation.ops.identity import ResolveIdentity

class KnowledgeRow:
    """Native actor: business validation only; map_cached owns row persistence."""
    async def __call__(self,row):
        if row.get('blocked') and self.label!='export':
            return {**row,'skipped_stages':row.get('skipped_stages',[])+[self.label]}
        try:return await self.process(row)
        except (ValueError,KeyError,TypeError) as error:
            return {**row,'blocked':{'stage':self.label,'reason':type(error).__name__,'detail':str(error)}}
    async def aclose(self):pass

class PrepareIdentity(KnowledgeRow, ResolveIdentity):
    label='identity_prepare'
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
