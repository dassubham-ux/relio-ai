from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from google import genai
from google.genai import types

from agent.models import CompanyBrief

MODEL = "gemini-2.5-flash"


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Phase 2: Gemini + Google Search grounding
# ---------------------------------------------------------------------------

RESEARCH_PROMPT = """\
You are a B2B market research analyst. Research the company at: {url}

I have also scraped their website. Here is the content:
--- WEBSITE CONTENT START ---
{website_text}
--- WEBSITE CONTENT END ---

Using Google Search, find and summarize:
1. Who are the 5–15 direct and adjacent competitors? Include their website URLs.
2. What do customers say on G2, Capterra, Reddit, or review sites? What pain points do they mention?
3. What job postings does this company or their customers post? What roles signal their ICP?
4. What is the pricing model and target company size?
5. What are 3 clear differentiators vs competitors?
6. What marketing language should be AVOIDED on Reddit (overly salesy, buzzwords)?

Be specific. Name real competitor products with URLs. Quote real review language when possible.
"""


def run_research_phase(client: genai.Client, url: str, website_text: str) -> tuple[str, list[str]]:
    """
    Phase 2: Run Gemini with Google Search grounding.
    Returns (research_text, source_urls).
    """
    prompt = RESEARCH_PROMPT.format(url=url, website_text=website_text)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.3,
        ),
    )

    research_text = response.text or ""

    # Extract grounding source URLs
    sources: list[str] = []
    try:
        for candidate in response.candidates or []:
            grounding = getattr(candidate, "grounding_metadata", None)
            if grounding:
                for chunk in getattr(grounding, "grounding_chunks", []) or []:
                    web = getattr(chunk, "web", None)
                    if web and getattr(web, "uri", None):
                        sources.append(web.uri)
    except Exception:
        pass

    return research_text, sources


# ---------------------------------------------------------------------------
# Phase 3: Structured JSON output
# ---------------------------------------------------------------------------

STRUCTURE_PROMPT = """\
You are a JSON data extraction specialist. Based on the website content and market research below,
produce a structured JSON object matching the schema exactly.

Company URL: {url}

--- WEBSITE CONTENT ---
{website_text}

--- MARKET RESEARCH (with competitor data, reviews, ICP signals) ---
{research_text}

Rules:
- company_snapshot.what_it_does: exactly 1–2 sentences, factual
- icp.roles: 3–6 specific job titles (e.g. "VP of Sales", not "executives")
- icp.industries: 2–5 industries
- icp.company_size: employee range like "50–500 employees"
- icp.geography: primary geographic markets
- primary_use_cases: 3–5 concrete use cases
- core_pain_points_solved: 5 specific pain points, written as problems customers face
- differentiators: 3 specific advantages over competitors (not generic marketing speak)
- reddit_safe_description: describe the product as if recommending it to a friend on Reddit — no jargon
- red_flags: 3–5 phrases/claims to avoid (hype words, unverifiable superlatives)
- keyword_map.problem_keywords: 4–5 pain-driven search terms someone types when frustrated
- keyword_map.workflow_keywords: 4–5 "how do I…" style queries
- keyword_map.tool_comparison_keywords: 4–5 "X vs Y" or "alternatives to X" queries
- keyword_map.competitor_keywords: 4–5 competitor names + category terms
- competitor_set: 5–15 competitors with accurate name, category, and URL
- metadata.url: the company URL provided
- metadata.researched_at: current ISO timestamp
- metadata.sources: list of URLs used in research
"""


def run_structuring_phase(
    client: genai.Client,
    url: str,
    website_text: str,
    research_text: str,
    sources: list[str],
) -> CompanyBrief:
    """
    Phase 3: Convert all gathered text into a validated CompanyBrief.
    Uses JSON mode + Pydantic schema for deterministic output.
    """
    prompt = STRUCTURE_PROMPT.format(
        url=url,
        website_text=website_text,
        research_text=research_text,
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CompanyBrief,
            temperature=0.0,
        ),
    )

    raw = response.text or "{}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}\n\nRaw response:\n{raw[:500]}") from exc

    # Inject metadata that Gemini can't know
    data.setdefault("metadata", {})
    data["metadata"]["url"] = url
    data["metadata"]["researched_at"] = datetime.now(timezone.utc).isoformat()
    if sources:
        existing = data["metadata"].get("sources", [])
        data["metadata"]["sources"] = list(dict.fromkeys(existing + sources))

    return CompanyBrief.model_validate(data)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def research_company(url: str, website_text: str) -> CompanyBrief:
    """
    Run Phases 2 + 3 for a company and return a validated CompanyBrief.

    Args:
        url: The company's homepage URL (used for grounding context).
        website_text: Pre-scraped and combined text from Phase 1 (or paste-text fallback).
    """
    client = _get_client()

    print("[Phase 2] Running Gemini research with Google Search grounding…")
    research_text, sources = run_research_phase(client, url, website_text)
    print(f"[Phase 2] Done. Got {len(research_text)} chars, {len(sources)} source URLs.")

    print("[Phase 3] Structuring into CompanyBrief JSON…")
    brief = run_structuring_phase(client, url, website_text, research_text, sources)
    print("[Phase 3] Done.")

    return brief
