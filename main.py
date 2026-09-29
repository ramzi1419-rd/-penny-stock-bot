import os,time,json,urllib.parse,urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID=os.getenv('TELEGRAM_CHAT_ID')
if not BOT_TOKEN: raise RuntimeError('Missing TELEGRAM_BOT_TOKEN')
TG=f'https://api.telegram.org/bot{BOT_TOKEN}'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36'
NQ='https://api.nasdaq.com/api/screener/stocks'
YC='https://query1.finance.yahoo.com/v8/finance/chart/'
YS='https://query1.finance.yahoo.com/v1/finance/search'
POS={'approval','approved','fda','contract','partnership','agreement','acquisition','merger','clinical','trial','results','positive','launch','orders','revenue','patent','license','milestone','expansion','award','government','strategic','wins','won','selected','collaboration','authorization','clearance','phase','breakthrough','raises','raised'}
NEG={'bankruptcy','delisting','investigation','lawsuit','fraud','default','offering','dilution','layoff','restatement','warning','downgrade'}
LAST={}

def req(url,params=None,timeout=20):
    if params: url+='?'+urllib.parse.urlencode(params)
    r=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Referer':'https://www.nasdaq.com/'})
    with urllib.request.urlopen(r,timeout=timeout) as x:return json.loads(x.read().decode())

def tg(method,data=None,timeout=12):
    r=urllib.request.Request(TG+'/'+method,data=urllib.parse.urlencode(data or {}).encode(),headers={'User-Agent':UA})
    with urllib.request.urlopen(r,timeout=timeout) as x:return json.loads(x.read().decode())

def send(text):
    if CHAT_ID: cid=CHAT_ID
    else: cid=RUNTIME_CHAT
    if cid:
        try: tg('sendMessage',{'chat_id':cid,'text':text,'disable_web_page_preview':True})
        except Exception as e: print('TELEGRAM SEND ERROR:',e)

def num(v):
    try:return float(str(v).replace('$','').replace(',','').replace('%','').strip())
    except:return 0.0

def universe():
    out={}
    screens=("most_actives","day_gainers","small_cap_gainers","undervalued_growth_stocks")
    url="https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    for scr in screens:
        try:
            d=req(url,{"scrIds":scr,"count":250},20)
            result=(d.get("finance") or {}).get("result") or []
            quotes=result[0].get("quotes",[]) if result else []
            for q in quotes:
                s=str(q.get("symbol") or "").upper().strip()
                if s and s.isalpha() and "." not in s and "-" not in s:
                    out[s]=q
        except Exception as e:
            print("YAHOO SCREENER ERROR",scr,e)
        time.sleep(.5)
    return list(out.values())

def chart(symbol):
    try:return req(YC+urllib.parse.quote(symbol),{'range':'3mo','interval':'1d','events':'div,splits'},15)
    except Exception as e: print('YAHOO CHART ERROR',symbol,e); return None

def rows_from(c):
    try:
        z=c['chart']['result'][0]; q=z['indicators']['quote'][0]
        out=[]
        for i,cl in enumerate(q.get('close') or []):
            if cl is None: continue
            out.append({'close':float(cl),'high':float((q.get('high') or [cl])[i] or cl),'low':float((q.get('low') or [cl])[i] or cl),'volume':int((q.get('volume') or [0])[i] or 0)})
        return out
    except:return []

def rsi(v,p=14):
    if len(v)<p+1:return 50.0
    g=[];l=[]
    for i in range(1,len(v)):
        d=v[i]-v[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g[:p])/p; al=sum(l[:p])/p
    for i in range(p,len(g)):
        ag=((ag*(p-1))+g[i])/p; al=((al*(p-1))+l[i])/p
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def obv(rs):
    x=0;o=[0]
    for i in range(1,len(rs)):
        if rs[i]['close']>rs[i-1]['close']:x+=rs[i]['volume']
        elif rs[i]['close']<rs[i-1]['close']:x-=rs[i]['volume']
        o.append(x)
    return o

def sma(v,p): return sum(v[-p:])/len(v[-p:]) if v else 0

def news(symbol):
    try:
        d=req(YS,{'q':symbol,'quotesCount':3,'newsCount':8,'enableFuzzyQuery':'false','enableCb':'false'},15)
        items=d.get('news') or []; ranked=[]
        for n in items:
            t=str(n.get('title') or '').lower(); sc=sum(2 for w in POS if w in t)-sum(3 for w in NEG if w in t)
            pub=n.get('providerPublishTime')
            if pub:
                try:
                    age=time.time()-float(pub); sc+=3 if age<=86400 else (1 if age<=259200 else 0)
                except:pass
            if sc>=2: ranked.append((sc,n))
        ranked.sort(key=lambda x:x[0],reverse=True)
        return ranked[0][1] if ranked else None
    except Exception as e: print('YAHOO NEWS ERROR',symbol,e); return None

def analyze(r):
    s=str(r.get('symbol') or '').upper(); price=num(r.get('lastsale')); vol=int(num(r.get('volume'))); ch=num(r.get('pctchange'))
    if not s or not 0<price<5 or vol<100000 or abs(ch)<2:return None
    rs=rows_from(chart(s) or {})
    if len(rs)<20:return None
    closes=[x['close'] for x in rs]; rv=vol/max(1,sma([x['volume'] for x in rs],20))
    ob=obv(rs)
    if rv<1.2 or ob[-1]<sma(ob,20):return None
    rr=rsi(closes)
    if rr>=82:return None
    n=news(s)
    if not n:return None
    recent=rs[-20:]; hi=max(x['high'] for x in recent); lo=min(x['low'] for x in recent); d=max(hi-lo,price*.1)
    fib={'e1':hi+d*.272,'e2':hi+d*.618,'e3':hi+d}
    return {'symbol':s,'price':closes[-1],'volume':vol,'change':ch,'rsi':rr,'rvol':rv,'entry1':closes[-1]*.98,'entry2':closes[-1]*1.02,'stop':min(closes[-1]*.94,(hi-d*.618)*.98),'fib':fib,'title':n.get('title') or 'Positive catalyst','url':n.get('link') or n.get('url') or ''}

def fmt(x):return f'{x:.2f}' if x>=1 else f'{x:.4f}'

def scan():
    print('Scanning U.S. penny stocks...'); rows=universe(); print('Universe:',len(rows))
    c=[r for r in rows if 0<num(r.get('lastsale'))<5 and num(r.get('volume'))>=100000 and abs(num(r.get('pctchange')))>=2]
    c.sort(key=lambda r:(num(r.get('volume')),abs(num(r.get('pctchange')))),reverse=True); c=c[:60]; print('Candidates:',len(c))
    a=[]
    for r in c:
        try:
            z=analyze(r)
            if z:a.append(z)
        except Exception as e:print('ANALYZE ERROR',r.get('symbol'),e)
        time.sleep(.15)
    a.sort(key=lambda x:(x['rvol'],abs(x['change']),x['volume']),reverse=True)
    for z in a[:5]:
        s=z['symbol']
        if time.time()-LAST.get(s,0)<43200:continue
        LAST[s]=time.time();f=z['fib']
        msg=(f"🚨 {s}\n${fmt(z['price'])}\nVOL {z['volume']:,}\nRVOL {z['rvol']:.1f}x\nRSI {z['rsi']:.0f}\n\nE {fmt(z['entry1'])}–{fmt(z['entry2'])}\nSL {fmt(z['stop'])}\n\nFIB\n{fmt(f['e1'])}\n{fmt(f['e2'])}\n{fmt(f['e3'])}\n\nTP\n{fmt(f['e2'])}\n{fmt(f['e3'])}\n{fmt(f['e3']*1.10)}\n\n📰 NEWS\n{z['title']}")
        if z['url']:msg+='\n'+z['url']
        send(msg);print('ALERT:',s)

def market_open():
    n=datetime.now(ZoneInfo('America/New_York'));return n.weekday()<5 and 240<=n.hour*60+n.minute<960

def telegram_loop():
    global RUNTIME_CHAT
    RUNTIME_CHAT=CHAT_ID; offset=None
    while True:
        try:
            p={'timeout':5}
            if offset is not None:p['offset']=offset
            d=tg('getUpdates',p,12)
            for u in d.get('result',[]):
                offset=u['update_id']+1;m=u.get('message') or {};ch=m.get('chat') or {};t=str(m.get('text') or '').strip()
                if ch.get('id'):RUNTIME_CHAT=str(ch['id'])
                if t=='/start':send('✅ Connected. Penny Stock Scanner is active.')
                elif t=='/scan':send('🔎 Scanning U.S. penny stocks...');scan();send('✅ Scan completed.')
        except Exception as e:print('TELEGRAM ERROR',e);time.sleep(3)

def scanner_loop():
    while True:
        try:
            if market_open():scan();time.sleep(300)
            else:time.sleep(120)
        except Exception as e:print('SCANNER ERROR',e);time.sleep(30)

if __name__=='__main__':
    print('🚀 Penny Stock Scanner started');print('Data source: Nasdaq screener + Yahoo Finance')
    import threading
    threading.Thread(target=scanner_loop,daemon=True).start();telegram_loop()
