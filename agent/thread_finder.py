from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import requests
from google import genai
from google.genai import types

from agent.models import (
    CompanyBrief,
    RedditThread,
    ThreadSearchMetadata,
    ThreadSearchResult,
    _ThreadRankingList,
)

MODEL = "gemini-2.5-flash"


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "RelioBot/1.0"})
    return session


# ---------------------------------------------------------------------------
# Phase 1: Reddit public search API
# ---------------------------------------------------------------------------

def _search_threads(
    session: requests.Session,
    subreddit: str,
    queries: list[str],
    limit_per_query: int = 15,
) -> list[dict]:
    """Search a subreddit for threads matching each query. Returns deduplicated raw post dicts."""
    seen: set[str] = set()
    results: list[dict] = []

    for query in queries:
        try:
            resp = session.get(
                f"https://www.reddit.com/r/{subreddit}/search.json",
                params={
                    "q": query,
                    "sort": "relevance",
                    "limit": limit_per_query,
                    "restrict_sr": "1",
                    "t": "year",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            for post in resp.json().get("data", {}).get("children", []):
                d = post.get("data", {})
                url = f"https://reddit.com{d.get('permalink', '')}"
                if url in seen:
                    continue
                seen.add(url)
                results.append({
                    "title": d.get("title", ""),
                    "url": url,
                    "subreddit": f"r/{d.get('subreddit', subreddit)}",
                    "score": d.get("score", 0) or 0,
                    "num_comments": d.get("num_comments", 0) or 0,
                    "created_utc": d.get("created_utc", 0),
                })
            time.sleep(0.5)
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Phase 2: Gemini semantic ranking
# ---------------------------------------------------------------------------

RANK_PROMPT = """\
You are a B2B sales intelligence analyst. From the Reddit threads below from r/{subreddit}, \
select and score the 10 most valuable ones for authentic engagement by a company selling to its ICP.

Company: {domain}
Product: {what_it_does}
ICP roles: {roles}
Key pain points: {pain_points}
Search keywords: {keywords}

Threads (index | title | upvotes | comments | url):
{threads_block}

For each of the top 10 threads return:
- url: exact URL from the list above
- relevance_score: 1–10 (10 = ICP member directly venting about a pain this product solves)
- relevance_reason: 1–2 sentences on the engagement opportunity
- opportunity_type: one of "pain_point" | "tool_comparison" | "workflow_question" | "competitor_mention" | "general_discussion"

Use only URLs from the list above. Return up to 10 (fewer if fewer exist).
"""


def _rank_threads(
    client: genai.Client,
    subreddit: str,
    domain: str,
    brief: CompanyBrief,
    raw: list[dict],
) -> _ThreadRankingList:
    snap = brief.company_snapshot
    km = brief.keyword_map

    threads_block = "\n".join(
        f"{i + 1} | {t['title']} | ↑{t['score']} | 💬{t['num_comments']} | {t['url']}"
        for i, t in enumerate(raw[:30])
    )

    prompt = RANK_PROMPT.format(
        subreddit=subreddit,
        domain=domain,
        what_it_does=snap.what_it_does,
        roles=", ".join(snap.icp.roles[:4]),
        pain_points=", ".join(snap.core_pain_points_solved[:4]),
        keywords=", ".join((km.problem_keywords + km.workflow_keywords)[:6]),
        threads_block=threads_block,
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_ThreadRankingList,
            temperature=0.0,
        ),
    )

    try:
        data = json.loads(response.text or "{}")
        return _ThreadRankingList.model_validate(data)
    except Exception:
        return _ThreadRankingList(threads=[])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def find_threads(brief: CompanyBrief, domain: str, subreddit_name: str) -> ThreadSearchResult:
    """
    Find and semantically rank the top 10 relevant Reddit threads in a subreddit.

    Args:
        brief: CompanyBrief from Agent 1.
        domain: Company domain, e.g. "stripe.com".
        subreddit_name: Subreddit name without r/ prefix, e.g. "entrepreneur".
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    session = _get_session()
    km = brief.keyword_map
    sub_normalized = f"r/{subreddit_name}"

    # Build search queries from keyword map
    queries = list(dict.fromkeys(
        km.problem_keywords[:3] +
        km.workflow_keywords[:2] +
        km.tool_comparison_keywords[:2] +
        km.competitor_keywords[:2]
    ))

    print(f"[Threads Phase 1] Searching {sub_normalized} with {len(queries)} queries…")
    raw = _search_threads(session, subreddit_name, queries)
    print(f"[Threads Phase 1] {len(raw)} candidate threads found.")

    if not raw:
        return ThreadSearchResult(
            domain=domain,
            subreddit=sub_normalized,
            threads=[],
            metadata=ThreadSearchMetadata(
                domain=domain,
                subreddit=sub_normalized,
                searched_at=datetime.now(timezone.utc).isoformat(),
                total_candidates=0,
            ),
        )

    print(f"[Threads Phase 2] Ranking with Gemini…")
    ranking = _rank_threads(client, subreddit_name, domain, brief, raw)
    print(f"[Threads Phase 2] {len(ranking.threads)} threads ranked.")

    url_map = {t["url"]: t for t in raw}
    threads: list[RedditThread] = []

    for ranked in ranking.threads:
        original = url_map.get(ranked.url)
        if not original:
            continue
        try:
            created_at = datetime.fromtimestamp(
                original.get("created_utc", 0), tz=timezone.utc
            ).isoformat()
        except Exception:
            created_at = ""

        threads.append(RedditThread(
            title=original["title"],
            url=ranked.url,
            subreddit=original["subreddit"],
            score=original["score"],
            num_comments=original["num_comments"],
            created_at=created_at,
            relevance_score=ranked.relevance_score,
            relevance_reason=ranked.relevance_reason,
            opportunity_type=ranked.opportunity_type,
        ))

    threads.sort(key=lambda t: t.relevance_score, reverse=True)

    return ThreadSearchResult(
        domain=domain,
        subreddit=sub_normalized,
        threads=threads[:10],
        metadata=ThreadSearchMetadata(
            domain=domain,
            subreddit=sub_normalized,
            searched_at=datetime.now(timezone.utc).isoformat(),
            total_candidates=len(raw),
        ),
    )
