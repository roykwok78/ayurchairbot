import os, json, re, time
import requests
from bs4 import BeautifulSoup

SEARCH_URL = os.getenv("SEARCH_URL", "https://jp.mercari.com/zh-TW/search?keyword=ayur%20chair&order=desc&sort=created_time")
KEYWORDS = [s.strip().lower() for s in os.getenv("KEYWORDS", "ayur chair").split(",") if s.strip()]
COLOR_KEYWORDS = [s.strip().lower() for s in os.getenv("COLOR_KEYWORDS", "").split(",") if s.strip()]  # 例: "black,黑,白,white"
MIN_PRICE = int(os.getenv("MIN_PRICE", "0") or 0)
MAX_PRICE = int(os.getenv("MAX_PRICE", "0") or 0)  # 0 = 不限制
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

def fetch_new_items():
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    links = set(m.group(0) for m in ITEM_URL_RE.finditer(html))
    items = []
    for url in links:
        title, price = "ayur chair", 0
        # 嘗試就近抓標題／價錢
        try:
            item_id = url.rsplit("/",1)[-1]
            # 定位到含該 href 的 a 標籤
            a = soup.find("a", href=re.compile(r"/item/" + re.escape(item_id)))
            if a:
                card = a.find_parent()
                if card:
                    text_block = card.get_text(" ", strip=True)
                    price = parse_price(text_block)
                    # 嘗試找較可讀的標題
                    for tag in card.find_all(["img","p","div","span"], limit=8):
                        t = (tag.get("alt") or tag.get_text(" ", strip=True) or "").strip()
                        if 6 <= len(t) <= 120 and "￥" not in t:
                            title = t
                            break
        except Exception:
            pass
        items.append({"id": url.rsplit("/",1)[-1], "title": title, "price": price, "url": url})
    return items

def main():
    seen = load_seen()
    items = fetch_new_items()
    items = sorted(items, key=lambda x: x["id"], reverse=True)

    pushed = 0
    for it in items:
        if it["id"] in seen:
            continue
        if match_filters(it["title"], it["price"]):
            msg = f"🆕 Mercari 新上架：{it['title']}\n價格：約¥{it['price']:,}\n{it['url']}"
            send_telegram(msg)
            pushed += 1
        seen.add(it["id"])

    save_seen(seen)
    print(f"Done. Pushed {pushed} items.")

if __name__ == "__main__":
    time.sleep(1)
    main()
