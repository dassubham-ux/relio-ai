#!/usr/bin/env python3
"""
Relio AI — Agent 2: Subreddit Finder
CLI entry point.

Usage:
    python main2.py --domain stripe.com
    python main2.py --brief-file output/stripe-com.json
    python main2.py --domain stripe.com --output custom.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _validate_subreddit_map(map_dict: dict) -> list[str]:
    """Return warnings if acceptance criteria are not met."""
    warnings = []
    subs = map_dict.get("subreddits", [])

    if len(subs) < 10:
        warnings.append(f"Only {len(subs)} subreddits found (need 10+)")

    zero_sub_subs = [s["name"] for s in subs if s.get("subscribers", 0) == 0]
    if zero_sub_subs:
        warnings.append(f"Subreddits with 0 subscribers: {', '.join(zero_sub_subs)}")

    no_rules = [s["name"] for s in subs if not s.get("posting_rules")]
    if no_rules:
        warnings.append(f"Subreddits missing posting_rules: {', '.join(no_rules)}")

    scores = [s.get("relevance_score", 0) for s in subs]
    if scores != sorted(scores, reverse=True):
        warnings.append("Subreddits are not sorted by relevance_score descending")

    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Relio AI Agent 2 — Subreddit Finder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--domain",
        metavar="DOMAIN",
        help="Company domain (e.g. stripe.com) — fetches CompanyBrief from MongoDB",
    )
    source_group.add_argument(
        "--brief-file",
        metavar="FILE",
        help="Path to a CompanyBrief JSON file (e.g. output/stripe-com.json)",
    )

    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Output JSON file path (default: output/<domain>-subreddits.json)",
    )

    args = parser.parse_args()

    from agent.models import CompanyBrief

    # Phase 1: Load CompanyBrief
    if args.domain:
        domain = args.domain.lstrip("www.")
        print(f"[Phase 1] Loading CompanyBrief from MongoDB for domain: {domain}")
        from pymongo import MongoClient
        mongo_client = MongoClient("mongodb://localhost:27017")
        collection = mongo_client["relio"]["company_briefs"]
        doc = collection.find_one({"domain": domain})
        if not doc:
            print(
                f"ERROR: No CompanyBrief found in MongoDB for domain '{domain}'. "
                "Run main.py first.",
                file=sys.stderr,
            )
            sys.exit(1)
        doc.pop("_id", None)
        doc.pop("domain", None)
        brief = CompanyBrief.model_validate(doc)
        print("[Phase 1] Loaded CompanyBrief from MongoDB.")

    else:
        brief_path = Path(args.brief_file)
        if not brief_path.exists():
            print(f"ERROR: File not found: {brief_path}", file=sys.stderr)
            sys.exit(1)
        print(f"[Phase 1] Loading CompanyBrief from file: {brief_path}")
        data = json.loads(brief_path.read_text(encoding="utf-8"))

        meta_url = data.get("metadata", {}).get("url", "")
        if not meta_url:
            print(
                "ERROR: CompanyBrief JSON missing metadata.url — cannot determine domain.",
                file=sys.stderr,
            )
            sys.exit(1)

        netloc = urlparse(meta_url).netloc
        if not netloc:
            print(f"ERROR: Cannot parse domain from metadata.url: {meta_url!r}", file=sys.stderr)
            sys.exit(1)

        domain = netloc.lstrip("www.")
        brief = CompanyBrief.model_validate(data)
        print(f"[Phase 1] Loaded CompanyBrief. Domain: {domain}")

    # Resolve output path
    slug = domain.replace(".", "-")
    output_path = Path(args.output) if args.output else Path("output") / f"{slug}-subreddits.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Phases 2–4: Discover, enrich, structure
    from agent.finder import find_subreddits
    subreddit_map = find_subreddits(brief=brief, domain=domain)

    # Serialize
    map_dict = json.loads(subreddit_map.model_dump_json(indent=2))

    # Acceptance check
    warnings = _validate_subreddit_map(map_dict)
    if warnings:
        print("\n[WARN] Acceptance criteria not fully met:")
        for w in warnings:
            print(f"  - {w}")
    else:
        sub_count = len(map_dict["subreddits"])
        top = map_dict["subreddits"][0] if map_dict["subreddits"] else {}
        print(
            f"\n[OK] {sub_count} subreddits found. "
            f"Top: {top.get('name')} (score={top.get('relevance_score')})"
        )

    # Write output
    output_path.write_text(json.dumps(map_dict, indent=2), encoding="utf-8")
    print(f"\nOutput written to: {output_path}")

    # Store in MongoDB
    from agent.storage import upsert_subreddit_map
    doc_id = upsert_subreddit_map(map_dict)
    print(f"Stored in MongoDB (relio.subreddit_maps) — _id: {doc_id}")


if __name__ == "__main__":
    main()
