"""Independent object evidence preparation. Does not mutate dataset truth or call models."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
import requests,json,hashlib,re
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=_ARCHIVE_ROOT
OUT=ROOT/'state/curation/knowledge_application_v1/version3_20/candidates_objects'
OUT.mkdir(parents=True,exist_ok=True)
HEAD={'User-Agent':'KnowledgeCurationResearch/1.0 (academic source verification)'}
SPECS=[('dovetail','Dovetail_joint','Finished_dovetail.jpg'),('clinker','Clinker_(boat_building)','Clinker_diagram_1937_Adm_manual_seamanship.jpg'),('splitpin','Split_pin','CotterPins.svg'),('buttress','Flying_buttress','Lübeck_Marienkirche_Strebebögen.jpg'),('hurdygurdy','Hurdy-gurdy','WheelTangents.jpg'),('shishiodoshi','Shishi-odoshi','Higashiyama_Botanical_Garden_Shishiodoshi_20170617.gif')]
def fetch():
 from urllib.parse import unquote
 for key,title,filename in SPECS:
  d=OUT/key;d.mkdir(exist_ok=True)
  if (d/'source_metadata.json').exists(): continue
  url='https://en.wikipedia.org/wiki/'+title;r=requests.get(url,headers=HEAD);r.raise_for_status();(d/'source.html').write_text(r.text)
  s=BeautifulSoup(r.text,'html.parser');(d/'source.txt').write_text(s.get_text(' ',strip=True))
  im=next(i for i in s.select('img') if filename in unquote(i.get('src','')))
  src='https:'+im['src'].split('?')[0]
  if '/thumb/' in src: src=src.replace('thumb.wikimedia.org','upload.wikimedia.org').replace('/thumb/','/').rsplit('/',1)[0]
  rr=requests.get(src,headers=HEAD)
  if rr.status_code!=200 or filename.endswith('.svg'):
   src='https:'+im['src'].split('?')[0]; src=re.sub(r'/[0-9]+px-', '/960px-',src);rr=requests.get(src,headers=HEAD)
  rr.raise_for_status();p=d/(filename+'.png' if filename.endswith('.svg') else filename);p.write_bytes(rr.content)
  meta={'url':url,'retrieved_at':'2026-09-13','revision_url':next((a.get('href') for a in s.select('a') if 'oldid=' in a.get('href','')),None),'image_url':src,'image_path':str(p),'image_sha256':hashlib.sha256(rr.content).hexdigest(),'caption':im.find_parent('figure').get_text(' ',strip=True) if im.find_parent('figure') else '', 'snapshot_path':str(d/'source.html')}
  (d/'source_metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(key,meta)
if __name__=='__main__':fetch()
