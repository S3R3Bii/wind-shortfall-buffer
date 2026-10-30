"""Capture declared public exports once; preserve every response before parsing."""
import argparse,hashlib,json,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
FIELDS=['datetime','resolutioncode','offshoreonshore','region','gridconnectiontype','measured','dayaheadforecast','monitoredcapacity','decrementalbidid']
BASE='https://opendata.elia.be/api/explore/v2.1/catalog/datasets/ods031'
def main():
 p=argparse.ArgumentParser();p.add_argument('private_dir',type=Path);a=p.parse_args();a.private_dir.mkdir(parents=True,exist_ok=False)
 commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 assert not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),'Commit and audit capture protocol first'
 receipts=[]
 def get(name,url,params=None):
  start=datetime.now(timezone.utc).isoformat()
  try:
   r=requests.get(url,params=params,timeout=45)
   data=r.content;(a.private_dir/name).write_bytes(data)
   record=dict(file=name,url=r.url,start_utc=start,end_utc=datetime.now(timezone.utc).isoformat(),status=r.status_code,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),headers={k:r.headers.get(k) for k in ['Content-Type','ETag','Last-Modified']},protocol_commit=commit)
   receipts.append(record);(a.private_dir/'requests.json').write_bytes((json.dumps(receipts,indent=2)+'\n').encode())
   print(json.dumps({k:record[k] for k in ['file','status','bytes','sha256']}),flush=True)
   assert r.status_code==200,('Source access failed;preserve,do not bypass',name,r.status_code)
   return r
  except requests.RequestException as error:
   receipts.append(dict(file=name,url=url,start_utc=start,end_utc=datetime.now(timezone.utc).isoformat(),error=type(error).__name__,protocol_commit=commit));(a.private_dir/'requests.json').write_bytes((json.dumps(receipts,indent=2)+'\n').encode());raise
 before=get('metadata-before.json',BASE).json()
 for year in range(2018,2024):
  for region in ['Flanders','Wallonia']:
   params=dict(select=','.join(FIELDS),where=f"offshoreonshore='Onshore' AND gridconnectiontype='Dso' AND region='{region}' AND datetime >= date'{year}-01-01T00:00:00Z' AND datetime < date'{year+1}-01-01T00:00:00Z'",order_by='datetime,region,gridconnectiontype,offshoreonshore',timezone='UTC',use_labels='false')
   for fmt in ['csv','json']:
    get(f'{year}-{region}.{fmt}',BASE+'/exports/'+fmt,params);time.sleep(.2)
 after=get('metadata-after.json',BASE).json()
 markers=['data_processed','metadata_processed','records_count']
 assert all(before['metas']['default'][k]==after['metas']['default'][k] for k in markers),'Dataset changed during capture;preserve and stop before models'
 print('SOURCE_BATCH_CAPTURED_NO_MODEL_SCORES')
if __name__=='__main__':main()
