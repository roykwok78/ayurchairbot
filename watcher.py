import os, json, re, time, datetime
import requests
from bs4 import BeautifulSoup

SEARCH_URL = os.getenv("SEARCH_URL") or "https://jp.mercari.com/zh-TW/search?keyword=ayur%20chair&order=desc&sort=created_time&status=on_sale"
KEYWORDS = [s.strip().lower() for s in os.getenv("KEYWORDS", "ayur chair").split(",") if s.strip()]
COLOR_KEYWORDS = [s.strip().lower() for s in os.getenv("COLOR_KEYWORDS", "").split(",") if s.strip()]
MIN_PRICE = int(os.getenv("MIN_PRICE") or "0")
MAX_PRICE = int(os.getenv("MAX_PRICE") or "0")
LATEST_COUNT = int(os.getenv("LATEST_COUNT") or "5")
ALWAYS_SEND_LATEST = os.getenv("ALWAYS_SEND_LATEST") == "1"
SEEN_FILE = "data/seen_ids.json"

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,ja;q=0.7",
}

ITEM_URL_RE = re.compile(r"https://jp\.mercari\.com/item/m[0-9a-zA-Z]+")
PRICE_RE = re.compile(r"￥\s*([\d,]+)")

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

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram 未設定，略過推送")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"Telegram 推送失敗: {e}")

def match_filters(title, price):
    t = title.lower()
    if KEYWORDS and not any(k in t for k in KEYWORDS):
        return False
    if COLOR_KEYWORDS and not any(c in t for c in COLOR_KEYWORDS):
        return False
    if MIN_PRICE and price < MIN_PRICE:
        return False
    if MAX_PRICE and MAX_PRICE > 0 and price > MAX_PRICE:
        return False
    return True

def parse_price(text):
    m = PRICE_RE.search(text.replace(",", ""))
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except Exception:
            return 0
    digits = re.findall(r"\d+", text)
    return int(digits[0]) if digits else 0

def fetch_list():
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    links = list(dict.fromkeys(m.group(0) for m in ITEM_URL_RE.finditer(html)))  # 保留原排序
    items = []
    for url in links:
        title, price = "ayur chair", 0
        try:
            item_id = url.rsplit("/",1)[-1]
            a = soup.find("a", href=re.compile(r"/item/" + re.escape(item_id)))
            if a:
                card = a.find_parent()
                if card:
                    text_block = card.get_text(" ", strip=True)
                    price = parse_price(text_block)
                    for tag in card.find_all(["img","p","div","span"], limit=8):
                        t = (tag.get("alt") or tag.get_text(" ", strip=True) or "").strip()
                        if 6 <= len(t) <= 120 and "￥" not in t:
                            title = t
                            break
        except Exception:
            pass
        items.append({"id": url.rsplit("/",1)[-1], "title": title, "price": price, "url": url})
    return items

def fetch_date(item_url):
    try:
        r = requests.get(item_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        meta = soup.find("meta", attrs={"property": "og:updated_time"})
        if not meta:
            meta = soup.find("meta", attrs={"property": "product:price:updated_time"})
        if meta and meta.get("content"):
            utc_time = datetime.datetime.fromisoformat(meta["content"].replace("Z", "+00:00"))
            jst_time = utc_time.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            return jst_time.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return ""

def format_item(it, include_date=False):
    date_str = ""
    if include_date:
        dt = fetch_date(it['url'])
        if dt:
            date_str = f"\n上架：{dt}"
    return f"{it['title']} ¥{it['price']:,}{date_str}\n{it['url']}"

def main():
    seen = load_seen()
    items = fetch_list()
    new_items = [it for it in items if it["id"] not in seen and match_filters(it["title"], it["price"])]

    messages = []
    if new_items:
        msg = "🆕 Mercari 新上架 ayur chair\n\n"
        for idx, it in enumerate(new_items, 1):
            msg += f"{idx}. {format_item(it, include_date=True)}\n\n"
        messages.append(msg.strip())

    if ALWAYS_SEND_LATEST:
        latest = items[:LATEST_COUNT]
        msg2 = f"📌 最新 {LATEST_COUNT} 個在售 ayur chair\n\n"
        for idx, it in enumerate(latest, 1):
            msg2 += f"{idx}. {format_item(it, include_date=True)}\n\n"
        messages.append(msg2.strip())

    for it in new_items:
        seen.add(it["id"])
    save_seen(seen)

    for msg in messages:
        send_telegram(msg)

    print(f"Done. Pushed {len(new_items)} new items, plus latest list: {ALWAYS_SEND_LATEST}")

if __name__ == "__main__":
    time.sleep(1)
    main()
