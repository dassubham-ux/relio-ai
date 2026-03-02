# Relio AI — Agent 1: Company Deep Research

## Purpose
Takes a company URL, fetches its web pages, uses Gemini with Google Search grounding,
and produces a fixed JSON schema consumed by Agent 2 (Subreddit Finder) and Agent 3 (Content Generator).

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env
# Add your GEMINI_API_KEY to .env
```

## Usage
```bash
python main.py --url "https://stripe.com"
python main.py --url "https://stripe.com" --output custom.json
python main.py --paste-text "Stripe is a payments platform..." --url "https://stripe.com"
```

## Output
JSON saved to `output/<domain>.json` by default.

## Acceptance Criteria
- 15+ keywords total across all keyword_map categories
- 5+ competitors in competitor_set
- ICP populated with roles, industries, company_size, geography

## Architecture
- `agent/fetcher.py` — Phase 1: scrapes homepage + /pricing /about /blog /docs
- `agent/researcher.py` — Phase 2: Gemini + Search grounding; Phase 3: structured JSON
- `agent/models.py` — Pydantic v2 schemas (stable contract for downstream agents)
- `main.py` — CLI orchestration

## Model
- `gemini-2.0-flash` with Google Search grounding tool
- Temperature 0.0 for deterministic structured output
