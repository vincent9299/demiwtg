"""Lossless quote-format alignment to a unique original source span."""
import re

def align_source_quote(quote, source):
    """Recover a unique literal span after whitespace/table-border formatting only."""
    if not isinstance(quote,str) or not quote or quote in source:return quote
    table=bool(re.search(r"\|\s*:?-{3,}",source))
    def keep(c):return not c.isspace() and not (table and c=='|')
    positions=[i for i,c in enumerate(source) if keep(c)]
    normalized=''.join(source[i] for i in positions)
    query=''.join(c for c in quote if keep(c))
    if not query:return quote
    start=normalized.find(query)
    if start<0 or normalized.find(query,start+1)>=0:return quote
    return source[positions[start]:positions[start+len(query)-1]+1]

