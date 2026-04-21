# web_app_hub

一個自用型 Python + SQLite Web Hub，包含小工具、帳戶設定、歷史紀錄，以及一套私密手機閱讀系統。

目前核心目標是：程式碼可推到 GitHub，私有資料留在本機或伺服器；AI 評分與作品資料可離線在本地模型上長時間批量生成，完成後再打包部署。

## 功能

- 舒爾特方格：練習、成績紀錄、趨勢圖。
- MBTI 測試：題目流程、結果保存。
- 私密閱讀系統：獨立密碼入口、搜索、標籤篩選、推薦排序、關聯推薦、評分排序、手機滑動閱讀、進度保存。
- 本地 AI 書庫分析：生成無劇透簡介、分類、標籤、總分與細分評分。
- 輕量使用者管理：名稱、密碼、生日等基礎設定。
- SQLite 持久化：單機/小規模自用部署優先。

## 目錄

```text
web_app_hub/
├── server.py                         # Web 後端與 API
├── reader_core.py                    # 書庫掃描、索引、推薦排序、正文讀取
├── reader_ai.py                      # 單本作品本地 AI 結構化分析
├── reader_ai_batch.py                # 批量 AI 分析與落庫
├── launch_reader_ai_batch.sh         # screen + caffeinate 後台長跑啟動器
├── static/
│   ├── index.html                    # Hub 首頁
│   ├── schulte.html                  # 舒爾特方格
│   ├── mbti.html                     # MBTI 測試
│   ├── reader.html                   # 手機優先閱讀前端
│   └── settings.html                 # 帳戶與閱讀系統設定
├── docs/
│   └── reader_system_architecture.md # 閱讀系統架構與部署說明
├── data/
│   └── hub.db                        # 本地 SQLite DB，不提交 Git
└── writer/                           # 本地作品庫，不提交 Git
```

## 啟動

```bash
python3 server.py
```

預設監聽：

```text
http://127.0.0.1:8777/
```

首次啟動會自動初始化 SQLite 資料庫、基礎 app 設定與測試帳號。

預設測試帳號：

```text
名稱：測試帳號
密碼：onelun
```

建議部署前修改或刪除預設帳號。

## 閱讀系統

入口：

```text
/apps/reader
```

預設閱讀入口密碼：

```text
reader888
```

閱讀系統會掃描 `writer/` 下的本地 `.txt` 作品，建立 `reader_works` 索引。前端主要針對手機瀏覽器設計，支援：

- 按作品名、作者、簡介、標籤搜索。
- 按推薦、作品與標籤契合度、關聯度、作品評分、最近更新排序。
- 詳情頁展示 `summary` 長簡介、標籤、評分拆解與推薦理由；列表與關聯推薦卡片使用較短的 `intro`。
- 正文區滑動閱讀，保存閱讀進度與上次位置。
- 以當前作品為 anchor 查找相似作品。

## 本地 AI

預設使用 OpenAI-compatible 本地模型服務：

```text
URL: http://127.0.0.1:8000/v1
Model: Qwen3.6-35B-A3B-4bit
Token/API key: 在 /settings 補入
```

`reader_ai.py` 會：

- 自動正規化 `/v1` URL。
- 讀取 `/models`，對模型名做 fuzzy match。
- 使用 `chat_template_kwargs.enable_thinking=false` 關閉 thinking。
- 要求 JSON object 輸出。
- 對簡介做無劇透清洗，避免結局、真相、死亡、身份揭曉、懷孕、車禍等中後段劇情外洩。

## AI 批處理

推薦的低成本快跑：

```bash
./launch_reader_ai_batch.sh --session reader_ai_synopsis_long_20260422 \
  --run-dir data/reader_ai_runs/synopsis_weighted_longsummary_20260422 -- \
  --mode auto \
  --whole-char-limit 12000 \
  --spread-chunk-count 4 \
  --spread-chunk-char-limit 1200 \
  --sample-profile weighted \
  --timeout 120 \
  --retry-count 1 \
  --quality-tier low \
  --quality-preset synopsis-weighted-longsummary-4x1200
```

策略說明：

- 短文小於 `12000` 字時整本輸入。
- 長文使用 `weighted` 取樣：開頭少量、中段更多、高潮前段少量，避開真正結尾。
- 若原文開頭已有 `內容簡介`、`作品簡介`、`文案`，Python 會先清洗並直接用作長簡介，模型只生成分類、標籤、評分與推薦理由。
- 若沒有原文簡介，模型生成一段較完整的無劇透詳情簡介和一段較短的卡片介紹。
- 結果標記為 `analysis_quality=low`，之後可以按低質量批次重跑。

補高質量示例：

```bash
python3 reader_ai_batch.py \
  --only-quality-tier low \
  --mode auto \
  --whole-char-limit 30000 \
  --spread-chunk-count 5 \
  --spread-chunk-char-limit 4500 \
  --sample-profile segmented \
  --quality-tier high \
  --quality-preset high-segmented-5x4500
```

已跑過的資料若後來改進了原文簡介清洗，可以不用重新調模型，直接用 backfill 腳本掃描原文並替換 `summary` / `intro`：

```bash
python3 reader_synopsis_backfill.py --status done --dry-run
python3 reader_synopsis_backfill.py --status done
```

這個腳本只處理非 `running` 行，會把可提取的原文簡介標記為 `analysis_summary_source=source_synopsis`，並把報告寫到 `data/reader_synopsis_reports/`。

## 資料邊界

GitHub 保存：

```text
server.py
reader_core.py
reader_ai.py
reader_ai_batch.py
reader_synopsis_backfill.py
launch_reader_ai_batch.sh
static/
docs/
README.md
```

不提交 Git：

```text
data/hub.db
data/reader_ai_runs/
data/reader_ai_records/
data/reader_synopsis_reports/
writer/
.env
*.log
```

原因是 `hub.db`、AI 輸出紀錄和 `writer/` 作品庫屬於私有資料或大文件，應只在本機/伺服器部署包中存在。

## 部署

部署到伺服器時至少需要：

```text
應用程式碼
static/
docs/
data/hub.db
writer/
```

如果伺服器只需要使用已生成的推薦資料，不需要本地 AI；如果要刷新單本 AI 評分或繼續批量分析，伺服器也需要可用的 OpenAI-compatible 本地模型服務。

詳細架構與打包流程見：[docs/reader_system_architecture.md](docs/reader_system_architecture.md)。

## License

暫未指定。
