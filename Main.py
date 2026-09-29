import os,time,json,threading,requests
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo

BOT=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip()
ET=ZoneInfo('America/New_York')
last_alert={}
s=requests.Session();s.headers['User-Agent']='Mozilla/5.0'

POS=('approval','approved','fda','contract','partnership','agreement','acquisition','merger','clinical','trial','results','positive','revenue','earnings','guidance','deal','order','award','patent','compliance','uplisting','strategic','launch')
NEG=('bankruptcy','delisting','lawsuit','investigation','going concern','default','termination','reverse split')

def get(url,params=None,timeout=15):
    try:
        r=s.get(url,params=params,timeout=timeout); r.raise_for_status(); return r.json()
    except Exception as e: print('HTTP',e); return None

def tg(method,payload=None):
    try:
        r=s.post(f'https://api.telegram.org/bot{BOT}/{method}',json=payload or {},timeout=25)
        if r.status_code==409: print('TELEGRAM 409 Conflict - another polling instance is active')
        elif r.status_code!=200: print('TELEGRAM ERROR',r.status_code,r.text[:200])
        return r.json() if r.status_code==200 else None
    except Exception as e: print('TELEGRAM',e); return None

def send(text,chat=None):
    return bool(tg('sendMessage',{'chat_id':chat or CHAT,'text':text,'disable_web_page_preview':True}))

def universe():
    d=get('https://api.nasdaq.com/api/screener/stocks',{'tableonly':'true','limit':'1000','offset':'0','download':'true'})
    try:
        rows=d['data']['rows']
        out={}
        for x in rows:
            sym=str(x.get('symbol','')).upper().strip()
            if sym and sym.isalpha():
                out[sym]=x
        return list(out.values())
    except:
        return []

def chart(sym):
    d=get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}',{'range':'3mo','interval':'1d','events':'history'})
    try:
        q=d['chart']['result'][0]['indicators']['quote'][0]
        out=[]
        for i,c in enumerate(q['close']):
            if c is not None: out.append({'c':float(c),'h':float(q['high'][i] or c),'l':float(q['low'][i] or c),'v':int(q['volume'][i] or 0)})
        return out
    except: return []

def quote(sym):
    d=get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}',{'range':'1d','interval':'1m','includePrePost':'true'})
    try:
        m=d['chart']['result'][0]['meta']; p=float(m.get('regularMarketPrice') or m['chartPreviousClose']); prev=float(m.get('previousClose') or m['chartPreviousClose']); v=int(m.get('regularMarketVolume') or 0)
        return p,v,((p-prev)/prev*100 if prev else 0)
    except: return None

def rsi(a,n=14):
    if len(a)<=n:return None
    g=[];l=[]
    for i in range(1,len(a)):
        d=a[i]-a[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g[:n])/n;al=sum(l[:n])/n
    for i in range(n,len(g)): ag=(ag*(n-1)+g[i])/n;al=(al*(n-1)+l[i])/n
    return 100 if al==0 else 100-100/(1+ag/al)

def news(sym):
    d=get('https://query2.finance.yahoo.com/v1/finance/search',{'q':sym,'quotesCount':3,'newsCount':10})
    if not d:return None
    cut=datetime.now(timezone.utc)-timedelta(days=3);best=None
    for x in d.get('news',[]):
        title=x.get('title',''); low=title.lower(); hits=sum(w in low for w in POS); bad=sum(w in low for w in NEG)
        if not title or hits<=bad:continue
        ts=x.get('providerPublishTime')
        if ts:
            try:
                if datetime.fromtimestamp(int(ts),timezone.utc)<cut:continue
            except:pass
        if best is None or hits>best[0]:best=(hits,title,x.get('link',''))
    return best

def nval(v):
    try:return float(str(v).replace('$','').replace(',','').replace('%','').strip())
    except:return 0.0

def analyze(sym,row=None):
    q=quote(sym)
    row=row or {}
    p=nval(row.get('lastsale')) or (q[0] if q else 0)
    v=nval(row.get('volume')) or (q[1] if q else 0)
    ch=nval(row.get('pctchange')) if row.get('pctchange') is not None else (q[2] if q else 0)
    # Nasdaq screener values are used first because Yahoo regular volume
    # can be zero during premarket.
    if p<=0 or p>=5 or v<50000 or abs(ch)<1:return None
    rows=chart(sym)
    if len(rows)<25:return None
    c=[x['c'] for x in rows];vol=[x['v'] for x in rows]
    rv=(sum(vol[-5:])/5)/(sum(vol[-20:])/20 or 1)
    if rv<0.9:return None
    r=rsi(c)
    if r is None or r>88:return None
    n=news(sym)
    if not n:return None
    lo=min(x['l'] for x in rows[-20:]);hi=max(x['h'] for x in rows[-20:]);d=hi-lo
    fib=[lo+d*.618,lo+d,lo+d*1.272,lo+d*1.618]
    targets=[x for x in fib if x>p*1.02][:3] or [p*1.08,p*1.15,p*1.25]
    score=(2 if ch>2 else 0)+(2 if rv>=1.2 else 1)+(2 if 45<=r<=75 else 1)+min(3,n[0])
    return {'sym':sym,'p':p,'v':v,'ch':ch,'rv':rv,'r':r,'targets':targets,'news':n,'score':score}

def fmt(x):return f'{x:.2f}' if x>=1 else f'{x:.4f}'
def vol(v):return f'{v/1e6:.1f}M' if v>=1e6 else f'{v/1e3:.0f}K'

def message(a):
    e1=a['p']*.985;e2=a['p']*1.015;sl=a['p']*.93
    t='\n'.join(fmt(x) for x in a['targets'])
    return f"🚨 {a['sym']}\n${fmt(a['p'])}\nVOL {vol(a['v'])}\nRV {a['rv']:.1f}x\nRSI {a['r']:.0f}\nE {fmt(e1)}–{fmt(e2)}\nSL {fmt(sl)}\nFIB\n{t}\nTP\n{t}\n📰 NEWS\n{a['news'][1]}\n{a['news'][2]}"

def scan(chat=None):
    print('Scanning U.S. penny stocks...'); u=universe();print('Universe:',len(u)); cs=[]
    for row in u:
        sym=str(row.get('symbol','')).upper()
        try:
            a=analyze(sym,row)
            if a:cs.append(a);print('Candidate:',sym)
        except Exception as e:print('ANALYZE',sym,e)
        if len(cs)>=10:break
    cs.sort(key=lambda x:(x['score'],x['rv'],abs(x['ch'])),reverse=True);print('Candidates:',len(cs))
    for a in cs[:5]:
        old=last_alert.get(a['sym'])
        if old and datetime.now(timezone.utc)-old<timedelta(hours=12):continue
        if send(message(a),chat):last_alert[a['sym']]=datetime.now(timezone.utc)

def telegram_loop():
    global CHAT
    if not BOT:print('ERROR: TELEGRAM_BOT_TOKEN missing');return
    tg('deleteWebhook',{'drop_pending_updates':False});off=None
    while True:
        try:
            p={'timeout':25,'allowed_updates':['message']}
            if off is not None:p['offset']=off
            d=tg('getUpdates',p)
            for u in (d or {}).get('result',[]):
                off=u['update_id']+1;m=u.get('message',{});cid=m.get('chat',{}).get('id');txt=(m.get('text') or '').strip()
                if not cid:continue
                if txt.startswith('/start'):CHAT=str(cid);send('✅ Connected. Penny Stock Scanner is active.',cid)
                elif txt.startswith('/scan'):send('🔎 Scanning U.S. penny stocks now...',cid);threading.Thread(target=scan,args=(cid,),daemon=True).start()
                elif txt.startswith('/status'):send('✅ Scanner online\nPrice < $5\nVolume ≥ 50K\nMove ≥ 1%\nRecent positive catalyst required',cid)
        except Exception as e:print('POLL',e);time.sleep(8)

def market():
    n=datetime.now(ET);return n.weekday()<5 and 4<=n.hour+(n.minute/60)<=16

def main():
    print('🚀 Penny Stock Scanner started');print('Data source: Nasdaq screener + Yahoo Finance');print('Relaxed filters: price<$5, volume>=50K, move>=1%, RV>=0.9x')
    threading.Thread(target=telegram_loop,daemon=True).start()
    while True:
        if market():scan()
        else:print('Outside U.S. premarket/market window.')
        time.sleep(300)

if __name__=='__main__':main()
