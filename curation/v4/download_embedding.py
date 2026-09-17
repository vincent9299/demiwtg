"""Download a pinned public embedding model, verify LFS hashes, record provenance."""
import argparse,hashlib,json,time
from pathlib import Path
import requests


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',default='Qwen/Qwen3-Embedding-0.6B');p.add_argument('--directory',type=Path,required=True);p.add_argument('--record',type=Path,required=True);p.add_argument('--proxy');a=p.parse_args()
    s=requests.Session();s.trust_env=False
    if a.proxy:s.proxies={'http':a.proxy,'https':a.proxy}
    response=s.get('https://huggingface.co/api/models/'+a.model,params={'blobs':'true'},timeout=45);response.raise_for_status();meta=response.json();revision=meta['sha']
    a.directory.mkdir(parents=True,exist_ok=True);a.record.mkdir(parents=True,exist_ok=True)
    (a.record/'upstream_metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    names={'config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','vocab.json','merges.txt','model.safetensors','README.md','LICENSE','preprocessor_config.json','processor_config.json','tokenizer.model','spiece.model'}
    files=[]
    for item in meta['siblings']:
        name=item['rfilename']
        if name not in names:continue
        target=a.directory/name;expected=(item.get('lfs') or {}).get('sha256')
        if target.exists():
            actual=hashlib.sha256(target.read_bytes()).hexdigest()
            if expected and actual!=expected:raise ValueError('Existing model file hash mismatch: '+name)
            files.append({'file':name,'sha256':actual,'bytes':target.stat().st_size,'reused':True});continue
        url=f'https://huggingface.co/{a.model}/resolve/{revision}/{name}'
        print('downloading',name,flush=True);temp=target.with_suffix(target.suffix+'.partial')
        if temp.exists():raise ValueError('Incomplete download exists: '+str(temp))
        h=hashlib.sha256();n=0;next_notice=128*1024*1024
        with s.get(url,stream=True,timeout=(30,120)) as r:
            r.raise_for_status()
            with temp.open('wb') as out:
                for chunk in r.iter_content(1024*1024):
                    out.write(chunk);h.update(chunk);n+=len(chunk)
                    if n>=next_notice:print(name,n,'bytes',flush=True);next_notice+=128*1024*1024
        if expected and h.hexdigest()!=expected:raise ValueError('Download SHA mismatch: '+name)
        temp.replace(target);files.append({'file':name,'sha256':h.hexdigest(),'bytes':n,'upstream_sha256':expected,'url':url})
    record={'model':a.model,'revision':revision,'files':files,'downloaded_at_unix':time.time()}
    (a.record/'download.json').write_text(json.dumps(record,indent=2)+'\n')
    print('complete',revision,flush=True)

if __name__=='__main__':main()
