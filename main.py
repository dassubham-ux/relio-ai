#!/usr/bin/env python3
"""
Relio AI — Agent 1: Company Deep Research
CLI entry point.

Usage:
    python main.py --url "https://stripe.com"
    python main.py --url "https://stripe.com" --output custom.json
    python main.py --paste-text "Stripe is a payments platform..." --url "https://stripe.com"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _default_output_path(url: str) -> Path:
    """Derive output filename from the company domain."""
    domain = urlparse(url).netloc.lstrip("www.")
    slug = domain.replace(".", "-")
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    return output_dir / f"{slug}.json"


def _validate_brief(brief_dict: dict) -> list[str]:
    """Return a list of warnings if acceptance criteria are not met."""
    warnings = []

    km = brief_dict.get("keyword_map", {})
    total_keywords = sum(
        len(km.get(k, []))
        for k in ("problem_keywords", "workflow_keywords", "tool_comparison_keywords", "competitor_keywords")
    )
    if total_keywords < 15:
        warnings.append(f"Only {total_keywords} keywords (need 15+)")

    competitors = brief_dict.get("competitor_set", [])
    if len(competitors) < 5:
        warnings.append(f"Only {len(competitors)} competitors (need 5+)")

    icp = brief_dict.get("company_snapshot", {}).get("icp", {})
    if not icp.get("roles") or not icp.get("industries"):
        warnings.append("ICP is incomplete (missing roles or industries)")

    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relio AI Agent 1 — Company Deep Research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", required=True, help="Company homepage URL to research")
    parser.add_argument(
        "--paste-text",
        metavar="TEXT",
        help="Manually provided company description (skips web scraping)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Output JSON file path (default: output/<domain>.json)",
    )
    args = parser.parse_args()

    # Resolve output path
    output_path = Path(args.output) if args.output else _default_output_path(args.url)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Phase 1: Website fetch (or paste-text fallback)
    if args.paste_text:
        print(f"[Phase 1] Using provided paste-text ({len(args.paste_text)} chars). Skipping web scrape.")
        website_text = args.paste_text
    else:
        print(f"[Phase 1] Fetching website pages for: {args.url}")
        from agent.fetcher import fetch_website_sync, pages_to_text
        pages = fetch_website_sync(args.url)
        if not pages:
            print(
                "ERROR: Could not fetch any pages from the website. "
                "Use --paste-text as a fallback.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[Phase 1] Fetched {len(pages)} page(s): {[p.url for p in pages]}")
        website_text = pages_to_text(pages)

    # Phases 2 + 3: Research and structure
    from agent.researcher import research_company
    brief = research_company(url=args.url, website_text=website_text)

    # Serialize to JSON
    brief_dict = json.loads(brief.model_dump_json(indent=2))

    # Acceptance check
    warnings = _validate_brief(brief_dict)
    if warnings:
        print("\n[WARN] Acceptance criteria not fully met:")
        for w in warnings:
            print(f"  - {w}")
    else:
        km = brief_dict["keyword_map"]
        kw_count = sum(len(km[k]) for k in km)
        comp_count = len(brief_dict["competitor_set"])
        print(f"\n[OK] Acceptance criteria met: {kw_count} keywords, {comp_count} competitors, ICP populated.")

    # Write output
    output_path.write_text(json.dumps(brief_dict, indent=2), encoding="utf-8")
    print(f"\nOutput written to: {output_path}")

    # Store in MongoDB
    from agent.storage import upsert_brief
    doc_id = upsert_brief(brief_dict)
    print(f"Stored in MongoDB (relio.company_briefs) — _id: {doc_id}")


if __name__ == "__main__":
    main()
