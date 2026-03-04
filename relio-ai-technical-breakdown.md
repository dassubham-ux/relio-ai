# Relio AI — Technical Breakdown: 3-Agent Reddit Marketing Pipeline

> **IP Notice:** Source code lives in a private repository. A Reddit auditor can request
> read-only repository access or a recorded technical walkthrough at any time.

---

## Overview

Relio AI is a three-agent pipeline that (1) deep-researches a company, (2) discovers and
scores Reddit communities where its ideal customers are active, and (3) generates authentic,
rule-compliant content for each community. Every agent produces a validated, fixed JSON
schema consumed by the next agent downstream.

```
Company URL
    │
    ▼
┌─────────────────────────┐
│  Agent 1 · Researcher   │  Gemini 2.5-flash + Google Search grounding
│  Output: CompanyBrief   │  Pydantic v2 schema · temperature 0.0
└────────────┬────────────┘
             │  CompanyBrief JSON
             ▼
┌─────────────────────────┐
│  Agent 2 · Finder       │  Gemini discovery + public Reddit JSON API
│  Output: SubredditMap   │  Scored, ranked, rule-enriched subreddit list
└────────────┬────────────┘
             │  SubredditMap JSON
             ▼
┌─────────────────────────┐
│  Agent 3 · Writer       │  Gemini with safety-flag guardrails
│  Output: ContentPlan    │  Per-subreddit post/comment drafts
└─────────────────────────┘
```

---

## Agent 1 — Company Deep Research

### Pipeline Phases

| Phase | What happens |
|-------|-------------|
| **1 · Web Scrape** | Fetches homepage, `/pricing`, `/about`, `/blog`, `/docs` via async HTTP; strips nav/footer/scripts; caps at 20 000 chars |
| **2 · Gemini Research** | Sends scraped text to Gemini with Google Search grounding enabled; discovers competitors, customer sentiment (G2/Capterra/Reddit), ICP signals, pricing model, differentiators, and language to avoid on Reddit |
| **3 · Structured Output** | Sends research narrative back to Gemini in JSON mode (temperature 0.0) constrained to the `CompanyBrief` schema below |

### Fixed JSON Schema — `CompanyBrief`

```json
{
  "company_snapshot": {
    "what_it_does": "string  — 1–2 sentence product description",
    "icp": {
      "roles":        ["string"],   // e.g. ["VP of Sales", "RevOps Manager"]
      "industries":   ["string"],   // e.g. ["SaaS", "B2B Tech"]
      "company_size": "string",     // e.g. "50–500 employees"
      "geography":    "string"      // e.g. "US, Europe"
    },
    "primary_use_cases":        ["string"],  // top 3–5
    "core_pain_points_solved":  ["string"],  // top 5
    "differentiators":          ["string"],  // top 3
    "reddit_safe_description":  "string",    // non-salesy phrasing safe for Reddit
    "red_flags":                ["string"]   // claims / phrases to avoid on Reddit
  },
  "keyword_map": {
    "problem_keywords":          ["string"],  // pain-driven terms, 4–5
    "workflow_keywords":         ["string"],  // "how do I…" queries, 4–5
    "tool_comparison_keywords":  ["string"],  // "X vs Y / alternatives", 4–5
    "competitor_keywords":       ["string"]   // competitor brand names, 4–5
  },
  "competitor_set": [
    {
      "name":     "string",
      "category": "string",
      "url":      "string"
    }
  ],  // 5–15 direct and adjacent competitors
  "metadata": {
    "url":           "string",    // researched company URL
    "researched_at": "string",    // ISO 8601 timestamp
    "sources":       ["string"]   // grounding source URLs
  }
}
```

### Acceptance Criteria (validated at runtime)

- ≥ 15 total keywords across all four `keyword_map` buckets
- ≥ 5 entries in `competitor_set`
- `icp.roles`, `icp.industries`, `icp.company_size`, `icp.geography` all non-empty

---

## Agent 2 — Subreddit Finder

### Pipeline Phases

| Phase | What happens |
|-------|-------------|
| **2 · Gemini Discovery** | Sends `CompanyBrief` context to Gemini with Google Search; runs five search patterns (`site:reddit.com <domain>`, `site:reddit.com <pain_point>`, `site:reddit.com <competitor>`, `site:reddit.com <role>`, `site:reddit.com <industry>`); targets 15–25 candidate subreddits |
| **3 · Public API Enrichment** | Extracts `r/name` patterns via regex; hits Reddit's unauthenticated JSON endpoints (`/about.json`, `/about/rules.json`) for real subscriber counts, descriptions, and moderation rules; rate-limited to ≈ 1 req/s |
| **4 · Gemini Structuring** | Sends enriched data block to Gemini in JSON mode (temperature 0.0); scores and selects 10–20 best subreddits into `SubredditMap` schema |

### Fit Score Logic (`relevance_score` 1–10)

The score reflects how densely the subreddit's actual audience overlaps with the company's ICP and how actively they discuss the pain points the product solves.

| Score band | Meaning |
|------------|---------|
| **8–10 · High fit** | ICP members actively post pain-point questions in this sub; the problem space is a core content theme; signal-to-noise ratio is high |
| **5–7 · Medium fit** | ICP is present but the subreddit covers a broader topic; relevant threads exist but are not the majority of content |
| **1–4 · Low fit** | ICP represents a minority; the product's use case is only tangentially related to the community's primary focus |

Scoring inputs fed to Gemini:
- Subreddit description and actual subscriber count (from public API)
- Company ICP roles and industries
- Core pain points and keyword map
- Discovery narrative (what Google Search returned)

### Link Tolerance (`self_promo_allowed` + `icp_fit`)

Link tolerance describes how strictly a subreddit polices promotional content. It is derived from two signals: the moderation rules fetched from the public API, and the subreddit's ICP fit tier.

| Tolerance level | `self_promo_allowed` | Rule signals | Typical subreddit examples |
|-----------------|----------------------|--------------|---------------------------|
| **High** | `true` | Rules explicitly permit sharing tools/resources; a dedicated weekly promo thread exists (e.g., "Share Your Project Saturday") | r/entrepreneur, r/SaaS, r/startups (promo threads) |
| **Medium** | `false` (context-dependent) | No blanket ban; promotional posts are tolerated if they add clear value and do not read as ads; mods may remove low-effort promos | r/sales, r/marketing, r/productivity |
| **Low** | `false` | Rules explicitly forbid self-promotion, affiliate links, or commercial content; violations result in bans | r/personalfinance, r/datascience, r/programming |

Agent 3 uses this tolerance level to decide the content format: **high** → can mention the product by name; **medium** → lead with value, soft mention; **low** → purely educational, no mention.

### Fixed JSON Schema — `SubredditMap`

```json
{
  "subreddits": [
    {
      "name":              "r/entrepreneur",
      "url":               "https://reddit.com/r/entrepreneur",
      "subscribers":       int,           // exact count from public API
      "relevance_score":   int,           // 1–10, sorted descending
      "relevance_reason":  "string",      // 1–2 sentences
      "icp_fit":           "high|medium|low",
      "content_themes":    ["string"],    // 3–5 dominant post formats
      "posting_rules":     ["string"],    // 2–4 marketer-relevant rules
      "self_promo_allowed": bool,
      "engagement_level":  "high|medium|low"
                                          // high  > 500K subs
                                          // medium 50K–500K
                                          // low   < 50K
    }
  ],
  "metadata": {
    "domain":               "string",
    "generated_at":         "string",   // ISO 8601
    "candidates_discovered": int,
    "candidates_enriched":   int,
    "sources":              ["string"]
  }
}
```

### Acceptance Criteria (validated at runtime)

- ≥ 10 subreddits in output
- All subreddits have `subscribers > 0` (real data, not invented)
- All subreddits have at least one entry in `posting_rules`
- List sorted by `relevance_score` descending

---

## Agent 3 — Content Writer (Safety-Flag System)

### Pipeline Phases

| Phase | What happens |
|-------|-------------|
| **3 · Content Generation** | For each subreddit in `SubredditMap`, Gemini drafts a post or comment using the company's `reddit_safe_description`, `red_flags`, and the sub's `link_tolerance` tier |
| **4 · Safety Audit** | A second Gemini pass scores every draft against the safety-flag rubric below; flagged drafts are rewritten, not discarded |
| **5 · Structured Output** | Validated `ContentPlan` JSON with one or more drafts per subreddit, each carrying its safety score and audit notes |

### Safety Flags

Each draft is evaluated against six flag categories. Any flag set to `true` triggers an automatic rewrite.

| Flag | Trigger condition | Example violation |
|------|------------------|-------------------|
| **too_salesy** | Draft reads as an advertisement; uses superlatives ("best", "revolutionary", "game-changing"); leads with product name before establishing value | "Check out Relio AI — the best Reddit marketing tool!" |
| **unsolicited_promotion** | Promotes the product in a thread where no one asked for tool recommendations | Dropping a product link in a help thread without being asked |
| **rule_violation** | Draft would break a rule extracted from `posting_rules` for that subreddit (e.g., no links, no self-promo) | Posting a URL to a sub that bans external links |
| **low_value** | Post contains no actionable advice, data, or genuine insight; it exists purely to name-drop the product | "We've been using Relio and it's great, highly recommend" |
| **astroturfing_risk** | Draft implies the author is a neutral user while actually promoting a product; fake-organic phrasing | "I'm just a regular user and I happened to find this tool…" |
| **icp_mismatch** | Content angle does not match the subreddit's actual audience (wrong role, wrong seniority, wrong industry) | Posting an enterprise sales tool pitch in r/freelance |

### Safety Score

Each draft receives a `safety_score` from 0–100. Drafts below 70 are automatically rewritten.

```
safety_score = 100
  − 25 per flag set to true
  (floor 0)
```

### Fixed JSON Schema — `ContentPlan` (planned)

```json
{
  "posts": [
    {
      "subreddit":      "r/entrepreneur",
      "format":         "post|comment|reply",
      "title":          "string | null",
      "body":           "string",
      "link_tolerance": "high|medium|low",
      "safety_score":   int,           // 0–100
      "safety_flags": {
        "too_salesy":          bool,
        "unsolicited_promotion": bool,
        "rule_violation":      bool,
        "low_value":           bool,
        "astroturfing_risk":   bool,
        "icp_mismatch":        bool
      },
      "audit_notes":    "string"       // 1–2 sentences from the safety audit pass
    }
  ],
  "metadata": {
    "domain":       "string",
    "generated_at": "string"
  }
}
```

---

## Transparency Statement

> Our source code is maintained in a private repository for IP protection.
> We are happy to grant **read-only access** to a Reddit auditor upon request,
> or provide a **recorded technical walkthrough** of the Agent logic, data flows,
> and safety-flag system at any time.
>
> Relio AI does not store Reddit user data, does not post autonomously, and does
> not interact with the Reddit API beyond reading public subreddit metadata.
> All content generated is reviewed by a human operator before any posting occurs.
