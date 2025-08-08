import os, json, re, time, datetime
import requests
from bs4 import BeautifulSoup

# -------- 環境變數 / Secrets --------
SEARCH_URL = os.getenv("SEARCH_URL") or "https://jp.mercari.com/search?keyword=ayur%20chair&order=desc&sort=created_time"
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
    "Accept-Language": "ja,en;q=0.9,zh-TW;q=0.8",
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
    if KEYWORDS and not any(k in t for k in KEYWORDS):
        return False
    if COLOR_KEYWORDS and not any(c in t for c in COLOR_KEYWORDS):
        return False
    if MIN_PRICE and price < MIN_PRICE:
        return False
    if MAX_PRICE and MAX_PRICE > 0 and price > MAX_PRICE:
        return False
    return True

# -------- ① 用 Playwright 抓（渲染後 DOM）--------
def fetch_list_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"WARN: Playwright 未安裝或不可用: {e}")
        return []

    items, seen_ids_local = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            locale="ja-JP",
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1366, "height": 900},
        )
        # 隱藏 webdriver
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        page.set_default_timeout(30000)

        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # 嘗試點 Cookie 同意
        try:
            page.locator("button:has-text('同意')").first.click(timeout=2000)
        except Exception:
            pass

        # 捲動幾屏，觸發 lazyload
        try:
            for _ in range(6):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(1200)
        except Exception:
            pass

        anchors = page.locator('a[href^="/item/"]')
        count = anchors.count()
        print(f"DEBUG: playwright anchors count = {count}")
        for i in range(count):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            if not href.startswith("/item/"):
                continue
            item_id = href.rsplit("/", 1)[-1]
            if item_id in seen_ids_local:
                continue
            seen_ids_local.add(item_id)
            url = "https://jp.mercari.com" + href

            # 標題
            title = None
            try:
                img = a.locator("img").first
                alt = img.get_attribute("alt")
                if alt:
                    title = alt.strip()
            except Exception:
                pass
            if not title:
                text_block = (a.inner_text() or "").strip()
                if not text_block:
                    parent = a
                    for _ in range(2):
                        parent = parent.evaluate_handle("el => el.parentElement")
                        el = parent.as_element() if parent else None
                        if el:
                            text_block = (el.inner_text() or "").strip()
                            if text_block:
                                break
                for piece in text_block.split("\n"):
                    t = piece.strip()
                    if 6 <= len(t) <= 120 and "￥" not in t and "¥" not in t:
                        title = t
                        break
            if not title:
                title = "ayur chair"

            price = parse_price((a.inner_text() or ""))
            items.append({"id": item_id, "title": title, "price": price, "url": url, "created": None})

        context.close()
        browser.close()
    return items

# -------- ② 後備：純 HTML（非 JS）--------
def fetch_list_html():
    try:
        r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        anchors = soup.select('a[href^="/item/"]')
        items, seen_ids_local = [], set()
        for a in anchors:
            href = a.get("href") or ""
            if not href.startswith("/item/"):
                continue
            item_id = href.rsplit("/", 1)[-1]
            if item_id in seen_ids_local:
                continue
            seen_ids_local.add(item_id)
            url = "https://jp.mercari.com" + href
            title = (a.get_text(" ", strip=True) or "ayur chair")
            price = parse_price(a.get_text(" ", strip=True))
            items.append({"id": item_id, "title": title, "price": price, "url": url, "created": None})
        return items
    except Exception:
        return []

# -------- 詳情頁取上架時間（JST）--------
def fetch_date(item_url: str, created_iso: str | None) -> str:
    if created_iso:
        try:
            iso = created_iso.replace("Z", "+00:00")
            dt_utc = datetime.datetime.fromisoformat(iso)
            jst = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            return jst.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
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

    items = fetch_list_playwright()
    if not items:
        print("DEBUG: Playwright 無結果，改用 HTML 後備")
        items = fetch_list_html()

    print(f"DEBUG: 列表抓到 {len(items)} 件")

    new_items = [it for it in items if it["id"] not in seen and match_filters(it["title"], it["price"])]
    print(f"DEBUG: new_items={len(new_items)} / seen_ids={len(seen)}")

    messages = []

    if new_items:
        msg = "🆕 Mercari 新上架 ayur chair（由新至舊）\n\n"
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
