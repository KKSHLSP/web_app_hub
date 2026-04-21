# web_app_hub

一个轻量的多用户 Web Hub，基于 Python + SQLite。

当前包含：
- 舒爾特方格
- MBTI 測試
- 私密閱讀系統（搜索 / 標籤推薦 / 手機滑動閱讀 / 本地 AI 評分）
- 輕量使用者管理（可選密碼、生日）
- 歷史記錄與趨勢圖

## 特性
- 單文件後端：`server.py`
- SQLite 持久化
- 靜態頁面前端
- 適合小規模朋友/自用場景
- 可繼續擴展更多 app / 工具
- 自動掃描 `writer/` 本地作品庫建立閱讀索引
- 閱讀系統具備獨立入口密碼與本地 LM Studio 配置

## 結構
```text
web_hub/
├── server.py
├── reader_core.py
├── reader_ai.py
├── static/
│   ├── index.html
│   ├── schulte.html
│   ├── mbti.html
│   ├── reader.html
│   └── settings.html
└── data/
    └── .gitkeep
```

## 啟動
```bash
python3 server.py
```

預設監聽：
- `0.0.0.0:8777`

打開：
- `http://127.0.0.1:8777/`

## 說明
首次啟動會自動初始化 SQLite 資料庫與測試帳號。

預設測試帳號：
- 名稱：`測試帳號`
- 密碼：`onelun`

閱讀系統預設入口密碼：
- `reader888`

閱讀系統預設本地 AI 設定：
- URL：`http://127.0.0.1:8000/v1`
- Model：`Qwen3.6-35B-A3B-4bit`
- Token：需自行在 `/settings` 補入本地 API key / Bearer token

詳細架構與部署打包流程見：[`docs/reader_system_architecture.md`](docs/reader_system_architecture.md)

> 建議自行修改或刪除預設測試帳號。

## 後續擴展方向
- 文檔工作台
- 更多小遊戲 / 工具
- 更完整的登入態 / session
- HTTPS / 反向代理
- 上傳文件工作區

## License
暫未指定，可按你的需求再補。
