import os,time,json,threading,requests
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo

BOT=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
CHAT=os.getenv('TELEGRAM_CHAT_ID','').strip()
ET=ZoneInfo('America/New_York')
last_alert={}
s=requests.Session();s.headers['User-Agent']='Mozilla/5.0'

STRONG_POS=(
    'fda approval','fda approves','fda clearance','fda cleared',
    'fda fast track','breakthrough therapy','orphan drug',
    'phase 3 results','phase 2 results','phase iii results','phase ii results',
    'primary endpoint','met primary endpoint','positive topline','top-line results',
    'merger','acquisition','definitive agreement','strategic partnership',
    'major contract','government contract','large order','license agreement',
    'buyout','tender offer','uplisting'
)
NEG=(
    'bankruptcy','delisting','lawsuit','investigation','going concern',
    'default','termination','reverse split','offering','atm offering'
)

from concurrent.futures import ThreadPoolExecutor, as_completed
SCAN_LOCK=threading.Lock()

def get(url,params=None,timeout=12):
    try:
        r=s.get(url,params=params,timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return None

def tg(method,payload=None):
    try:
        r=s.post(f'https://api.telegram.org/bot{BOT}/{method}',json=payload or {},timeout=25)
        if r.status_code==409:
            print('TELEGRAM 409 Conflict - another polling instance is active')
        return r.json() if r.status_code==200 else None
    except Exception:
        return None

def send(text,chat=None):
    return bool(tg('sendMessage',{'chat_id':chat or CHAT,'text':text,'disable_web_page_preview':True}))

def universe():
    d=get('https://api.nasdaq.com/api/screener/stocks',
          {'tableonly':'true','limit':'1000','offset':'0','download':'true'})
    try:
        rows=d['data']['rows']
        out={}
        for x in rows:
            sym=str(x.get('symbol','')).upper().strip()
            if sym and sym.isalpha():
                out[sym]=x
        return list(out.values())
    except Exception:
        return []

def news(sym):
    """News FIRST. Only recent, strong catalysts survive to technical analysis."""
    d=get('https://query2.finance.yahoo.com/v1/finance/search',
          {'q':sym,'quotesCount':1,'newsCount':8})
    if not d:
        return None

    now=datetime.now(timezone.utc)
    cutoff=now-timedelta(hours=24)
    best=None

    for x in d.get('news',[]):
        title=(x.get('title') or '').strip()
        if not title:
            continue

        ts=x.get('providerPublishTime')
        if not ts:
            continue
        try:
            published=datetime.fromtimestamp(int(ts),timezone.utc)
        except Exception:
            continue

        if published < cutoff or published > now+timedelta(minutes=5):
            continue

        low=title.lower()
        bad=[w for w in NEG if w in low]
        strong=[w for w in STRONG_POS if w in low]

        if bad or not strong:
            continue

        score=len(strong)*3
        item=(score,title,x.get('link',''),published)
        if best is None or item[0]>best[0]:
            best=item

    return best

def news_first_candidates(rows):
    """Check news concurrently before requesting charts/technical data."""
    candidates=[]
    total=len(rows)

    def check(row):
        sym=str(row.get('symbol','')).upper()
        price=nval(row.get('lastsale'))
        volume=nval(row.get('volume'))
        change=nval(row.get('pctchange')) if row.get('pctchange') is not None else 0

        # Fast pre-filter: only actively moving penny stocks reach the news check.
        # News must still be a strong, fresh catalyst.
        if price<=0 or price>=5 or volume<100000 or change<10:
            return None

        n=news(sym)
        if not n:
            return None

        return {'row':row,'news':n}

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures=[pool.submit(check,row) for row in rows]
        done=0
        for f in as_completed(futures):
            done+=1
            try:
                result=f.result()
                if result:
                    candidates.append(result)
            except Exception:
                pass

            if done % 100 == 0:
                print(f'News scan: {done}/{total} | news candidates: {len(candidates)}')

    candidates.sort(key=lambda x:x['news'][0],reverse=True)
    return candidates

def chart(sym):
    d=get(f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}',
          {'range':'3mo','interval':'1d','events':'history'})
    try:
        q=d['chart']['result'][0]['indicators']['quote'][0]
        out=[]
        for i,c in enumerate(q['close']):
            if c is not None:
                out.append({
                    'c':float(c),
                    'h':float(q['high'][i] or c),
                    'l':float(q['low'][i] or c),
                    'v':int(q['volume'][i] or 0)
                })
        return out
    except Exception:
        return []

def rsi(a,n=14):
    if len(a)<=n:
        return None
    g=[];l=[]
    for i in range(1,len(a)):
        d=a[i]-a[i-1]
        g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g[:n])/n;al=sum(l[:n])/n
    for i in range(n,len(g)):
        ag=(ag*(n-1)+g[i])/n
        al=(al*(n-1)+l[i])/n
    return 100 if al==0 else 100-100/(1+ag/al)

def nval(v):
    try:
        return float(str(v).replace('$','').replace(',','').replace('%','').strip())
    except Exception:
        return 0.0

def analyze_candidate(item):
    row=item['row']
    sym=str(row.get('symbol','')).upper()
    p=nval(row.get('lastsale'))
    v=nval(row.get('volume'))
    ch=nval(row.get('pctchange')) if row.get('pctchange') is not None else 0

    rows=chart(sym)
    if len(rows)<25:
        return None

    closes=[x['c'] for x in rows]
    vols=[x['v'] for x in rows]
    avg20=sum(vols[-20:])/20 or 1
    rv=(sum(vols[-5:])/5)/avg20

    if rv<1.5:
        return None

    r=rsi(closes)
    if r is None:
        return None

    lo=min(x['l'] for x in rows[-20:])
    hi=max(x['h'] for x in rows[-20:])
    d=hi-lo
    if d<=0 or p<lo+d*.50:
        return None

    fib=[lo+d*.618,lo+d,lo+d*1.272,lo+d*1.618]
    targets=[x for x in fib if x>p*1.02][:3] or [p*1.08,p*1.15,p*1.25]

    score=(
        5 +
        (2 if ch>=5 else 1) +
        (2 if rv>=3 else 1) +
        (2 if 45<=r<=72 else 1) +
        min(5,item['news'][0])
    )

    return {
        'sym':sym,'p':p,'v':v,'ch':ch,'rv':rv,'r':r,
        'targets':targets,'news':item['news'],'score':score
    }

def fmt(x):
    return f'{x:.2f}' if x>=1 else f'{x:.4f}'

def vol(v):
    return f'{v/1e6:.1f}M' if v>=1e6 else f'{v/1e3:.0f}K'

def message(a):
    e1=a['p']*.99
    e2=a['p']*1.01
    sl=a['p']*.94
    t='\n'.join(fmt(x) for x in a['targets'])
    age=max(0,int((datetime.now(timezone.utc)-a['news'][3]).total_seconds()/3600))

    return (
        f"🚨 {a['sym']}\n\n"
        f"💵 السعر: ${fmt(a['p'])}\n"
        f"📊 الحجم: {vol(a['v'])}\n"
        f"📈 RVOL: {a['rv']:.1f}x\n"
        f"📉 RSI: {a['r']:.0f}\n"
        f"📈 التغير: +{a['ch']:.1f}%\n\n"
        f"🎯 الدخول: {fmt(e1)}–{fmt(e2)}\n"
        f"🛑 وقف الخسارة: {fmt(sl)}\n\n"
        f"📐 فيبوناتشي:\n{t}\n\n"
        f"🎯 الأهداف:\n{t}\n\n"
        f"📰 المحفز (آخر {age} ساعة):\n"
        f"{a['news'][1]}\n{a['news'][2]}"
    )

def scan(chat=None):
    if not SCAN_LOCK.acquire(blocking=False):
        if chat:
            send('⏳ يوجد فحص جارٍ بالفعل، انتظر حتى ينتهي.',chat)
        return

    try:
        print('🔎 News-first scan started...')
        rows=universe()
        print('Universe:',len(rows))

        if chat:
            send(f'🔎 أفحص الأخبار أولًا لـ {len(rows)} سهم...\\nثم أحلل فنيًا الأسهم التي لديها محفز فقط.',chat)

        # Step 1: news first
        news_candidates=news_first_candidates(rows)
        print('Recent strong-news candidates:',len(news_candidates))

        if chat:
            send(f'📰 وُجد {len(news_candidates)} سهمًا لديه محفز حديث.\\n📊 أبدأ التحليل الفني لها فقط...',chat)

        # Step 2: technical analysis ONLY for news candidates
        results=[]
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures=[pool.submit(analyze_candidate,item) for item in news_candidates[:50]]
            for f in as_completed(futures):
                try:
                    a=f.result()
                    if a:
                        results.append(a)
                except Exception as e:
                    print('ANALYZE',e)

        results.sort(key=lambda x:(x['score'],x['rv'],x['ch']),reverse=True)
        print('Final candidates:',len(results))

        sent=0
        for a in results[:10]:
            old=last_alert.get(a['sym'])
            if old and datetime.now(timezone.utc)-old<timedelta(hours=12):
                continue
            if send(message(a),chat):
                last_alert[a['sym']]=datetime.now(timezone.utc)
                sent+=1

        if chat and sent==0:
            send('ℹ️ لم أجد حاليًا فرصة تحقق جميع الشروط.',chat)

    finally:
        SCAN_LOCK.release()

def telegram_loop():
    global CHAT
    if not BOT:
        print('ERROR: TELEGRAM_BOT_TOKEN missing')
        return

    tg('deleteWebhook',{'drop_pending_updates':False})
    off=None

    while True:
        try:
            p={'timeout':25,'allowed_updates':['message']}
            if off is not None:
                p['offset']=off

            d=tg('getUpdates',p)
            for u in (d or {}).get('result',[]):
                off=u['update_id']+1
                m=u.get('message',{})
                cid=m.get('chat',{}).get('id')
                txt=(m.get('text') or '').strip()
                if not cid:
                    continue

                if txt.startswith('/start'):
                    CHAT=str(cid)
                    send('✅ تم الربط. الماسح يعمل الآن.',cid)

                elif txt.startswith('/scan'):
                    send('🔎 أبدأ البحث عن الأخبار أولًا...',cid)
                    threading.Thread(target=scan,args=(cid,),daemon=True).start()

                elif txt.startswith('/status'):
                    send(
                        '✅ الماسح يعمل\n'
                        '🟢 المراقبة تلقائية كل دقيقتين أثناء السوق\n'
                        '📰 خبر حديث خلال 24 ساعة\n'
                        '💵 السعر < $5\n'
                        '📊 الحجم ≥ 100K\n'
                        '📈 الارتفاع ≥ 10%\n'
                        '🔥 RVOL ≥ 1.5x',
                        cid
                    )

        except Exception as e:
            print('POLL',e)
            time.sleep(8)

def market():
    n=datetime.now(ET)
    return n.weekday()<5 and 4<=n.hour+(n.minute/60)<=16

def main():
    print('🚀 Penny Stock Scanner started')
    print('Mode: AUTO every 2 minutes | +10% first | fresh catalyst | momentum')
    print('Auto alerts: penny < $5, gain >=10%, volume >=100K, fresh catalyst <=24h, RVOL >=1.5')
    threading.Thread(target=telegram_loop,daemon=True).start()

    while True:
        if market():
            scan()
        else:
            print('Outside U.S. premarket/market window.')
        time.sleep(120)

if __name__=='__main__':
    main()
