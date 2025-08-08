
# Mercari "ayur chair" Watcher (GitHub Actions + Telegram)

功能：每 30 分鐘抓 Mercari 搜尋結果，發現新上架（去重）就用 Telegram 通知。

預設搜尋：
```
https://jp.mercari.com/zh-TW/search?keyword=ayur%20chair&order=desc&sort=created_time
```

## 一鍵使用步驟

1. 新建 GitHub Repo，將本專案內容上傳。
2. 到 **Settings → Secrets and variables → Actions → New repository secret**，新增：
   - `TELEGRAM_BOT_TOKEN`：在 Telegram @BotFather 建 bot 取得
   - `TELEGRAM_CHAT_ID`：給你的 bot 發一個訊息後，瀏覽：`https://api.telegram.org/bot<TOKEN>/getUpdates` 找 `chat.id`
   - 可選 `SEARCH_URL`（若你想改為別的搜尋連結）
   - 可選 `COLOR_KEYWORDS`（例：`white,白,黑,black`；用來過濾標題）
   - 可選 `MIN_PRICE` / `MAX_PRICE`（日圓，0 表示不限制）
3. 首次手動觸發：到 **Actions** → `Mercari Watch` → `Run workflow`。
   - 建議先跑一次建立 `data/seen_ids.json`，看看 log，確認正常。
4. 之後會依照 cron `*/30 * * * *` 每 30 分鐘自動執行。

## 調整頻率
在 `.github/workflows/watch.yml` 裡改 `cron`。例：每小時一次 → `0 * * * *`。

## 注意事項
- Mercari 前端可能變動，如抓不到標題/價錢，訊息仍會包含連結；可再調整 `watcher.py` 的解析邏輯。
- 請勿把 TOKEN/CHAT_ID 寫死在程式碼，務必使用 GitHub Secrets。
- 第一次大量歷史商品：建議先把 `send_telegram` 的呼叫註解，跑一次建立「已看清單」，避免狂推送。

