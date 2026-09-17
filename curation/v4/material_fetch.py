"""Read-only remote byte acquisition into a run cache; never writes the dataset."""
import asyncio
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from .contracts import digest
from .ops.operators import verify_image

async def fetch_image(record,run,config):
    relative=record.get('path','');parts=Path(relative).parts;expected=record.get('sha256','')
    if not parts or parts[0]!='blobs' or '..' in parts or len(expected)!=64:
        return {'status':'invalid_locator'}
    root=Path(run)/'material_cache';dest=root/relative
    if dest.exists():return verify_image(record,root,[])
    base=config.get('cos_base_url')
    if not base:return {'status':'remote_not_configured','note':'Remote material remains pending, not absent.'}
    u=urlsplit(base)
    if u.scheme not in ('http','https') or u.username or u.password or u.query or u.fragment:
        raise ValueError('COS base must be a plain HTTP(S) endpoint without embedded credentials')
    url=base.rstrip('/')+'/'+relative
    try:
        async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=25) as client:
            async with client.stream('GET',url) as response:
                if response.status_code!=200:return {'status':'remote_error','http_status':response.status_code,'url':url}
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>config['max_image_bytes']:return {'status':'byte_budget_exceeded','url':url}
        if digest(bytes(data))!=expected:return {'status':'hash_mismatch','url':url,'actual_sha256':digest(bytes(data))}
        dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix(dest.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(dest)
        result=await asyncio.to_thread(verify_image,record,root,[])
        return {**result,'acquisition_url':url}
    except httpx.HTTPError as e:return {'status':'remote_error','url':url,'error':type(e).__name__}
