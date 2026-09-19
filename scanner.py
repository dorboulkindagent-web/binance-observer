"""Binance Spot market scanner. Public data only; never authenticates or trades."""
import json, math, time, urllib.request, urllib.parse, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
BASE="https://data-api.binance.vision"; QUOTES=("USDT",); STABLE={"USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT"}
def get(path,params=None):
 u=BASE+path+("?" + urllib.parse.urlencode(params) if params else "")
 req=urllib.request.Request(u,headers={"User-Agent":"BinanceMarketScanner/2.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
def ema(a,n):
 k=2/(n+1);v=a[0]
 for x in a[1:]:v=x*k+v*(1-k)
 return v
def rsi(a,n=14):
 gains=[];loss=[]
 for x,y in zip(a[-n-1:-1],a[-n:]):
  d=y-x;gains.append(max(d,0));loss.append(max(-d,0))
 ag=sum(gains)/n;al=sum(loss)/n
 return 100 if al==0 else 100-100/(1+ag/al)
def atr(rows,n=14):
 vals=[]
 for p,c in zip(rows[-n-1:-1],rows[-n:]):
  h=float(c[2]);l=float(c[3]);pc=float(p[4]);vals.append(max(h-l,abs(h-pc),abs(l-pc)))
 return sum(vals)/len(vals)
def analyze(symbol,rows,quote_vol):
 now=int(time.time()*1000); rows=[r for r in rows if int(r[6])<now]
 if len(rows)<55:return None
 c=[float(r[4]) for r in rows];v=[float(r[5]) for r in rows];p=c[-1]
 e9=ema(c[-30:],9);e21=ema(c[-45:],21);rv=rsi(c);a=atr(rows);ap=a/p*100
 hi=max(float(r[2]) for r in rows[-21:-1]);lo=min(float(r[3]) for r in rows[-21:-1])
 vd=v[-1]/(sum(v[-21:-1])/20 or 1); up=(hi-p)/p*100; down=(p-lo)/p*100
 macd=ema(c[-40:],12)-ema(c[-40:],26); prev=ema(c[-41:-1],12)-ema(c[-41:-1],26)
 bull=0;bear=0;why=[]
 if up<=max(.8,ap*1.2):bull+=25;why.append("קרוב להתנגדות")
 if down<=max(.8,ap*1.2):bear+=25
 if e9>e21:bull+=20
 else:bear+=20
 if macd>prev:bull+=15
 else:bear+=15
 if vd>=1.5: bull+=20 if c[-1]>=c[-2] else 0; bear+=20 if c[-1]<c[-2] else 0; why.append("נפח מוגבר")
 if 52<=rv<=72:bull+=15
 if 28<=rv<=48:bear+=15
 if p>hi:bull+=20;why.append("פריצה")
 if p<lo:bear+=20;why.append("שבירה")
 score=max(bull,bear); kind="BREAKOUT_SETUP" if bull>=bear else "BREAKDOWN_RISK"
 return {"symbol":symbol,"price":p,"score":min(score,100),"kind":kind,"rsi":round(rv,1),"ema9":round(e9,8),"ema21":round(e21,8),"atr_pct":round(ap,2),"volume_ratio":round(vd,2),"resistance":hi,"support":lo,"distance_resistance_pct":round(up,2),"distance_support_pct":round(down,2),"quote_volume_24h":round(quote_vol,2),"candle_close":int(rows[-1][6]),"reasons":why}
def main():
 info=get("/api/v3/exchangeInfo"); ticks=get("/api/v3/ticker/24hr")
 tv={x["symbol"]:float(x.get("quoteVolume",0)) for x in ticks}
 syms=[x["symbol"] for x in info["symbols"] if x.get("status")=="TRADING" and x.get("quoteAsset") in QUOTES and x.get("isSpotTradingAllowed",True) and x["symbol"] not in STABLE]
 # Free-tier budget: scan all eligible symbols for liquidity, then technical candles for top 120 liquid markets.
 syms.sort(key=lambda s:tv.get(s,0),reverse=True); candidates=syms[:120]; out=[];errors=0
 def scan_one(s):
  rows=get("/api/v3/klines",{"symbol":s,"interval":"15m","limit":60})
  return analyze(s,rows,tv.get(s,0))
 with ThreadPoolExecutor(max_workers=12) as pool:
  futures={pool.submit(scan_one,s):s for s in candidates}
  for future in as_completed(futures):
   try:
    a=future.result()
    if a:out.append(a)
   except Exception:
    errors+=1
 out.sort(key=lambda x:(x["score"],x["quote_volume_24h"]),reverse=True)
 stamp=dt.datetime.now(dt.timezone.utc).isoformat()
 snap={"updated_at":stamp,"mode":"OBSERVE_ONLY","execution":"NOT_IMPLEMENTED","universe_count":len(syms),"analyzed_count":len(out),"candidate_limit":120,"interval":"15m","errors":errors,"alerts":[x for x in out if x["score"]>=55][:40],"leaders":out[:80]}
 Path("docs").mkdir(exist_ok=True);Path("docs/scanner.json").write_text(json.dumps(snap,ensure_ascii=False,indent=2)+"\n")
 h=Path("docs/alerts-history.jsonl");old=h.read_text().splitlines() if h.exists() else [];old.append(json.dumps({"updated_at":stamp,"alerts":snap["alerts"]},ensure_ascii=False));h.write_text("\n".join(old[-1008:])+"\n")
if __name__=="__main__":main()
