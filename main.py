import os, time, threading, requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

BOT=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip()
ET=ZoneInfo('America/New_York')
S=requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*'})
LAST_ALERT={}
LOCK=threading.Lock()

STRONG=('.com','fda approval','fda approves','fda clearance','fda cleared','fda fast track',
'breakthrough therapy','orphan drug','phase 3 results','phase 2 results','phase iii results',
'phase ii results','primary endpoint','met primary endpoint','positive topline','top-line results',
'merger','acquisition','definitive agreement','strategic partnership','major contract',
'government contract','large order','license agreement','buyout','tender offer','uplisting',
'contract award','receives award')
NEG=('bankruptcy','delisting','going concern','default','termination','reverse split','offering','atm offering')


def get(url, params=None, timeout=10):
    try:
        r=S.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print('HTTP',e)
        return None


def tg(method,payload=None):
    try:
        r=S.post(f'https://api.telegram.org/bot{BOT}/{method}',json=payload or {},timeout=20)
        return r.json() if r.status_code==200 else None
    except Exception as e:
        print('TG',e); return None


def send(msg, chat=None):
    return bool(tg('sendMessage',{'chat_id':chat or CHAT,'text':msg,'disable_web_page_preview':True}))


def num(v):
    try: return float(str(v).replace('$','').replace(',','').replace('%','').strip())
    except: return 0.0


def yahoo_screen(scr_id, count=100):
    d=get('https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved',
          {'scrIds':scr_id,'count':count})
    try: return d['finance']['result'][0]['quotes']
    except: return []


def candidate_pool():
    # Do NOT crawl thousands of symbols. Pull only focused market lists.
    screens=['day_gainers','most_actives','small_cap_gainers']
    out={}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs={ex.submit(yahoo_screen,x,100):x for x in screens}
        for f in as_completed(fs):
            try:
                for q in f.result():
                    sym=str(q.get('symbol','')).upper()
                    if not sym: continue
                    price=num(q.get('regularMarketPrice') or q.get('postMarketPrice') or q.get('preMarketPrice'))
                    change=num(q.get('regularMarketChangePercent') or q.get('postMarketChangePercent') or q.get('preMarketChangePercent'))
                    volume=int(q.get('regularMarketVolume') or q.get('postMarketVolume') or q.get('preMarketVolume') or 0)
                    if price<=0 or price>=5: continue
                    # Two entry paths: >=10% mover OR strong-volume mover.
                    if not ((change>=10 and volume>=100000) or (change>=3 and volume>=500000)):
                        continue
                    out[sym]={'sym':sym,'p':price,'ch':change,'v':volume,'raw':q}
            except Exception as e: print('SCREEN',e)
    arr=list(out.values())
    arr.sort(key=lambda x:(x['ch'],x['v']),reverse=True)
    print('Focused candidate pool:',len(arr))
    return arr[:180]


def news(sym):
    d=get('https://query2.finance.yahoo.com/v1/finance/search',{'q':sym,'quotesCount':1,'newsCount':10})
    if not d: return None
    now=datetime.now(timezone.utc); cut=now-timedelta(hours=24); best=None
    for x in d.get('news',[]):
        title=(x.get('title') or '').strip(); ts=x.get('providerPublishTime')
        if not title or not ts: continue
        try: dt=datetime.fromtimestamp(int(ts),timezone.utc)
        except: continue
        if dt<cut or dt>now+timedelta(minutes=5): continue
        low=title.lower()
        if any(w in low for w in NEG): continue
        hits=[w for w in STRONG if w in low]
        if not hits: continue
        score=len(hits)*3
        item=(score,title,x.get('link',''),dt)
        if best is None or score>best[0]: best=item
    return best


def intraday(sym):
    d=get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}',
          {'range':'1d','interval':'5m','includePrePost':'true','events':'history'})
    try:
        r=d['chart']['result'][0]; q=r['indicators']['quote'][0]
        a=[]
        for i,c in enumerate(q['close']):
            if c is None: continue
            a.append({'o':float(q['open'][i] or c),'h':float(q['high'][i] or c),'l':float(q['low'][i] or c),
                      'c':float(c),'v':int(q['volume'][i] or 0)})
        return a
    except: return []


def daily(sym):
    d=get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}',{'range':'3mo','interval':'1d','events':'history'})
    try:
        q=d['chart']['result'][0]['indicators']['quote'][0]; a=[]
        for i,c in enumerate(q['close']):
            if c is not None: a.append({'c':float(c),'h':float(q['high'][i] or c),'l':float(q['low'][i] or c),'v':int(q['volume'][i] or 0)})
        return a
    except: return []


def rsi(c,n=14):
    if len(c)<=n: return None
    g=[];l=[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g[:n])/n; al=sum(l[:n])/n
    for i in range(n,len(g)): ag=(ag*(n-1)+g[i])/n; al=(al*(n-1)+l[i])/n
    return 100 if al==0 else 100-100/(1+ag/al)


def analyze(x):
    sym=x['sym']; bars=intraday(sym); days=daily(sym)
    if len(bars)<8 or len(days)<25: return None
    p=bars[-1]['c']
    # Consolidated intraday volume + recent volume acceleration.
    v=sum(b['v'] for b in bars)
    recent=sum(b['v'] for b in bars[-6:])
    prior=sum(b['v'] for b in bars[-24:-6]) or 1
    rvol=(recent/6)/(prior/18)
    if x['ch']>=10 and x['v']>=100000:
        mover_ok=True
    else:
        mover_ok=(rvol>=1.5 and x['v']>=500000)
    n=news(sym)
    news_ok=n is not None
    if not mover_ok and not news_ok: return None

    # Practical entry: near VWAP / breakout of recent 5m high.
    pv=sum(b['c']*b['v'] for b in bars if b['v']>0); vv=sum(b['v'] for b in bars if b['v']>0) or 1
    vwap=pv/vv
    recent_high=max(b['h'] for b in bars[-12:]); recent_low=min(b['l'] for b in bars[-12:])
    entry_low=max(vwap*0.995, recent_low)
    entry_high=min(recent_high*1.005, p*1.015)
    if entry_low>entry_high: entry_low=p*0.99; entry_high=p*1.01

    # Risk/targets: nearest intraday resistance, then daily range extensions.
    dlow=min(z['l'] for z in days[-20:]); dhigh=max(z['h'] for z in days[-20:]); dr=dhigh-dlow
    fib=[dlow+dr*1.0, dlow+dr*1.272, dlow+dr*1.618]
    targets=[z for z in fib if z>p*1.03][:3]
    if len(targets)<3: targets += [p*1.08,p*1.15,p*1.25]
    targets=targets[:3]
    sl=max(recent_low*0.985, p*0.94)
    rr=((targets[0]-p)/(p-sl)) if p>sl else 0
    score=(3 if x['ch']>=10 else 1)+(2 if rvol>=2 else 1)+(3 if news_ok else 0)+(1 if p>=vwap else 0)+(1 if rr>=1.5 else 0)
    return {**x,'p':p,'rv':rvol,'vwap':vwap,'rsi':rsi([z['c'] for z in days]),'entry':(entry_low,entry_high),'sl':sl,'targets':targets,'news':n,'score':score,'v5':v}


def fmt(x): return f'{x:.2f}' if x>=1 else f'{x:.4f}'
def vol(v): return f'{v/1e6:.1f}M' if v>=1e6 else f'{v/1e3:.0f}K'


def msg(a):
    news_line=''
    if a['news']:
        age=max(0,int((datetime.now(timezone.utc)-a['news'][3]).total_seconds()/3600))
        news_line=f"\n📰 الخبر ({age}س):\n{a['news'][1]}\n{a['news'][2]}"
    return (f"🚨 {a['sym']}\n\n💵 السعر: ${fmt(a['p'])}\n📈 التغير: +{a['ch']:.1f}%\n"
            f"📊 الحجم: {vol(a['v'])}\n🔥 RVOL: {a['rv']:.1f}x\n📉 RSI: {a['rsi']:.0f}\n\n"
            f"🎯 منطقة الدخول: {fmt(a['entry'][0])} – {fmt(a['entry'][1])}\n"
            f"🛑 وقف الخسارة: {fmt(a['sl'])}\n\n🎯 الأهداف:\n"
            f"1️⃣ {fmt(a['targets'][0])}\n2️⃣ {fmt(a['targets'][1])}\n3️⃣ {fmt(a['targets'][2])}\n"
            f"📍 VWAP: {fmt(a['vwap'])}{news_line}")


def scan():
    if not LOCK.acquire(False): return
    try:
        print('AUTO SCAN: focused movers/active only')
        pool=candidate_pool()
        if not pool:
            print('No focused movers.'); return
        results=[]
        with ThreadPoolExecutor(max_workers=12) as ex:
            fs=[ex.submit(analyze,x) for x in pool]
            for f in as_completed(fs):
                try:
                    a=f.result()
                    if a: results.append(a)
                except Exception as e: print('ANALYZE',e)
        results.sort(key=lambda z:(z['score'],z['ch'],z['rv']),reverse=True)
        print('Alerts:',len(results))
        for a in results[:10]:
            old=LAST_ALERT.get(a['sym'])
            if old and datetime.now(timezone.utc)-old<timedelta(hours=12): continue
            if send(msg(a)): LAST_ALERT[a['sym']]=datetime.now(timezone.utc)
    finally: LOCK.release()


def telegram_loop():
    global CHAT
    if not BOT: return
    tg('deleteWebhook',{'drop_pending_updates':False}); off=None
    while True:
        try:
            p={'timeout':20,'allowed_updates':['message']}
            if off is not None: p['offset']=off
            d=tg('getUpdates',p)
            for u in (d or {}).get('result',[]):
                off=u['update_id']+1; m=u.get('message',{}); cid=m.get('chat',{}).get('id'); txt=(m.get('text') or '').strip()
                if not cid: continue
                if txt.startswith('/start'):
                    CHAT=str(cid); send('✅ تم الربط. الماسح يعمل تلقائيًا.',cid)
                elif txt.startswith('/status'):
                    send('✅ يعمل تلقائيًا كل دقيقتين\n🎯 Penny < $5\n📈 +10% أو زخم/حجم قوي\n📰 خبر حديث إن وجد\n🎯 دخول + وقف + 3 أهداف',cid)
        except Exception as e: print('POLL',e); time.sleep(5)


def market():
    n=datetime.now(ET); return n.weekday()<5 and 4<=n.hour+(n.minute/60)<=20


def main():
    print('🚀 Practical Penny Scanner started')
    print('Focused mode: top gainers + most active + small-cap gainers; no full-market news crawl')
    threading.Thread(target=telegram_loop,daemon=True).start()
    while True:
        if market(): scan()
        else: print('Outside US extended-hours window.')
        time.sleep(120)

if __name__=='__main__': main()
