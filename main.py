import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

FMP_KEY = os.getenv("FMP_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not FMP_KEY or not BOT_TOKEN:
    raise RuntimeError("Missing FMP_API_KEY or TELEGRAM_BOT_TOKEN")

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"
FMP = "https://financialmodelingprep.com/stable"
CHAT_ID = None
LAST_ALERT = {}

POSITIVE = ("approval","approved","fda","contract","partnership","agreement",
            "acquisition","merger","clinical","trial","results","positive",
            "launch","orders","revenue","patent","license","milestone",
            "expansion","award","government")
NEGATIVE = ("bankruptcy","delisting","investigation","lawsuit","fraud","default")

def request_json(url, data=None):
    if data is not None:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                      headers={"User-Agent":"PennyStockScanner/1.0"})
    else:
        req = urllib.request.Request(url, headers={"User-Agent":"PennyStockScanner/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

def fmp(endpoint, params=None):
    params = dict(params or {})
    params["apikey"] = FMP_KEY
    return request_json(FMP + endpoint + "?" + urllib.parse.urlencode(params))

def tg(method, data=None):
    return request_json(TG + "/" + method, data or {})

def send(text):
    if CHAT_ID:
        tg("sendMessage", {"chat_id": CHAT_ID, "text": text,
                           "disable_web_page_preview": True})

def get_quotes():
    out = []
    for exchange in ("NASDAQ","NYSE","AMEX"):
        try:
            data = fmp("/batch-exchange-quote", {"exchange": exchange})
            if isinstance(data, list):
                out.extend(data)
        except Exception as e:
            print("QUOTE ERROR", exchange, e)
    return out

def get_news():
    try:
        data = fmp("/news/stock-latest", {"page":0,"limit":100})
        return data if isinstance(data,list) else []
    except Exception as e:
        print("NEWS ERROR", e)
        return []

def fib(high, low):
    d = high-low
    return {"e1":high+d*.272, "e2":high+d*.618, "e3":high+d}

def fmt(x):
    return f"{x:.2f}" if x >= 1 else f"{x:.4f}"

def analyze(q, news):
    symbol = q.get("symbol")
    try:
        price=float(q.get("price") or 0)
        volume=int(q.get("volume") or 0)
        high=float(q.get("dayHigh") or 0)
        low=float(q.get("dayLow") or 0)
        change=float(q.get("change") or 0)
    except Exception:
        return None

    if not symbol or price <= 0 or price > 5 or volume < 100000:
        return None
    change_pct = abs(change/price*100)
    if change_pct < 2:
        return None

    item=None
    for n in news:
        tickers=str(n.get("tickers") or n.get("symbol") or "").upper()
        title=str(n.get("title") or n.get("headline") or "")
        t=title.lower()
        if symbol.upper() in tickers and any(w in t for w in POSITIVE) and not any(w in t for w in NEGATIVE):
            item=n
            break
    if not item:
        return None

    if high <= low or low <= 0:
        high,low=price*1.12,price*.88
    f=fib(high,low)
    return {
        "symbol":symbol,"price":price,"volume":volume,"change":change_pct,
        "entry1":price*.98,"entry2":price*1.02,"stop":low*.97,"fib":f,
        "title":item.get("title") or item.get("headline") or "Positive catalyst",
        "url":item.get("url") or item.get("link") or ""
    }

def scan():
    quotes=get_quotes()
    news=get_news()
    candidates=[a for q in quotes if (a:=analyze(q,news))]
    candidates.sort(key=lambda x:(x["change"],x["volume"]), reverse=True)
    for a in candidates[:10]:
        s=a["symbol"]
        if time.time()-LAST_ALERT.get(s,0)<43200:
            continue
        LAST_ALERT[s]=time.time()
        f=a["fib"]
        msg=(f"🚨 {s}\n${fmt(a['price'])}\nVOL {a['volume']:,}\n"
             f"CHG +{a['change']:.1f}%\n\nE {fmt(a['entry1'])}–{fmt(a['entry2'])}\n"
             f"SL {fmt(a['stop'])}\n\nFIB\n{fmt(f['e1'])}\n{fmt(f['e2'])}\n{fmt(f['e3'])}"
             f"\n\nTP\n{fmt(f['e1'])}\n{fmt(f['e2'])}\n{fmt(f['e3'])}\n\n📰 NEWS\n{a['title']}")
        if a["url"]:
            msg += "\n" + a["url"]
        send(msg)

def market_open():
    now=datetime.now(ZoneInfo("America/New_York"))
    if now.weekday()>=5:
        return False
    m=now.hour*60+now.minute
    return 240<=m<960

def scanner_loop():
    while True:
        try:
            if market_open():
                scan()
                time.sleep(300)
            else:
                time.sleep(120)
        except Exception as e:
            print("SCAN ERROR",e)
            time.sleep(30)

def telegram_loop():
    global CHAT_ID
    offset=None
    while True:
        try:
            params={"timeout":25}
            if offset is not None:
                params["offset"]=offset
            result=tg("getUpdates",params)
            for u in result.get("result",[]):
                offset=u["update_id"]+1
                message=u.get("message") or {}
                chat=message.get("chat") or {}
                text=message.get("text") or ""
                if chat.get("id"):
                    CHAT_ID=chat["id"]
                if text=="/start":
                    send("✅ Connected. Penny Stock Scanner is active.")
                elif text=="/scan":
                    send("🔎 Scanning U.S. penny stocks...")
                    scan()
                    send("✅ Scan completed.")
        except Exception as e:
            print("TELEGRAM ERROR",e)
            time.sleep(5)

if __name__=="__main__":
    print("🚀 Penny Stock Scanner started")
    import threading
    threading.Thread(target=scanner_loop,daemon=True).start()
    telegram_loop()
