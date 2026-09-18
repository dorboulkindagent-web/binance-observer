#!/usr/bin/env python3
import os,json,time,threading,urllib.request,urllib.parse,sqlite3,hashlib,secrets
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

PORT=int(os.getenv("PORT","8000")); DB=os.getenv("DB_PATH","/tmp/observer.sqlite3")
SYMBOLS=["BTCUSDT","ETHUSDT"]; INTERVAL="1m"
Path(DB).parent.mkdir(parents=True,exist_ok=True)
db=sqlite3.connect(DB,check_same_thread=False); lock=threading.RLock()
db.execute("""create table if not exists ticks(ts integer,symbol text,price real,signal text,reason text,primary key(ts,symbol))"""); db.execute("""create table if not exists events(ts integer,level text,message text)"""); db.commit()
state={"running":True,"last_update":0,"error":None,"prices":{}}
def ema(xs,n):
    if not xs:return 0
    a=2/(n+1); v=xs[0]
    for x in xs[1:]:v=x*a+v*(1-a)
    return v
def fetch(symbol):
    u="https://api.binance.com/api/v3/klines?"+urllib.parse.urlencode({"symbol":symbol,"interval":INTERVAL,"limit":80})
    with urllib.request.urlopen(u,timeout=10) as r:return json.load(r)
def worker():
    while True:
      if state["running"]:
       try:
        now=int(time.time()*1000)
        for s in SYMBOLS:
          rows=fetch(s); closed=[r for r in rows if int(r[6])<now]; closes=[float(r[4]) for r in closed]
          if len(closes)<30:continue
          fast,slow=ema(closes[-30:],9),ema(closes[-40:],21)
          sig="מעקב"; reason=f"EMA9={fast:.2f}, EMA21={slow:.2f} — תצפית בלבד"
          price=closes[-1]; ts=int(closed[-1][6])
          with lock:
           db.execute("insert or ignore into ticks values(?,?,?,?,?)",(ts,s,price,sig,reason)); db.commit()
          state["prices"][s]=price
        state["last_update"]=int(time.time()); state["error"]=None
       except Exception as e:
        state["error"]=type(e).__name__
        with lock: db.execute("insert into events values(?,?,?)",(int(time.time()),"ERROR",type(e).__name__)); db.commit()
      time.sleep(15)
threading.Thread(target=worker,daemon=True).start()

HTML=r'''<!doctype html><html dir="rtl" lang="he"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Binance Observer</title><style>
body{font-family:system-ui;background:#0b1020;color:#eef2ff;margin:0}.wrap{max-width:900px;margin:auto;padding:20px}.top,.card{background:#151c31;border:1px solid #29334f;border-radius:18px;padding:18px;margin:12px 0}.top{display:flex;justify-content:space-between;gap:12px;align-items:center}.badge{background:#123d32;color:#7fffc9;padding:7px 12px;border-radius:99px}.warn{background:#3c2d12;color:#ffd982;padding:12px;border-radius:12px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.price{font-size:30px;font-weight:800}button{border:0;border-radius:10px;padding:10px 16px;font-weight:700}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #29334f;text-align:right}@media(max-width:600px){.grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.price{font-size:25px}}
</style><div class="wrap"><div class="top"><div><h1>Binance Observer</h1><div>ניטור ותיעוד בלבד — אין קנייה, מכירה או גישה לחשבון</div></div><span class="badge">OBSERVE ONLY</span></div>
<div class="warn">המערכת קוראת נתוני שוק ציבוריים בלבד. אין בה API לביצוע עסקאות.</div><div id="status" class="card">טוען…</div><div id="cards" class="grid"></div><div class="card"><h2>תיעוד אחרון</h2><table><thead><tr><th>זמן</th><th>זוג</th><th>מחיר</th><th>החלטה</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<script>
async function refresh(){let r=await fetch('/api/state');let x=await r.json();document.getElementById('status').innerHTML='<b>מנוע:</b> '+(x.running?'פעיל':'מושהה')+' · <b>עדכון:</b> '+(x.last_update?new Date(x.last_update*1000).toLocaleString('he-IL'):'ממתין')+(x.error?' · שגיאת חיבור: '+x.error:'');document.getElementById('cards').innerHTML=Object.entries(x.prices).map(([s,p])=>'<div class="card"><b>'+s+'</b><div class="price">'+Number(p).toLocaleString()+'</div><small>נר סגור · Binance Spot</small></div>').join('');let q=await fetch('/api/log');let d=await q.json();document.getElementById('rows').innerHTML=d.rows.map(a=>'<tr><td>'+new Date(a[0]).toLocaleString('he-IL')+'</td><td>'+a[1]+'</td><td>'+Number(a[2]).toLocaleString()+'</td><td>'+a[3]+'</td></tr>').join('')}refresh();setInterval(refresh,15000)
</script></html>'''
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def hdr(self,c=200,k="application/json; charset=utf-8"):
  self.send_response(c);self.send_header("Content-Type",k);self.send_header("Cache-Control","no-store");self.send_header("X-Content-Type-Options","nosniff");self.send_header("X-Frame-Options","DENY");self.end_headers()
 def do_GET(self):
  if self.path=="/":
   self.hdr(200,"text/html; charset=utf-8");self.wfile.write(HTML.encode());return
  if self.path=="/api/health":
   self.hdr();self.wfile.write(json.dumps({"ok":True,"execution":"NOT_IMPLEMENTED"}).encode());return
  if self.path=="/api/state":
   self.hdr();self.wfile.write(json.dumps(state,ensure_ascii=False).encode());return
  if self.path=="/api/log":
   with lock: rows=db.execute("select ts,symbol,price,signal from ticks order by ts desc limit 50").fetchall()
   self.hdr();self.wfile.write(json.dumps({"rows":rows},ensure_ascii=False).encode());return
  self.hdr(404);self.wfile.write(b'{"error":"not found"}')
 def do_POST(self): self.hdr(405);self.wfile.write(b'{"error":"observe only"}')
 do_PUT=do_POST;do_DELETE=do_POST;do_PATCH=do_POST
ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
