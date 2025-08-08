import os, json, re, time, datetime
import requests
from bs4 import BeautifulSoup

# ---------- 可用 Secrets 覆蓋 ----------
SEARCH_URL = os.getenv("SEARCH_URL") or "https://jp.mercari.com/search?keyword=ayur%20chair&order=desc&sort=created_time"
KEYWORDS = [s.strip().lower() for s in os.getenv("KEYWORDS", "ayur chair").split(",") if s.strip()]
COLOR_KEYWORDS = [s.strip().lower() for s in os.getenv("COLOR_KEYWORDS", "").split(",") if s.strip()]
MIN_PRICE = int(os.getenv("MIN_PRICE") or "0")
MAX_PRICE = int(os.getenv("MAX_PRICE") or "0")
LATEST_COUNT = int(os.getenv("LATEST_COUNT") or "5")
ALWAYS_SEND_LATEST = os.getenv("ALWAYS_SEND_LATEST") == "1"
USE_API = os.getenv("USE_API") == "1"     # 可選：強制用 API

SEEN_FILE = "data/seen_ids.json"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9,zh-TW;q=0.8",
}

PRICE_RE = re.compile(r"[￥¥]\s*([\d,]+)")

def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False)

def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("WARN: Telegram 未設定，略過推送")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            print(f"WARN: Telegram 回應 {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"WARN: Telegram 推送失敗: {e}")

def parse_price(text: str) -> int:
    m = PRICE_RE.search(text.replace(",", ""))
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except Exception:
            return 0
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else 0

def match_filters(title: str, price: int) -> bool:
    t = (title or "").lower()
    if KEYWORDS and not any(k in t for k in KEYWORDS): return False
    if COLOR_KEYWORDS and not any(c in t for c in COLOR_KEYWORDS): return False
    if MIN_PRICE and price < MIN_PRICE: return False
    if MAX_PRICE and MAX_PRICE > 0 and price > MAX_PRICE: return False
    return True

# ---------- 先試 HTML，抓唔到就用 API ----------
def fetch_list_html():
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    anchors = soup.select('a[href^="/item/m"]')
    items, seen_ids_local = [], set()
    for a in anchors:
        href = a.get("href") or ""
        if not href.startswith("/item/m"): continue
        item_id = href.rsplit("/", 1)[-1]
        if item_id in seen_ids_local: continue
        seen_ids_local.add(item_id)
        url = "https://jp.mercari.com" + href
        title, price = None, 0
        img = a.find("img")
        if img and img.get("alt"): title = img["alt"].strip()
        card = a
        for _ in range(3):
            text_block = (card.get_text(" ", strip=True) or "")
            if not title:
                for tag in card.find_all(["p","div","span"], limit=8):
                    t = (tag.get_text(" ", strip=True) or "").strip()
                    if 6 <= len(t) <= 120 and "￥" not in t and "¥" not in t:
                        title = t; break
            if price == 0: price = parse_price(text_block)
            if title and price: break
            card = card.parent or card
        if not title: title = "ayur chair"
        items.append({"id": item_id, "title": title, "price": price, "url": url, "created": None})
    return items

def fetch_list_api():
    # 從 SEARCH_URL 取 keyword、是否 on_sale
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(SEARCH_URL).query)
    keyword = (qs.get("keyword", ["ayur chair"])[0]).strip()
    want_on_sale = (qs.get("status", [""])[0] == "on_sale")

    payload = {
        "pageSize": 60,
        "searchSessionId": "",
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": ["THUMBNAIL_TYPE_STATIC", "THUMBNAIL_TYPE_MOVIE"],
        "searchCondition": {
            "keyword": keyword,
            "sort": "CREATED_TIME",
            "order": "DESC",
            "statuses": ["on_sale"] if want_on_sale else [],
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Platform": "web",
        "User-Agent": HEADERS["User-Agent"],
        "Accept-Language": HEADERS["Accept-Language"],
    }
    url = "https://api.mercari.jp/v2/entities:search"
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = []
    for ent in data.get("items", []):
        m = ent.get("id")
        if not m: continue
        title = ent.get("name") or "ayur chair"
        price = int(ent.get("price") or 0)
        url = f"https://jp.mercari.com/item/{m}"
        created = ent.get("updated") or ent.get("created")
        items.append({"id": m, "title": title, "price": price, "url": url, "created": created})
    return items

def fetch_list():
    items = [] if USE_API else fetch_list_html()
    if USE_API or len(items) == 0:
        try:
            api_items = fetch_list_api()
            if api_items: return api_items
        except Exception as e:
            print(f"WARN: API 後備也失敗: {e}")
    return items

def fetch_date(item_url: str, created_iso: str | None) -> str:
    # 如果 API 已經提供 ISO 時間，直接用
    if created_iso:
        try:
            iso = created_iso.replace("Z", "+00:00")
            dt_utc = datetime.datetime.fromisoformat(iso)
            jst = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            return jst.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    # 後備抓詳情
    try:
        r = requests.get(item_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for key in ("og:updated_time","product:price:updated_time","article:modified_time","og:published_time"):
            meta = soup.find("meta", attrs={"property": key})
            if meta and meta.get("content"):
                iso = meta["content"].strip().replace("Z", "+00:00")
                dt_utc = datetime.datetime.fromisoformat(iso)
                jst = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                return jst.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return ""

def format_item(it, with_date=False) -> str:
    date_str = ""
    if with_date:
        dt = fetch_date(it["url"], it.get("created"))
        if dt: date_str = f"\n上架：{dt}"
    price_part = f" ¥{it['price']:,}" if it["price"] else ""
    return f"{it['title']}{price_part}{date_str}\n{it['url']}"

def main():
    send_telegram("📡 Mercari Watch 測試訊息：workflow 已啟動")
    print(f"DEBUG: ALWAYS_SEND_LATEST={ALWAYS_SEND_LATEST}, LATEST_COUNT={LATEST_COUNT}")
    print(f"DEBUG: SEARCH_URL={SEARCH_URL} USE_API={USE_API}")

    seen = load_seen()
    items = fetch_list()
    print(f"DEBUG: 列表抓到 {len(items)} 件")

    new_items = [it for it in items if it["id"] not in seen and match_filters(it["title"], it["price"])]
    print(f"DEBUG: new_items={len(new_items)} / seen_ids={len(seen)}")

    messages = []

    if new_items:
        msg = "🆕 Mercari 新上架 ayur chair（在售/由新至舊）\n\n"
        for i, it in enumerate(new_items, 1):
            msg += f"{i}. {format_item(it, with_date=True)}\n\n"
        messages.append(msg.strip())

    if ALWAYS_SEND_LATEST:
        latest = items[:LATEST_COUNT]
        print(f"DEBUG: latest_to_send={len(latest)}")
        if latest:
            msg2 = f"📌 最新 {len(latest)} 個 ayur chair（由新至舊）\n\n"
            for i, it in enumerate(latest, 1):
                msg2 += f"{i}. {format_item(it, with_date=True)}\n\n"
            messages.append(msg2.strip())
        else:
            messages.append("📌 最新清單：目前搜尋結果為空。")

    for it in new_items:
        seen.add(it["id"])
    save_seen(seen)

    for m in messages:
        send_telegram(m)

    print(f"Done. Pushed new={len(new_items)}, sent_latest={ALWAYS_SEND_LATEST}")

if __name__ == "__main__":
    time.sleep(1)
    main()
