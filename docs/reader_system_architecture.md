# 私密閱讀系統架構說明

## 目標

這套閱讀系統是 `web_app_hub` 的第三個 app，入口為 `/apps/reader`。它面向手機瀏覽器，提供私密密碼入口、作品搜索、標籤/評分/關聯推薦、滑動閱讀、閱讀進度保存，以及本地 AI 生成的無劇透簡介、分類、標籤與評分。

系統設計時假設作品原文與 AI 產物都在本機或內網，斷網時仍可繼續跑批處理。GitHub 只保存程式與文檔，不保存 `writer/` 原始書庫、SQLite DB 或 AI 生成資料。

## 模組

```text
server.py
  Web 後端與 API 路由，負責 app 入口、閱讀 API、設定 API、AI 單本刷新。

reader_core.py
  閱讀資料核心：掃描 writer/、建立 reader_works 索引、讀取正文、推薦排序、資料轉換。

reader_ai.py
  單本作品 AI 分析腳本：調用本地 OpenAI-compatible API，輸出 JSON 結構化結果。

reader_ai_batch.py
  批量 AI 分析腳本：選取待處理作品、調用 reader_ai、落庫、保存 JSON/JSONL 紀錄。

launch_reader_ai_batch.sh
  macOS 後台啟動器：用 screen + caffeinate 長時間跑批處理，避免電腦休眠。

static/reader.html
  手機優先的閱讀前端：解鎖、搜索、排序、詳情、正文滑動閱讀、進度保存。

static/settings.html
  普通帳戶設定與閱讀系統設定：閱讀密碼、本地 AI URL、模型、Token。
```

## 主要資料表

`reader_works`

保存每部作品的索引、簡介、標籤、分類、AI 評分與批處理狀態。重要欄位：

- `relpath`：相對於倉庫根目錄的作品文件路徑。
- `title` / `author`：作品展示與搜索。
- `summary` / `intro`：AI 生成的無劇透簡介與卡片描述。
- `tags_json` / `categories_json` / `primary_category`：推薦與篩選依據。
- `ai_score`：總評分。
- `ai_metrics_json`：細分評分與批處理元資料，例如 `analysis_quality`、`analysis_preset`、`analysis_source_char_count`。
- `ai_status`：`pending`、`running`、`done`、`failed`。

`reader_reads`

保存閱讀進度與打開次數，用於續讀和人氣排序。

`app_settings`

保存閱讀入口密碼 hash、本地 AI URL、模型與 token。

## AI 分析流程

批處理入口：

```bash
./launch_reader_ai_batch.sh --session reader_ai_synopsis_weighted_20260422 \
  --run-dir data/reader_ai_runs/synopsis_weighted_20260422 -- \
  --mode auto \
  --whole-char-limit 12000 \
  --spread-chunk-count 4 \
  --spread-chunk-char-limit 1200 \
  --sample-profile weighted \
  --timeout 90 \
  --retry-count 1 \
  --quality-tier low \
  --quality-preset synopsis-weighted-4x1200
```

目前快跑策略：

- 短文小於 `12000` 字時整本輸入。
- 長文使用 `weighted` 取樣：開頭與高潮前段少量取樣，中段與核心衝突給更多字數。
- `4x1200` 的總輸入約 `4800` 字，典型窗口為 `2%`、`42%`、`62%`、`82%`。
- 也保留 `segmented` 取樣：按章節長度等距分段取固定窗口，適合之後補高質量資料。
- 不取 `92%` 之後，降低結局、番外、作者後記干擾。
- 若原文開頭已有 `內容簡介`、`作品簡介`、`文案` 等簡介欄，Python 會先清洗並直接寫入 `summary` / `intro`；模型只輸出分類、標籤、評分與推薦理由。
- 若沒有原文簡介，模型才生成無劇透 `summary` / `intro`。
- 生成後寫入 `analysis_quality=low`、`analysis_sample_profile=weighted`、`analysis_summary_source`，之後可專門重跑低質量結果。

無劇透策略：

- prompt 明確禁止暴露結局、真相、最終選擇、身份揭曉、死亡、最終配對。
- `reader_ai.py` 會對輸出做分句清洗，刪除含 `最後`、`最終`、`真相`、`原來`、`早已` 等劇透信號的分句。
- 輸出會安全截句，避免卡片簡介停在半句。

重跑低質量結果：

```bash
python3 reader_ai_batch.py \
  --only-quality-tier low \
  --mode auto \
  --whole-char-limit 30000 \
  --spread-chunk-count 5 \
  --spread-chunk-char-limit 4500 \
  --quality-tier high \
  --quality-preset high-5x4500
```

## 本地 AI 設定

預設使用 OpenAI-compatible API：

```text
URL: http://127.0.0.1:8000/v1
Model: Qwen3.6-35B-A3B-4bit
Token/API key: 在 /settings 設定
```

`reader_ai.py` 會自動：

- 正規化 `/v1` URL。
- 讀取 `/models` 並做模型名稱 fuzzy match。
- 使用 `chat_template_kwargs.enable_thinking=false` 關閉 thinking。
- 要求 `response_format={"type":"json_object"}`。

## 推薦與排序

前端支援以下排序模式：

- `recommended`：綜合 AI/規則分、標籤契合、人氣、閱讀進度。
- `match`：作品與所選標籤的契合度。
- `related`：以某部作品為 anchor，按作者、標籤、分類、標題相似度排序。
- `score`：按 `ai_score` 或規則分排序。
- `recent`：按更新時間排序。

## 部署打包

GitHub 只保存程式。等 AI 跑完，要部署到伺服器時，需要額外打包資料：

```text
server.py
reader_core.py
reader_ai.py
reader_ai_batch.py
launch_reader_ai_batch.sh
static/
data/hub.db
writer/
```

可選打包：

```text
data/reader_ai_records/
data/reader_ai_runs/
```

其中 `data/hub.db` 是核心資料庫，包含索引、AI 結果、設定、閱讀進度。`writer/` 是原始作品庫，若伺服器不需要重新掃描，也可以只部署 DB；但要支持正文閱讀，仍需要作品原文路徑與 DB 中 `relpath` 對得上。

## 伺服器接入步驟

1. 部署程式碼。
2. 放入 `data/hub.db`。
3. 放入 `writer/`，保持相對路徑不變。
4. 啟動：

```bash
python3 server.py
```

5. 打開 `/settings` 設定閱讀密碼與本地 AI 參數。
6. 打開 `/apps/reader` 驗證搜索、詳情、正文、進度保存。

如果伺服器沒有本地 AI，已生成的 `summary`、`tags_json`、`ai_score` 仍可直接使用；只是不再刷新 AI 評分。
