"""Paper trading simulator for Binance Radar. No authenticated APIs; no real orders."""
import json, datetime as dt
from pathlib import Path
START_CASH=10000.0; RISK_FRACTION=0.02; MAX_POSITIONS=5
def load(p,default):
 try:return json.loads(Path(p).read_text())
 except Exception:return default
def main():
 scan=load("docs/scanner.json",{}); state=load("docs/paper.json",{"cash":START_CASH,"positions":{},"closed":[],"equity":START_CASH})
 alerts={a["symbol"]:a for a in scan.get("alerts",[])}; leaders={a["symbol"]:a for a in scan.get("leaders",[])}
 # Exit paper positions at invalidation or +2 ATR move. Simulation only.
 for s,pos in list(state["positions"].items()):
  a=leaders.get(s)
  if not a:continue
  p=a["price"]; entry=pos["entry"]; atr=pos["atr_pct"]/100*entry
  exit_reason=None
  if p<=pos["stop"]:exit_reason="STOP"
  elif p>=pos["target"]:exit_reason="TARGET"
  if exit_reason:
   pnl=(p-entry)*pos["qty"];state["cash"]+=p*pos["qty"];pos.update({"exit":p,"pnl":round(pnl,2),"exit_reason":exit_reason,"closed_at":scan.get("updated_at")});state["closed"].append(pos);del state["positions"][s]
 # Open only high-score bullish setups with 1h/4h agreement.
 slots=MAX_POSITIONS-len(state["positions"])
 for a in sorted(alerts.values(),key=lambda x:x.get("score",0),reverse=True):
  if slots<=0:break
  s=a["symbol"];ctx=a.get("trend_context",{})
  if s in state["positions"] or a.get("kind")!="BREAKOUT_SETUP" or a.get("score",0)<80 or ctx.get("1h")!="UP" or ctx.get("4h")!="UP":continue
  entry=a["price"]; stop=min(a["support"],entry*(1-max(a["atr_pct"]/100*1.5,.01))); risk=max(entry-stop,entry*.005)
  risk_cash=max(state["cash"]*RISK_FRACTION,0);qty=min(risk_cash/risk,(state["cash"]/max(slots,1))/entry)
  cost=qty*entry
  if qty<=0 or cost>state["cash"]:continue
  target=entry+2*risk;state["cash"]-=cost;state["positions"][s]={"symbol":s,"entry":entry,"qty":qty,"stop":stop,"target":target,"atr_pct":a["atr_pct"],"opened_at":scan.get("updated_at"),"mode":"PAPER"};slots-=1
 equity=state["cash"]
 for s,pos in state["positions"].items():equity+=leaders.get(s,{"price":pos["entry"]})["price"]*pos["qty"]
 state["equity"]=round(equity,2);state["return_pct"]=round((equity/START_CASH-1)*100,2);state["updated_at"]=dt.datetime.now(dt.timezone.utc).isoformat();state["mode"]="PAPER_ONLY";state["real_execution"]="DISABLED";state["closed"]=state["closed"][-500:]
 Path("docs/paper.json").write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__":main()
