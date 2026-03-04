from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ICP(BaseModel):
    roles: List[str] = Field(description="Job titles/roles of ideal customers, e.g. 'VP of Sales'")
    industries: List[str] = Field(description="Target industries, e.g. 'SaaS', 'B2B'")
    company_size: str = Field(description="Employee count range, e.g. '50–500 employees'")
    geography: str = Field(description="Target geographies, e.g. 'US, Europe'")


class CompanySnapshot(BaseModel):
    what_it_does: str = Field(description="1–2 sentence description of the product/service")
    one_liner: str = Field(description="Single punchy sentence the company uses to pitch itself")
    value_proposition: List[str] = Field(description="3–5 concrete statements of value the product creates for customers")
    icp: ICP = Field(description="Ideal customer profile")
    primary_use_cases: List[str] = Field(description="Top 3–5 primary use cases")
    core_pain_points_solved: List[str] = Field(description="Top 5 pain points the product solves")
    differentiators: List[str] = Field(description="Top 3–5 specific competitive differentiators")
    positioning: str = Field(description="2–3 sentences on how the company positions itself vs the competition")
    reddit_safe_description: str = Field(description="Non-salesy phrasing safe for Reddit communities")
    red_flags: List[str] = Field(description="Claims or language to avoid when discussing on Reddit")


class FirmographicData(BaseModel):
    industry: str = Field(description="The company's own industry/market, e.g. 'API security and AI application security'")
    founded: str = Field(description="Year founded, e.g. '2021'. Use 'Unknown' if not determinable.")
    employee_range: str = Field(description="Headcount range, e.g. '11–50', '51–200', '201–500'. Use 'Unknown' if not determinable.")
    funding: str = Field(description="Funding stage and total raised, e.g. 'Series A – $20.5M'. Use 'Bootstrapped' or 'Unknown' if not determinable.")
    headquarters: str = Field(description="City and country of HQ, e.g. 'San Francisco, USA'. Use 'Unknown' if not determinable.")


class MarketCategory(BaseModel):
    primary: str = Field(description="Primary market category, e.g. 'Runtime API security platform'")
    secondary: str = Field(description="Secondary market category or adjacent positioning, e.g. 'API observability and sensitive data protection'")


class KeywordMap(BaseModel):
    problem_keywords: List[str] = Field(description="Pain-driven search terms, 4–5 items")
    workflow_keywords: List[str] = Field(description="'How do I…' style workflow queries, 4–5 items")
    tool_comparison_keywords: List[str] = Field(description="'X vs Y' and 'alternatives' queries, 4–5 items")
    competitor_keywords: List[str] = Field(description="Competitor brand names and category terms, 4–5 items")


class Competitor(BaseModel):
    name: str = Field(description="Competitor product/company name")
    category: str = Field(description="Product category, e.g. 'Sales engagement'")
    url: str = Field(description="Competitor website URL")


class Metadata(BaseModel):
    url: str = Field(description="The researched company URL")
    researched_at: str = Field(description="ISO 8601 timestamp of when research was done")
    sources: List[str] = Field(description="URLs of sources used during research")


class CompanyBrief(BaseModel):
    company_snapshot: CompanySnapshot
    firmographic_data: FirmographicData
    market_category: MarketCategory
    keyword_map: KeywordMap
    competitor_set: List[Competitor] = Field(description="5–15 direct and adjacent competitors")
    metadata: Metadata


# ---------------------------------------------------------------------------
# Agent 2: Subreddit Finder models
# ---------------------------------------------------------------------------

class Subreddit(BaseModel):
    name: str                   # "r/entrepreneur"
    url: str                    # "https://reddit.com/r/entrepreneur"
    subscribers: int            # from PRAW (0 if unavailable)
    relevance_score: int        # 1–10
    relevance_reason: str       # 1–2 sentences
    icp_fit: str                # "high" | "medium" | "low"
    content_themes: List[str]   # 3–5 items
    posting_rules: List[str]    # key rules for marketers
    self_promo_allowed: bool
    engagement_level: str       # "high" | "medium" | "low"


class SubredditMapMetadata(BaseModel):
    domain: str
    generated_at: str
    candidates_discovered: int
    candidates_enriched: int
    sources: List[str]


class SubredditMap(BaseModel):
    subreddits: List[Subreddit]
    metadata: SubredditMapMetadata


# ---------------------------------------------------------------------------
# Agent 3: Thread Finder models
# ---------------------------------------------------------------------------

class RedditThread(BaseModel):
    title: str
    url: str
    subreddit: str
    score: int
    num_comments: int
    created_at: str
    relevance_score: int        # 1–10
    relevance_reason: str       # 1–2 sentences
    opportunity_type: str       # "pain_point" | "tool_comparison" | "workflow_question" | "competitor_mention" | "general_discussion"


class ThreadSearchMetadata(BaseModel):
    domain: str
    subreddit: str
    searched_at: str
    total_candidates: int


class ThreadSearchResult(BaseModel):
    domain: str
    subreddit: str
    threads: List[RedditThread]
    metadata: ThreadSearchMetadata


# Internal: Gemini ranking output only (not stored)
class _ThreadRanking(BaseModel):
    url: str
    relevance_score: int
    relevance_reason: str
    opportunity_type: str


class _ThreadRankingList(BaseModel):
    threads: List[_ThreadRanking]
