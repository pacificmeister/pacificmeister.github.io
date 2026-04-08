#!/usr/bin/env python3
"""Generate prioritized PacificMeister content-gap briefs from a reusable topic library.

Outputs a markdown report with:
- coverage check against existing blog inventory
- top uncovered opportunities ranked by intent x value
- ready-to-write briefs with slug, internal link targets, and outline
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TOKEN_RE = re.compile(r"[^a-z0-9]+")

HUB_PRIORITY = {
    "index.html": 3,
    "blog.html": 3,
    "blog-best-efoils-2026.html": 2,
    "blog-diy-efoil-guide.html": 2,
    "blog-how-to-ride-efoil.html": 2,
    "blog-efoil-cost-guide.html": 2,
    "efoil-finder.html": 2,
    "efoil-configurator.html": 2,
}


@dataclass
class Page:
    name: str
    title: str
    tokens: set[str]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate PacificMeister content-gap briefs")
    p.add_argument("--site-dir", type=Path, default=Path("."), help="Path to pacificmeister.github.io root")
    p.add_argument(
        "--topics",
        type=Path,
        default=Path("tools/content_topics.json"),
        help="Topic definition JSON file",
    )
    p.add_argument("--max-briefs", type=int, default=8, help="Maximum uncovered briefs to output")
    p.add_argument(
        "--write-report",
        type=Path,
        default=Path("reports/content-gap-briefs.md"),
        help="Markdown output path (relative to site-dir if not absolute)",
    )
    return p.parse_args()


def slugify(text: str) -> str:
    slug = "-".join(t for t in TOKEN_RE.split(text.lower()) if t)
    return f"blog-{slug}.html"


def normalize_tokens(text: str) -> set[str]:
    return {t for t in TOKEN_RE.split(text.lower()) if t}


def discover_pages(site_dir: Path) -> list[Page]:
    pages: list[Page] = []
    for path in sorted(site_dir.glob("*.html")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        match = TITLE_RE.search(raw)
        title = match.group(1).strip() if match else path.name
        token_source = f"{path.name.replace('.html', '')} {title}"
        pages.append(Page(name=path.name, title=title, tokens=normalize_tokens(token_source)))
    return pages


def coverage_score(topic_keywords: list[str], corpus_tokens: set[str]) -> int:
    score = 0
    for keyword in topic_keywords:
        keyword_tokens = normalize_tokens(keyword)
        if keyword_tokens and keyword_tokens.issubset(corpus_tokens):
            score += 1
    return score


def is_covered(topic: dict[str, Any], score: int, corpus_tokens: set[str]) -> bool:
    primary_tokens = normalize_tokens(topic.get("primaryKeyword", ""))
    if primary_tokens and primary_tokens.issubset(corpus_tokens):
        return True
    return score >= 2


def suggest_internal_links(topic: dict[str, Any], pages: list[Page], limit: int = 4) -> list[str]:
    topic_tokens = normalize_tokens(" ".join(topic.get("keywords", [])) + " " + topic.get("primaryKeyword", ""))
    ranked: list[tuple[int, str]] = []

    for page in pages:
        overlap = len(topic_tokens & page.tokens)
        if overlap == 0 and page.name not in HUB_PRIORITY:
            continue
        score = overlap * 10 + HUB_PRIORITY.get(page.name, 0)
        ranked.append((score, page.name))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    picks = [name for _, name in ranked[:limit]]
    return picks


def render_report(
    topics: list[dict[str, Any]],
    covered: list[dict[str, Any]],
    uncovered_ranked: list[dict[str, Any]],
    pages: list[Page],
    max_briefs: int,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append("# PacificMeister Content Gap Briefs")
    lines.append("")
    lines.append(f"- Generated: {generated}")
    lines.append(f"- Existing HTML pages scanned: {len(pages)}")
    lines.append(f"- Topics in library: {len(topics)}")
    lines.append(f"- Covered topics: {len(covered)}")
    lines.append(f"- Uncovered opportunities: {len(uncovered_ranked)}")
    lines.append("")

    lines.append("## Top Uncovered Opportunities")
    lines.append("")
    if not uncovered_ranked:
        lines.append("Everything in the topic library is already covered.")
        lines.append("")
        return "\n".join(lines) + "\n"

    for i, t in enumerate(uncovered_ranked[:max_briefs], start=1):
        lines.append(f"### {i}) {t['topic']}")
        lines.append(f"- Priority score: {t['priorityScore']} (intent {t['intent']} × value {t['value']})")
        lines.append(f"- Primary keyword: {t['primaryKeyword']}")
        lines.append(f"- Proposed URL: `{t['proposedSlug']}`")
        lines.append(f"- Angle: {t['angle']}")
        lines.append("- Suggested internal links to include:")
        for link in t["internalLinks"]:
            lines.append(f"  - `{link}`")
        lines.append("- Suggested outline:")
        for item in t.get("outline", []):
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("## Covered Topics")
    lines.append("")
    for t in covered:
        lines.append(f"- {t['topic']} (coverage score {t['coverageScore']})")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    topics_path = args.topics if args.topics.is_absolute() else site_dir / args.topics

    if not topics_path.exists():
        raise SystemExit(f"ERROR: topics file not found: {topics_path}")

    topics = json.loads(topics_path.read_text(encoding="utf-8"))
    pages = discover_pages(site_dir)

    corpus_tokens: set[str] = set()
    for p in pages:
        corpus_tokens |= p.tokens

    covered: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []

    for topic in topics:
        score = coverage_score(topic.get("keywords", []), corpus_tokens)
        enriched = dict(topic)
        enriched["coverageScore"] = score
        enriched["priorityScore"] = int(topic.get("intent", 0)) * int(topic.get("value", 0))
        enriched["proposedSlug"] = slugify(topic.get("primaryKeyword", topic.get("topic", "new-topic")))
        enriched["internalLinks"] = suggest_internal_links(topic, pages)

        if is_covered(topic, score, corpus_tokens):
            covered.append(enriched)
        else:
            uncovered.append(enriched)

    covered.sort(key=lambda t: (-t["coverageScore"], t["topic"]))
    uncovered.sort(key=lambda t: (-t["priorityScore"], t["topic"]))

    report = render_report(topics, covered, uncovered, pages, args.max_briefs)

    out = args.write_report
    if not out.is_absolute():
        out = site_dir / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print("PacificMeister content gap brief generation complete")
    print(f"Topics: {len(topics)}")
    print(f"Covered: {len(covered)}")
    print(f"Uncovered: {len(uncovered)}")
    print(f"Report written: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
