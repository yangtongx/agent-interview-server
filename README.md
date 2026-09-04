# 阿里 AI 面试官 · 服务端

独立的 FastAPI 服务：题库 PostgreSQL、点评、通义 TTS / ASR。默认端口 **43180**。

配套目录（都在 `D:\work` 下，互不嵌套）：

- 网页：`ali-agent-interview`
- 本仓库：`ali-agent-interview-server`
- 鸿蒙：`ali-agent-interview-harmony`

## 接口

- `GET /api/bank`
- `GET|POST /api/judge`
- `POST /api/tts`
- `POST /api/transcribe`

若目录里有网页静态导出 `out/`（来自网页仓库 `npm run build`），本服务会一并托管。

## 本地运行

需要 Python 3.12+、可连上的 PostgreSQL。展开题库需要 Node.js 20+（仅 seed 时用）。

```bash
cp .env.example .env
# 填写 PG_URI，以及可选的 LLM_API_KEY
python -m pip install -r server/requirements.txt
npm install
npm run seed
python -m server
```

浏览器或鸿蒙访问 [http://127.0.0.1:43180](http://127.0.0.1:43180)。

改 `data/*.json` 后重新 `npm run seed`，接口才会更新。

## Docker

先配置 `.env`（至少 `PG_URI`）：

```bash
docker compose up -d --build
```

默认映射主机 **80** → 容器 43180。改端口：`PUBLISH_PORT=8080 docker compose up -d --build`。

## 环境变量

见 `.env.example`。不要把真实 `PG_URI` 或 API Key 提交进 git。
