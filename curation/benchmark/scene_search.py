"""Scene search from the shared image manifest and bounded external providers.

Search uses model-produced initial-scene queries, never the hidden answer/target.
Only ranked candidates' pixels are opened. Results, failures and exact input
metadata are frozen per query so checkpoint resumes do not repeat network calls.
"""
import asyncio
import hashlib
import os
import heapq
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image

from curation.preparation.materials import duplicate, pixels, tokens
from curation.preparation.records import digest, run_records, store_blob
from curation.preparation.materials import asset_pixels
from curation.benchmark.operators import fail





def _plain(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or '')


def _partition(sha, branch):
    # Same target/source reservation used by the existing training target search.
    return branch != 'training' or int(digest(sha)[:8], 16) % 2 == 1


class SceneSearch:
    def __init__(self, run, knowledge, guard, config):
        self.run, self.guard = Path(run), guard
        self.options = config['scene_search']
        self.limit = config['max_source_images']
        self.references = [{**m['asset'], 'dhash': pixels(m['asset'])[2]}
                           for m in knowledge if m['kind'] == 'image']

    def binding(self, row, phase):
        return {'phase': phase, 'queries': row['focus']['search_queries'],
                'scene_requirements': row['focus']['scene_requirements'],
                'branch': row['branch'], 'options': self.options, 'limit': self.limit,
                'references': [a['sha256'] for a in self.references],
                'split_registry': self.guard.registry}

    def cached(self, binding):
        key = 'scene_search/' + digest(binding)
        return key, run_records(self.run).get(key)

    def accept_pixels(self, asset, branch):
        actual = digest(asset_pixels(asset))
        run_records(self.run).put('observed_asset/' + asset['sha256'],
                  {'path': asset.get('path'), 'blob_ref':asset.get('blob_ref'),
                   'actual_sha256': actual, 'expected_sha256': asset['sha256']})
        if not _partition(asset['sha256'], branch):
            raise ValueError('Reserved for training targets')
        _, mime, dhash = pixels(asset)
        asset = {**asset, 'mime': mime, 'dhash': dhash}
        if any(duplicate(asset, ref) for ref in self.references):
            raise ValueError('Published reference or near duplicate')
        if not self.guard.allows_asset(asset, branch == 'training'):
            raise ValueError('Formal-test image/source reservation')
        return asset

    def local(self, row):
        binding = self.binding(row, 'local')
        cache, saved = self.cached(binding)
        if saved:
            return saved
        source = self.options.get('image_ref')
        trace = {'source':source, 'query_fields':['focus.search_queries'],
                 'queries':binding['queries'], 'scope':'declared image entity table',
                 'method':'scene_query_lexical/1', 'records_scanned':0,
                 'metadata_matches':0, 'pixels_inspected':0, 'excluded':[],
                 'results_are_candidates_not_verified_sources':True}
        if not source:
            result = {'candidates':[], 'trace':{**trace,'error':'No image DatasetRef selected'}}
            run_records(self.run).put(cache,result)
            return result
        from demiflow.lance.refs import DatasetRef
        from project import resolve_root
        dataset = DatasetRef.from_dict(source).open(resolve_root())
        query_sets = [tokens(q) for q in binding['queries']]
        terms = set().union(*query_sets)
        if not terms or self.limit == 0:
            result = {'candidates': [], 'trace': {**trace, 'error': 'Empty query terms or zero image budget'}}
            run_records(self.run).put(cache, result)
            return result
        pattern = re.compile('|'.join(re.escape(t) for t in sorted(terms, key=lambda x: (-len(x), x))))
        heap = []
        ceiling = self.options['max_metadata_records']
        metadata_limit = self.options['metadata_candidates']
        stop = False
        for batch in dataset.scanner(columns=['sha256','concepts','sources','availability']).to_batches():
            if stop:break
            for entity in batch.to_pylist():
                if ceiling and trace['records_scanned'] >= ceiling:
                    stop=True;break
                offset=trace['records_scanned'];trace['records_scanned']+=1
                if entity['availability']!='available':continue
                from collect.materials import generation_origin
                if generation_origin(entity) in {'generated','synthetic','ai_generated'}:continue
                sha=entity['sha256']
                if not _partition(sha,row['branch']):continue
                for source_index, origin in enumerate(entity['sources']):
                    if origin.get('identity') is False:continue
                    searchable=' '.join(_plain(origin.get(k)) for k in ('caption','concepts','title'))
                    if not pattern.search(searchable.lower()):continue
                    found=tokens(searchable)
                    score=max((len(q & found)/max(1,len(q)) for q in query_sets),default=0)
                    if score<=0:continue
                    trace['metadata_matches']+=1
                    record={**origin,'sha256':sha,'_dataset_ref':source,
                            'concepts':origin['concepts']}
                    entry=(score,sha,offset,source_index,record)
                    if len(heap)<metadata_limit:heapq.heappush(heap,entry)
                    elif entry[:4]>heap[0][:4]:heapq.heapreplace(heap,entry)
        trace.update(full_manifest_scanned=trace['records_scanned']==dataset.count_rows(),
                     metadata_budget=ceiling,ranked_metadata=len(heap))
        candidates, retained_metadata = [], []
        for score, sha, offset, source_index, record in sorted(heap, key=lambda e: (-e[0], e[1], e[2], e[3])):
            retained_metadata.append({'score': score, 'byte_offset': offset, 'record': record})
            if len(candidates) >= self.limit:
                continue
            path = record.get('path') or record.get('blob_path')
            source_info = {k: record[k] for k in ('landing_url', 'content_url', 'url', 'license', 'author', 'source', 'fetched_at') if record.get(k)}
            if not source_info:
                continue
            try:
                trace['pixels_inspected'] += 1
                asset = self.accept_pixels({'path': path, 'sha256': sha, 'source': source_info,
                    'origin': record.get('generation_origin', 'not_verified'),
                    'description': record.get('caption') or record.get('description') or '',
                    'source_concepts': record.get('instances', record.get('concepts', [])),
                    'search_score': score, 'metadata_origin': {'dataset_ref':record['_dataset_ref'], 'source_record_id':record['source_record_id']}}, row['branch'])
                if not any(duplicate(asset, a) for a in candidates):
                    candidates.append(asset)
            except (ValueError, OSError) as error:
                trace['excluded'].append({'sha256': sha, 'reason': str(error)})
        result = {'candidates': candidates, 'trace': trace, 'ranked_metadata': retained_metadata}
        run_records(self.run).put(cache, result)
        return result

    async def fetch(self, client, url, *, params=None, max_bytes=None):
        """One GET; freeze successes and failures, without implicit retry loops."""
        request = {'url': url, 'params': params, 'max_bytes': max_bytes}
        key = digest(request)
        records = run_records(self.run)
        journal = 'scene_http/' + key + '/response'
        old = records.get(journal)
        if old is not None:
            if old.get('error'): raise ValueError(old['error'])
            from demiflow.lance.blobs import BlobRef
            from project import resolve_root
            return BlobRef(**old['body_ref']).read(resolve_root())
        started = 'scene_http/' + key + '/request'
        if records.get(started) is not None:
            raise ValueError('Interrupted external request; use a new run to retry')
        records.put(started,request)
        try:
            chunks, size = [], 0
            async with client.stream('GET', url, params=params) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > (max_bytes or self.options['max_download_bytes']):
                        raise ValueError('External response exceeds byte budget')
                    chunks.append(chunk)
            data = b''.join(chunks)
            body_ref = store_blob(self.run,data)
            records.put(journal, {'request':request, 'body_ref':body_ref.to_dict(),
                'final_url': str(response.url), 'status_code': response.status_code,
                'content_type': response.headers.get('content-type')})
            return data
        except (httpx.HTTPError, ValueError) as error:
            detail = type(error).__name__ + ': ' + str(error)
            records.put(journal, {'request': request, 'error': detail})
            raise ValueError(detail) from error

    async def provider(self, client, name, query):
        cap = self.options['external_results_per_query']
        if name == 'searxng':
            url = self.options['searxng_url'].rstrip('/') + '/search'
            params = {'q': query, 'categories': 'images', 'format': 'json', 'pageno': 1, 'safesearch': 1}
            body = json.loads(await self.fetch(client, url, params=params, max_bytes=2_000_000))
            return [{'content_url': r['img_src'], 'landing_url': r.get('url'),
                     'source': 'searxng/' + str(r.get('engine', 'unknown')),
                     'license': r.get('license'), 'author': r.get('author'),
                     'description': r.get('content') or r.get('title') or '',
                     'origin': 'not_verified'} for r in body.get('results', [])[:cap]
                    if str(r.get('img_src', '')).startswith(('https://', 'http://'))]
        if name != 'commons':
            raise ValueError('Unsupported scene search provider: ' + name)
        params = {'action': 'query', 'format': 'json', 'generator': 'search', 'gsrsearch': query + ' filetype:bitmap',
                  'gsrnamespace': 6, 'gsrlimit': cap, 'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata'}
        thumbnail_width = self.options.get('commons_thumbnail_width', 0)
        if thumbnail_width:
            params['iiurlwidth'] = thumbnail_width
        body = json.loads(await self.fetch(client, self.options['commons_url'], params=params, max_bytes=4_000_000))
        if body.get('error'):
            raise ValueError('Commons search error: ' + _plain(body['error']))
        pages = (body.get('query') or {}).get('pages', {})
        result = []
        for page in sorted(pages.values(), key=lambda p: p.get('index', 0))[:cap]:
            info = (page.get('imageinfo') or [{}])[0]
            if not str(info.get('url', '')).startswith(('https://', 'http://')):
                continue
            if info.get('mime') and info['mime'] not in {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}:
                continue
            original_url = info['url']
            download_url = (info.get('thumburl') or original_url) if thumbnail_width else original_url
            parts = urlsplit(download_url)
            # Marketing parameters are not part of the image identity. Keep
            # the exact API URL too; all actual GETs remain in the HTTP journal.
            content_url = (urlunsplit(parts._replace(query=urlencode([
                (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if not k.lower().startswith('utm_')])))
                if parts.hostname in {'upload.wikimedia.org', 'thumb.wikimedia.org'} else download_url)
            ext = info.get('extmetadata') or {}
            def value(key):
                val = ext.get(key, {})
                return val.get('value') if isinstance(val, dict) else val
            result.append({'content_url': content_url, 'original_content_url': original_url,
                **({'download_variant': 'thumbnail' if download_url != original_url else 'original',
                    'requested_thumbnail_width': thumbnail_width,
                    'download_width': info.get('thumbwidth') if download_url != original_url else info.get('width'),
                    'download_height': info.get('thumbheight') if download_url != original_url else info.get('height')}
                   if thumbnail_width else {}),
                'landing_url': info.get('descriptionurl'),
                'source': 'wikimedia_commons', 'license': value('LicenseShortName'), 'author': value('Artist'),
                'description': value('ImageDescription') or page.get('title', ''), 'origin': 'not_verified',
                'provenance': {k: value(k) for k in ('DateTimeOriginal', 'Credit', 'Source', 'ObjectName', 'LicenseUrl') if value(k)}})
        return result

    async def external(self, row):
        binding = self.binding(row, 'external')
        cache, saved = self.cached(binding)
        if saved:
            return saved
        trace = {'queries': binding['queries'], 'query_fields': ['focus.search_queries'],
                 'providers': self.options['external_providers'], 'searches': [], 'excluded': [],
                 'reason': 'No usable local image, including local model rejection', 'downloads': 0}
        candidates, seen = [], set()
        async with httpx.AsyncClient(timeout=self.options['timeout_s'], follow_redirects=True,
                    trust_env=self.options['trust_env'], headers={'User-Agent': 'demiwtg-research/1.0 (image-source-review)'}) as client:
            for provider in self.options['external_providers']:
                queries = []
                for query in binding['queries'][:self.options['max_external_queries']]:
                    queries.append((query, query))
                    # Commons requires all ordinary query terms. A bounded
                    # prefix backoff offers candidates; the full scene
                    # requirements still go unchanged to the visual reviewer.
                    if provider == 'commons' and len(query.split()) > 3:
                        queries.append((query, ' '.join(query.split()[:3])))
                for author_query, query in queries:
                    if len(candidates) >= self.limit or trace['downloads'] >= self.options['max_external_downloads']:
                        break
                    query_trace = {'provider': provider, 'query': query, 'author_query': author_query,
                                   'query_mode': 'full' if query == author_query else 'prefix3',
                                   'provider_query': query + ' filetype:bitmap' if provider == 'commons' else query}
                    try:
                        results = await self.provider(client, provider, query)
                        trace['searches'].append({**query_trace, 'results': results})
                    except (ValueError, TypeError, KeyError) as error:
                        trace['searches'].append({**query_trace, 'error': str(error)})
                        continue
                    for hit in results:
                        if len(candidates) >= self.limit or trace['downloads'] >= self.options['max_external_downloads']:
                            break
                        if hit['content_url'] in seen:
                            continue
                        seen.add(hit['content_url'])
                        trace['downloads'] += 1
                        try:
                            data = await self.fetch(client, hit['content_url'])
                            sha = digest(data)
                            if not _partition(sha, row['branch']):
                                raise ValueError('Reserved for training targets')
                            with Image.open(io.BytesIO(data)) as im:
                                if im.format not in {'JPEG', 'PNG', 'WEBP', 'GIF'} or im.width * im.height > 40_000_000:
                                    raise ValueError('Unsupported image or pixel budget exceeded')
                                im.verify()
                                ext = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}[im.format]
                            ref = store_blob(self.run,data)
                            asset = self.accept_pixels({'path': None, 'blob_ref':ref.to_dict(), 'sha256':sha,
                                'source': {k: v for k, v in hit.items() if k not in {'description', 'origin'} and v},
                                'origin': hit['origin'], 'description': hit['description'],
                                'source_concepts': [], 'retrieval_query': query}, row['branch'])
                            if not any(duplicate(asset, a) for a in candidates):
                                candidates.append(asset)
                        except (ValueError, OSError) as error:
                            trace['excluded'].append({'url': hit['content_url'], 'reason': str(error)})
        result = {'candidates': candidates, 'trace': trace}
        run_records(self.run).put(cache, result)
        return result


class SearchLocalScenes:
    def __init__(self, search):
        self.search = search

    async def __call__(self, row):
        if row['status'] != 'needs_edit_source_search':
            return row
        result = await asyncio.to_thread(self.search.local, row)
        output = {**row, 'source_candidates': result['candidates'], 'edit_source_search': result['trace']}
        return ({**output, 'status': 'edit_candidates_ready'} if result['candidates'] else
                fail(output, 'needs_edit_source', 'No eligible local scene candidate within declared search budget'))


class SearchExternalScenes:
    def __init__(self, search):
        self.search = search

    async def __call__(self, row):
        if row['status'] != 'needs_edit_source':
            return row
        if not self.search.options['external_providers']:
            return {**row, 'external_source_search': {'status': 'disabled', 'reason': 'External providers disabled in run config'}}
        result = await self.search.external(row)
        output = {**row, 'local_source_candidates': row.get('source_candidates', []),
                  'source_candidates': result['candidates'], 'external_source_search': result['trace']}
        return ({**output, 'status': 'external_edit_candidates_ready'} if result['candidates'] else
                fail(output, 'needs_edit_source', 'External search found no eligible scene within declared budget; see provider errors/exclusions'))
