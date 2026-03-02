from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import praw
from google import genai
from google.genai import types

from agent.models import CompanyBrief, SubredditMap

MODEL = "gemini-2.5-flash"


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return genai.Client(api_key=api_key)


def _get_reddit() -> praw.Reddit:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise EnvironmentError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set in .env. "
            "Create a Reddit 'script' app at https://www.reddit.com/prefs/apps"
        )
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent="RelioBot/1.0 (by /u/relio_ai_bot)",
        read_only=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_subreddit_names(text: str) -> list[str]:
    """Extract deduplicated subreddit names from text, order-preserving."""
    matches = re.findall(r"r/([A-Za-z0-9_]{2,21})", text)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        lower = m.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(m)
    return result


@dataclass
class PRAWSubredditData:
    name: str
    url: str
    subscribers: int
    public_description: str
    rules: list[str] = field(default_factory=list)
    accessible: bool = True


def _enrich_subreddit(reddit: praw.Reddit, name: str) -> PRAWSubredditData:
    """Fetch subreddit metadata via PRAW. Returns inaccessible record on any error."""
    try:
        results = reddit.subreddits.search_by_name(name, exact=True)
        if not results:
            return PRAWSubredditData(
                name=f"r/{name}", url="", subscribers=0,
                public_description="", rules=[], accessible=False,
            )

        sub = results[0]
        subscribers = sub.subscribers or 0
        public_description = (sub.public_description or "")[:500]

        rules: list[str] = []
        try:
            for rule in sub.rules:
                short_desc = (rule.short_name or rule.description or "")[:200].strip()
                if short_desc:
                    rules.append(short_desc)
                if len(rules) >= 10:
                    break
        except Exception:
            pass

        return PRAWSubredditData(
            name=f"r/{sub.display_name}",
            url=f"https://reddit.com/r/{sub.display_name}",
            subscribers=subscribers,
            public_description=public_description,
            rules=rules,
            accessible=True,
        )
    except Exception:
        return PRAWSubredditData(
            name=f"r/{name}", url="", subscribers=0,
            public_description="", rules=[], accessible=False,
        )


# ---------------------------------------------------------------------------
# Phase 2: Gemini Discovery
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """\
You are a Reddit community researcher helping find the best subreddits for authentic B2B engagement.

Company: {domain}
Product description: {what_it_does}

Target customer (ICP):
- Roles: {roles}
- Industries: {industries}
- Company size: {company_size}

Key pain points this product solves:
{pain_points}

Important keywords:
- Problem keywords: {problem_keywords}
- Workflow keywords: {workflow_keywords}
- Tool comparison keywords: {tool_comparison_keywords}
- Competitor keywords: {competitor_keywords}

Competitors: {competitors}

Using Google Search, find Reddit communities where this product's ICP actively participates. Specifically, search for:
1. site:reddit.com {domain} — find where people already discuss this product
2. site:reddit.com <pain_point> — find where ICP vents about these problems
3. site:reddit.com <competitor_name> — find competitor discussions
4. site:reddit.com <role> community — find professional communities for ICP roles
5. site:reddit.com <industry> subreddit — find industry-specific communities

For each subreddit you find, note:
- The subreddit name in r/name format
- Why ICP members would be there
- What content themes dominate
- Whether self-promotion is commonly tolerated

Aim to discover 15–25 relevant subreddits. Include both large (1M+) and niche (<50K) communities.
Return a comprehensive narrative with every subreddit mentioned in r/name format.
"""


def run_discovery_phase(
    client: genai.Client, brief: CompanyBrief, domain: str
) -> tuple[str, list[str]]:
    """
    Phase 2: Gemini + Google Search to discover relevant subreddits.
    Returns (discovery_text, source_urls).
    """
    snap = brief.company_snapshot
    km = brief.keyword_map
    competitors = ", ".join(c.name for c in brief.competitor_set[:8])

    prompt = DISCOVERY_PROMPT.format(
        domain=domain,
        what_it_does=snap.what_it_does,
        roles=", ".join(snap.icp.roles),
        industries=", ".join(snap.icp.industries),
        company_size=snap.icp.company_size,
        pain_points="\n".join(f"- {p}" for p in snap.core_pain_points_solved),
        problem_keywords=", ".join(km.problem_keywords),
        workflow_keywords=", ".join(km.workflow_keywords),
        tool_comparison_keywords=", ".join(km.tool_comparison_keywords),
        competitor_keywords=", ".join(km.competitor_keywords),
        competitors=competitors,
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.3,
        ),
    )

    discovery_text = response.text or ""

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

    return discovery_text, sources


# ---------------------------------------------------------------------------
# Phase 3: PRAW Enrichment
# ---------------------------------------------------------------------------

def run_enrichment_phase(
    reddit: praw.Reddit, discovery_text: str
) -> tuple[list[PRAWSubredditData], int]:
    """
    Phase 3: Extract subreddit names from discovery text, enrich via PRAW.
    Returns (accessible_subreddits, total_candidates_count).
    """
    candidates = _extract_subreddit_names(discovery_text)
    preview = candidates[:10]
    suffix = "..." if len(candidates) > 10 else ""
    print(f"[Phase 3] Discovered {len(candidates)} candidate subreddits: {preview}{suffix}")

    enriched: list[PRAWSubredditData] = []
    for name in candidates:
        data = _enrich_subreddit(reddit, name)
        if data.accessible:
            enriched.append(data)
            print(f"[Phase 3]   ✓ r/{name}: {data.subscribers:,} subscribers")
        else:
            print(f"[Phase 3]   ✗ r/{name}: inaccessible (private/banned/nonexistent)")

    return enriched, len(candidates)


def _format_praw_block(enriched: list[PRAWSubredditData]) -> str:
    """Format PRAW data as a readable text block for the structuring prompt."""
    lines = ["=== REAL SUBREDDIT DATA FROM PRAW ===\n"]
    for sub in enriched:
        lines.append(f"Subreddit: {sub.name}")
        lines.append(f"URL: {sub.url}")
        lines.append(f"Subscribers: {sub.subscribers:,}")
        desc = sub.public_description[:300] if sub.public_description else "N/A"
        lines.append(f"Description: {desc}")
        if sub.rules:
            lines.append("Rules:")
            for rule in sub.rules:
                lines.append(f"  - {rule}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 4: Gemini Structuring
# ---------------------------------------------------------------------------

STRUCTURE_PROMPT = """\
You are a Reddit marketing strategist. Based on real subreddit data and discovery research below,
produce a structured JSON ranking the best subreddits for authentic engagement.

Company domain: {domain}
Product: {what_it_does}
ICP:
- Roles: {roles}
- Industries: {industries}
- Pain points: {pain_points}

{praw_block}

=== DISCOVERY NARRATIVE (what Gemini found via Google Search) ===
{discovery_text}

Scoring rubric (relevance_score 1–10):
- 8–10: ICP actively asks pain-point questions here, high signal-to-noise
- 5–7: ICP present but subreddit is broader; relevant threads exist
- 1–4: Tangentially related; ICP is a minority of members

Instructions:
- Select the BEST 10–20 subreddits from the PRAW data above
- Use EXACT subscriber counts from the REAL SUBREDDIT DATA block — do not invent numbers
- For posting_rules, extract the 2–4 most marketer-relevant rules (self-promo policy, link rules)
- Set self_promo_allowed=true only if rules explicitly permit it or there is a weekly thread for it
- content_themes: 3–5 specific post formats/topics common in that sub
- engagement_level: "high" if >500K subs and active, "medium" for 50K–500K, "low" for <50K
- Sort by relevance_score descending
"""


def run_structuring_phase(
    client: genai.Client,
    brief: CompanyBrief,
    domain: str,
    enriched: list[PRAWSubredditData],
    discovery_text: str,
    sources: list[str],
    candidates_discovered: int,
) -> SubredditMap:
    """Phase 4: Convert PRAW data + discovery narrative into a validated SubredditMap."""
    snap = brief.company_snapshot
    praw_block = _format_praw_block(enriched)

    prompt = STRUCTURE_PROMPT.format(
        domain=domain,
        what_it_does=snap.what_it_does,
        roles=", ".join(snap.icp.roles),
        industries=", ".join(snap.icp.industries),
        pain_points=", ".join(snap.core_pain_points_solved[:5]),
        praw_block=praw_block,
        discovery_text=discovery_text[:3000],
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SubredditMap,
            temperature=0.0,
        ),
    )

    raw = response.text or "{}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Gemini returned invalid JSON: {exc}\n\nRaw response:\n{raw[:500]}"
        ) from exc

    # Inject metadata — Gemini can't know timestamps/counts
    data["metadata"] = {
        "domain": domain,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates_discovered": candidates_discovered,
        "candidates_enriched": len(enriched),
        "sources": list(dict.fromkeys(sources)),
    }

    # Safety net: sort by relevance_score descending
    if "subreddits" in data:
        data["subreddits"] = sorted(
            data["subreddits"],
            key=lambda s: s.get("relevance_score", 0),
            reverse=True,
        )

    return SubredditMap.model_validate(data)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def find_subreddits(brief: CompanyBrief, domain: str) -> SubredditMap:
    """
    Run Phases 2–4 to find and rank subreddits for a company.

    Args:
        brief: Validated CompanyBrief from Agent 1.
        domain: Company domain (e.g. "stripe.com").
    """
    client = _get_client()
    reddit = _get_reddit()

    print("[Phase 2] Running Gemini subreddit discovery with Google Search grounding…")
    discovery_text, sources = run_discovery_phase(client, brief, domain)
    print(f"[Phase 2] Done. Got {len(discovery_text)} chars, {len(sources)} source URLs.")

    print("[Phase 3] Enriching candidates via PRAW…")
    enriched, candidates_discovered = run_enrichment_phase(reddit, discovery_text)
    print(f"[Phase 3] Done. {len(enriched)} accessible subreddits from {candidates_discovered} candidates.")

    if len(enriched) < 10:
        print(f"[WARN] Only {len(enriched)} accessible subreddits found (target: 10+). Output may be sparse.")

    print("[Phase 4] Structuring into SubredditMap JSON…")
    subreddit_map = run_structuring_phase(
        client, brief, domain, enriched, discovery_text, sources, candidates_discovered,
    )
    print("[Phase 4] Done.")

    return subreddit_map
