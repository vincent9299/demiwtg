"""Push concept sampling before business conversion; decoding stays in demiflow."""
from .dataset_operators import SelectConcept


class SelectSourceRecords:
    def __init__(self, kind, ids=None, sample_rate=1., seed=42, *, enabled=True):
        self.kind=kind
        self.select=SelectConcept(ids,sample_rate,seed)
        self.enabled=enabled

    def __call__(self, row):
        # Preserve decode failures for audit, not as usable materials.
        value=row.get('value')
        if not self.enabled or row.get('error') or not isinstance(value,dict):return True
        if self.kind=='legacy_concepts':
            if not isinstance(value.get('name'),str) or not value['name']:return True
            refs=['legacy:'+value['name']]
        elif self.kind in {'qid_concepts','qid_concepts_base'}:
            # Keep all QID page mappings: early removal could conceal ambiguous pages.
            return True
        elif self.kind in {'legacy_docs','legacy_images'}:
            names=value.get('concepts') or value.get('instances') or []
            if not isinstance(names,list) or any(not isinstance(x,str) for x in names):return True
            refs=['legacy:'+x for x in names]
            if not refs:return True  # Unassociated records remain visible, never invent a link.
        else:return True  # Wiki page mapping still requires the downstream join.
        return any(self.select({'concept_ref':ref})['selected'] for ref in refs)
