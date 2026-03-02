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
    icp: ICP = Field(description="Ideal customer profile")
    primary_use_cases: List[str] = Field(description="Top 3–5 primary use cases")
    core_pain_points_solved: List[str] = Field(description="Top 5 pain points the product solves")
    differentiators: List[str] = Field(description="Top 3 competitive differentiators")
    reddit_safe_description: str = Field(description="Non-salesy phrasing safe for Reddit communities")
    red_flags: List[str] = Field(description="Claims or language to avoid when discussing on Reddit")


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
    keyword_map: KeywordMap
    competitor_set: List[Competitor] = Field(description="5–15 direct and adjacent competitors")
    metadata: Metadata
