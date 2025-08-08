import os, json, re, time, datetime
import requests
from bs4 import BeautifulSoup

# -------- 可調參數（亦可用 GitHub Secrets 覆蓋） --------
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
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,ja;q=0.7",
}

PRICE_RE = re.compile(r"[￥¥]\s*([\d,]+)")

# -------- 通用工具 --------
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
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
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
    if KEYWORDS and not any(k in t for k in KEYWORDS):
        return False
    if COLOR_KEYWORDS and COLOR_KEYWORDS and not any(c in t for c in COLOR_KEYWORDS):
        return False
    if MIN_PRICE and price < MIN_PRICE:
        return False
    if MAX_PRICE and MAX_PRICE > 0 and price > MAX_PRICE:
        return False
    return True

# -------- 抓取列表（更穩陣做法）--------
def fetch_list():
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 取所有商品卡 anchor：href 以 /item/m 開頭
    anchors = soup.select('a[href^="/item/m"]')
    items = []
    seen_ids_local = set()

    for a in anchors:
        href = a.get("href") or ""
        if not href.startswith("/item/m"):
            continue
        item_id = href.rsplit("/", 1)[-1]
        if item_id in seen_ids_local:
            continue
        seen_ids_local.add(item_id)
        url = "https://jp.mercari.com" + href

        # 嘗試在卡片附近找標題/價錢
        title = None
        price = 0

        # 1) 先試 <img alt>
        img = a.find("img")
        if img and img.get("alt"):
            title = img["alt"].strip()

        # 2) 再試 anchor 及父層文本
        card = a
        for _ in range(3):  # 往上找幾層
            text_block = (card.get_text(" ", strip=True) or "")
            if not title:
                # 找一段像標題的字
                for tag in card.find_all(["p", "div", "span"], limit=8):
                    t = (tag.get_text(" ", strip=True) or "").strip()
                    if 6 <= len(t) <= 120 and "￥" not in t and "¥" not in t:
                        title = t
                        break
            if price == 0:
                price = parse_price(text_block)
            if title and price:
                break
            if card.parent:
                card = card.parent
            else:
                break

        # 3) 後備：至少給個 title
        if not title:
            title = "ayur chair"

        items.append({"id": item_id, "title": title, "price": price, "url": url})

    print(f"DEBUG: 列表抓到 {len(items)} 件")
    return items

# -------- 抓詳情頁取上架時間（JST）--------
def fetch_date(item_url: str) -> str:
    try:
        r = requests.get(item_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # 常見 meta：og:updated_time / product:price:updated_time / article:modified_time
        for key in ("og:updated_time", "product:price:updated_time", "article:modified_time", "og:published_time"):
            meta = soup.find("meta", attrs={"property": key})
            if meta and meta.get("content"):
                iso = meta["content"].strip().replace("Z", "+00:00")
                dt_utc = datetime.datetime.fromisoformat(iso)
                jst = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                return jst.strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        print(f"WARN: 取上架時間失敗: {e}")
    return ""

def format_item(it, with_date=False) -> str:
    date_str = ""
    if with_date:
        dt = fetch_date(it["url"])
        if dt:
            date_str = f"\n上架：{dt}"
    price_part = f" ¥{it['price']:,}" if it["price"] else ""
    return f"{it['title']}{price_part}{date_str}\n{it['url']}"

# -------- 主流程 --------
def main():
    # Debug ping
    send_telegram("📡 Mercari Watch 測試訊息：workflow 已啟動")
    print(f"DEBUG: ALWAYS_SEND_LATEST={ALWAYS_SEND_LATEST}, LATEST_COUNT={LATEST_COUNT}")
    print(f"DEBUG: SEARCH_URL={SEARCH_URL}")

    seen = load_seen()
    items = fetch_list()

    # 新貨（按目前頁面順序 = 已由新至舊）
    new_items = [it for it in items if it["id"] not in seen and match_filters(it["title"], it["price"])]
    print(f"DEBUG: new_items={len(new_items)} / seen_ids={len(seen)}")

    messages = []

    # 有新貨 → 推送新貨
    if new_items:
        msg = "🆕 Mercari 新上架 ayur chair（在售）\n\n"
        for i, it in enumerate(new_items, 1):
            msg += f"{i}. {format_item(it, with_date=True)}\n\n"
        messages.append(msg.strip())

    # 需要每次都推最新 N 個
    if ALWAYS_SEND_LATEST:
        latest = items[:LATEST_COUNT]
        print(f"DEBUG: latest_to_send={len(latest)}")
        if latest:
            msg2 = f"📌 最新 {len(latest)} 個在售 ayur chair（由新至舊）\n\n"
            for i, it in enumerate(latest, 1):
                msg2 += f"{i}. {format_item(it, with_date=True)}\n\n"
            messages.append(msg2.strip())
        else:
            messages.append("📌 最新清單：目前搜尋結果沒有在售商品。")

    # 更新 seen（只把真正視為新貨的加入）
    for it in new_items:
        seen.add(it["id"])
    save_seen(seen)

    # 發送
    if not messages:
        print("INFO: 無新貨，且未開 ALWAYS_SEND_LATEST；今次不推送列表。")
    else:
        for m in messages:
            send_telegram(m)

    print(f"Done. Pushed new={len(new_items)}, sent_latest={ALWAYS_SEND_LATEST}")

if __name__ == "__main__":
    time.sleep(1)
    main()
