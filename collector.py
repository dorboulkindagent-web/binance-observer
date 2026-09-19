import json,urllib.request,urllib.parse,datetime
from pathlib import Path
symbols=["BTCUSDT","ETHUSDT"]
def ema(a,n):
 k=2/(n+1);v=a[0]
 for x in a[1:]:v=x*k+v*(1-k)
 return v
out={"updated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"mode":"OBSERVE_ONLY","execution":"NOT_IMPLEMENTED","symbols":{}}
for s in symbols:
 u="https://api.binance.com/api/v3/klines?"+urllib.parse.urlencode({"symbol":s,"interval":"5m","limit":60})
 with urllib.request.urlopen(u,timeout=15) as r: rows=json.load(r)
 now=int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000); rows=[x for x in rows if int(x[6])<now]; c=[float(x[4]) for x in rows]
 out["symbols"][s]={"price":c[-1],"ema9":round(ema(c[-30:],9),4),"ema21":round(ema(c[-40:],21),4),"candle_close":int(rows[-1][6])}
Path("docs").mkdir(exist_ok=True);Path("docs/data.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
p=Path("docs/history.jsonl");old=p.read_text() if p.exists() else "";p.write_text("\n".join((old+json.dumps(out,ensure_ascii=False)+"\n").splitlines()[-2016:])+"\n")
