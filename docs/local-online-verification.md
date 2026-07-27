# 本機線上對照驗證

## 目的

驗證未點入群組時，監控程式是否能取得自然發生的新訊息完整內文。只有通過本文件的驗收條件，程式才可部署至遠端主機。

## 本機準備

1. 將 `config.local.template.json` 複製為 `config.json`。
2. 在本機 `config.json` 填入登入資料。不要提交此檔案。
3. 保持 `browser.headless` 為 `false`，並設定 `watch.out_dir` 為 `local_verification`。
4. 若要驗證 Telegram，填入本機 Telegram 設定並把 `enabled` 改為 `true`；否則先以 CSV 對照為主。

## 執行

以本機 Python 執行 `fpc_watch_ui_login_telegram_v2026.07.27.4.py`，在可見瀏覽器完成登入。啟動監控後，至少持續 24 小時或直到實際群組自然出現一則新訊息。

## 對照流程

1. 新訊息抵達時，不要點入該群組。
2. 確認 `local_verification/system_YYYYMMDD.log` 出現版本、網路處理及 `[CSV] append` 訊號。
3. 確認 `local_verification/messages_YYYYMMDD.csv` 的內文不含側欄的省略號截斷。
4. 如啟用 Telegram，確認收到相同完整內文。
5. 完成上述紀錄後，才可點入該群組，將畫面中的最後一則完整訊息與 CSV／Telegram 逐字比對。

## 通過與失敗

- 通過：未點入狀態即已寫入完整內文，且之後點入畫面的內容逐字相同。
- 失敗：出現 `truncated preview withheld`、沒有 `[CSV] append`、或 CSV／Telegram 內容與點入後畫面不同。請保留 `system`、`network`、`message_envelopes` 三種日誌供重播分析。
