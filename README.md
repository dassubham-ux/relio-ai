# Relio AI

A 3-agent Reddit marketing pipeline that researches any company and surfaces the best subreddits for authentic B2B engagement.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Agent 1 — Research                      │
│                                                                 │
│   Web Scrape (httpx)  →  Gemini + Google Search  →  CompanyBrief│
│   (homepage, /pricing,    (competitors, ICP,          (JSON,    │
│    /about, /blog, /docs)   pain points, keywords)    MongoDB)   │
└───────────────────────────────────┬─────────────────────────────┘
                                    │ CompanyBrief
┌───────────────────────────────────▼─────────────────────────────┐
│                         Agent 2 — Subreddit Finder              │
│                                                                 │
│   Gemini Discovery  →  PRAW Enrichment  →  Gemini Structuring   │
│   (Google Search)      (subscribers,        SubredditMap        │
│                         rules, desc)         (JSON, MongoDB)    │
└───────────────────────────────────┬─────────────────────────────┘
                                    │ SubredditMap
┌───────────────────────────────────▼─────────────────────────────┐
│                    Agent 3 — Content Generator (coming soon)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start — Docker

```bash
# 1. Copy and fill in your API keys
cp .env.example .env
# Edit .env: add GEMINI_API_KEY, REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET

# 2. Build and start (MongoDB + API server)
docker compose up --build

# 3. Health check
curl http://localhost:8000/health

# 4. Start a research job (Agent 1)
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"url": "https://stripe.com"}'
# → {"job_id": "abc123", "status": "queued"}

# 5. Poll until completed (jobs take 1–3 min)
curl http://localhost:8000/api/v1/jobs/abc123

# 6. Retrieve the saved brief
curl http://localhost:8000/api/v1/briefs/stripe.com
```

---

## Quick Start — Local

```bash
# 1. Create and activate virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your keys

# 4. Start MongoDB (must be running locally)
mongod --dbpath /data/db

# 5. Start the API server
uvicorn api.app:app --reload --port 8000
```

---

## ngrok Setup

ngrok exposes your local API to the internet with a public HTTPS URL.

```bash
# Add your token to .env
echo "NGROK_AUTHTOKEN=your_token_here" >> .env

# Start with ngrok profile
docker compose --profile ngrok up --build

# Check the public URL
curl http://localhost:4040/api/tunnels
# Look for "public_url" in the response
```

The ngrok web UI is available at http://localhost:4040.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server + MongoDB status |
| POST | `/api/v1/research` | Start Agent 1 (Company Deep Research) |
| POST | `/api/v1/subreddits` | Start Agent 2 (Subreddit Finder) |
| GET | `/api/v1/jobs/{job_id}` | Poll job status / result |
| GET | `/api/v1/briefs/{domain}` | Fetch CompanyBrief from MongoDB |
| GET | `/api/v1/subreddits/{domain}` | Fetch SubredditMap from MongoDB |

### POST `/api/v1/research`

```bash
curl -X POST http://localhost:8000/api/v1/research \
  -H "Content-Type: application/json" \
  -d '{"url": "https://stripe.com"}'
```

Body: `{"url": "https://stripe.com", "paste_text": null}`

Response `202`: `{"job_id": "abc123", "status": "queued"}`

Use `paste_text` to skip web scraping and supply your own company description.

### POST `/api/v1/subreddits`

Requires a CompanyBrief to already exist for the domain (run research first).

```bash
curl -X POST http://localhost:8000/api/v1/subreddits \
  -H "Content-Type: application/json" \
  -d '{"domain": "stripe.com"}'
```

Response `202`: `{"job_id": "def456", "status": "queued"}`

### GET `/api/v1/jobs/{job_id}`

```bash
curl http://localhost:8000/api/v1/jobs/abc123
```

Response:
```json
{
  "job_id": "abc123",
  "status": "completed",
  "result": { ... },
  "error": null
}
```

`status` is one of: `queued` | `running` | `completed` | `failed`

### GET `/api/v1/briefs/{domain}`

```bash
curl http://localhost:8000/api/v1/briefs/stripe.com
```

Returns the full `CompanyBrief` JSON stored in MongoDB. `404` if not found.

### GET `/api/v1/subreddits/{domain}`

```bash
curl http://localhost:8000/api/v1/subreddits/stripe.com
```

Returns the full `SubredditMap` JSON stored in MongoDB. `404` if not found.

---

## CLI Usage

The original CLI tools still work alongside the API:

```bash
# Agent 1 — Company Deep Research
python3 main.py --url "https://stripe.com"
python3 main.py --url "https://stripe.com" --output custom.json
python3 main.py --paste-text "Stripe is a payments platform..." --url "https://stripe.com"

# Agent 2 — Subreddit Finder
python3 main2.py --domain stripe.com
```

Output is saved to `output/<domain>.json` by default.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `REDDIT_CLIENT_ID` | Yes (Agent 2) | Reddit script app client ID |
| `REDDIT_CLIENT_SECRET` | Yes (Agent 2) | Reddit script app client secret |
| `MONGO_URI` | No | MongoDB connection string (default: `mongodb://localhost:27017`) |
| `NGROK_AUTHTOKEN` | No | ngrok auth token (only for `--profile ngrok`) |

To get API keys:
- **Gemini**: https://aistudio.google.com/app/apikey
- **Reddit**: Create a "script" app at https://www.reddit.com/prefs/apps
- **ngrok**: https://dashboard.ngrok.com/get-started/your-authtoken

---

## Project Structure

```
relio-ai/
├── agent/
│   ├── fetcher.py       Phase 1: scrape homepage + /pricing /about /blog /docs
│   ├── researcher.py    Phase 2: Gemini + Search grounding; Phase 3: structured JSON
│   ├── finder.py        Agent 2: Gemini discovery + PRAW enrichment + structuring
│   ├── storage.py       MongoDB upsert helpers (company_briefs, subreddit_maps)
│   ├── models.py        Pydantic v2 schemas (CompanyBrief, SubredditMap, …)
│   └── __init__.py
├── api/
│   ├── app.py           FastAPI application (async job runner, 6 endpoints)
│   └── __init__.py
├── main.py              CLI for Agent 1
├── main2.py             CLI for Agent 2
├── Dockerfile           python:3.12-slim, uvicorn entrypoint
├── docker-compose.yml   mongo + app + ngrok (optional profile)
├── requirements.txt
├── .env.example         Template for all environment variables
└── output/              JSON output from CLI runs
```
