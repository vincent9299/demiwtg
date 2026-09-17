"""Freeze pre-filter images and contact sheets for a local model comparison."""
from pathlib import Path
import json,math
from curation.v4.ops.multimodal import BatchImageSelection
from curation.v4.contracts import digest,immutable
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image,ImageOps
import argparse
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--run',type=Path,required=True);args=p.parse_args()
run=args.run;source=args.source
if run.exists():raise FileExistsError('Use a new experiment directory')
run.mkdir(parents=True)
inputs=[];inventory=[]
definitions={'OK手势':'拇指和食指相触成环、其余手指伸展或放松的手势；相关图解和实际使用场景也可保留。','玻璃棒':'实验室中用于搅拌、引流等的实心玻璃棒；同属实验器材不自动属于目标。','白花芍药':'植物学物种 Paeonia sterniana；泛指白色芍药花或其他栽培品种不自动认证为这一物种。身份不确定时保留不确定性。'}
for ci,line in enumerate(source.read_text().splitlines()):
 row=json.loads(line);concept=row['identity']['target_label']
 for batch in BatchImageSelection(4)(row):
  batch['image_prompt'].pop('metadata',None)
  batch['image_prompt']['identity_context']={'definition':definitions[concept]}
  inputs.append(batch)
 for i,m in enumerate(row['available_images']):inventory.append({'sample_id':f'{ci}-{i:02d}','concept':concept,'image_id':m['image_id'],'path':m['bytes']['path'],'sha256':m['record']['sha256']})
 ims=[x for x in inventory if x['concept']==concept]
 for start in range(0,len(ims),12):
  fig,axes=plt.subplots(3,4,figsize=(16,12))
  for ax in axes.flat:ax.axis('off')
  for ax,x in zip(axes.flat,ims[start:start+12]):
   with Image.open(x['path']) as im:ax.imshow(ImageOps.exif_transpose(im).convert('RGB'))
   ax.set_title(x['sample_id']+' '+x['image_id'],fontsize=10)
  fig.tight_layout();fig.savefig(run/f'contact_{ci}_{start//12}.png',dpi=120);plt.close(fig)
(run/'inputs.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in inputs))
(run/'inventory.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in inventory))
immutable(run/'input_manifest.json',{'source':str(source),'sha256':digest(source.read_bytes()),'definitions':definitions,'images':len(inventory),'batches':len(inputs),'policy':'All frozen preselection usable images; same pixels and prompt across models; no earlier model decisions/annotations/captions supplied'})
print(len(inputs),len(inventory))
