#!/usr/bin/env python3
"""SEO integrity guard for PacificMeister blog pages.

Checks:
1) every blog-*.html page is present in sitemap.xml
2) every blog-*.html page is linked from blog.html
3) every blog page has at least --min-inbound internal links

Optional:
- internal-link opportunity suggestions for weakly linked pages
- markdown report output for recurring audits

Exit code:
- 0: pass
- 1: critical failure (missing from sitemap or blog index)
- 2: runtime/config error
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DOMAIN = "pacificmeister.github.io"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "build",
    "complete",
    "cost",
    "costs",
    "diy",
    "efoil",
    "for",
    "from",
    "guide",
    "how",
    "in",
    "is",
    "of",
    "the",
    "to",
    "vs",
    "what",
    "with",
    "your",
    "2026",
}
HUB_PAGES = {
    "index.html",
    "blog.html",
    "blog-best-efoils-2026.html",
    "blog-diy-efoil-guide.html",
    "blog-how-to-ride-efoil.html",
    "blog-efoil-cost-guide.html",
    "efoil-finder.html",
    "efoil-configurator.html",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PacificMeister SEO integrity audit")
    p.add_argument("--site-dir", type=Path, default=Path("."), help="Path to pacificmeister.github.io root")
    p.add_argument("--min-inbound", type=int, default=5, help="Warn threshold for inbound links")
    p.add_argument("--suggestion-count", type=int, default=5, help="Max source-page suggestions per weak page")
    p.add_argument(
        "--reinforce-count",
        type=int,
        default=5,
        help="Also generate suggestions for the lowest-inbound pages even when threshold passes",
    )
    p.add_argument("--write-report", type=Path, default=None, help="Write markdown report to this path")
    return p.parse_args()


def discover_blog_articles(site_dir: Path) -> list[str]:
    return sorted(p.name for p in site_dir.glob("blog-*.html") if p.name != "blog.html")


def normalize_internal_target(href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None

    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc != DOMAIN:
            return None
        path = parsed.path or "/"
    else:
        path = parsed.path or href

    if path.startswith("/"):
        path = path[1:]
    if path in {"", "/"}:
        return "index.html"

    return path.split("#", 1)[0].split("?", 1)[0]


def extract_internal_links(html_path: Path) -> set[str]:
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    links: set[str] = set()
    for href in HREF_RE.findall(text):
        n = normalize_internal_target(href)
        if n:
            links.add(n)
    return links


def parse_sitemap(sitemap_path: Path) -> set[str]:
    tree = ET.parse(sitemap_path)
    root = tree.getroot()
    urls: set[str] = set()
    for loc in root.findall(".//sm:loc", SITEMAP_NS):
        if not loc.text:
            continue
        n = normalize_internal_target(loc.text)
        if n:
            urls.add(n)
    return urls


def slug_tokens(filename: str) -> set[str]:
    stem = filename.removesuffix(".html")
    if stem.startswith("blog-"):
        stem = stem[5:]
    tokens = {t for t in TOKEN_SPLIT_RE.split(stem.lower()) if t and t not in STOPWORDS}
    return tokens


def candidate_source_pages(html_files: list[str]) -> list[str]:
    candidates: list[str] = []
    for name in sorted(html_files):
        if name in HUB_PAGES:
            candidates.append(name)
            continue
        if name.startswith("blog-") and name != "blog.html":
            candidates.append(name)
    return candidates


def build_link_suggestions(
    weak_targets: list[str],
    source_pages: list[str],
    links_by_file: dict[str, set[str]],
    max_suggestions: int,
) -> dict[str, list[tuple[str, str]]]:
    suggestions: dict[str, list[tuple[str, str]]] = {}
    source_tokens = {s: slug_tokens(s) for s in source_pages}

    for target in weak_targets:
        target_tokens = slug_tokens(target)
        scored: list[tuple[int, str, str]] = []

        for source in source_pages:
            if source == target:
                continue
            if target in links_by_file.get(source, set()):
                continue

            overlap = sorted(target_tokens & source_tokens.get(source, set()))
            if overlap:
                score = len(overlap) * 10 + (2 if source in HUB_PAGES else 0)
                reason = f"keyword overlap: {', '.join(overlap[:4])}"
            elif source in HUB_PAGES:
                score = 1
                reason = "hub page with strong authority"
            else:
                continue

            scored.append((score, source, reason))

        scored.sort(key=lambda x: (-x[0], x[1]))
        suggestions[target] = [(s, r) for _, s, r in scored[:max_suggestions]]

    return suggestions


def render_report(
    min_inbound: int,
    blog_articles: list[str],
    inbound_counts: dict[str, int],
    missing_from_sitemap: list[str],
    missing_from_blog_index: list[str],
    weakly_linked: list[str],
    reinforcement_targets: list[str],
    suggestions: dict[str, list[tuple[str, str]]],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append("# PacificMeister SEO Audit Report")
    lines.append("")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Blog pages scanned: {len(blog_articles)}")
    lines.append(f"- Inbound warning threshold: {min_inbound}")
    lines.append("")

    lines.append("## Critical Checks")
    lines.append("")
    lines.append(f"- Missing from sitemap.xml: {len(missing_from_sitemap)}")
    for item in missing_from_sitemap:
        lines.append(f"  - {item}")
    lines.append(f"- Missing from blog.html index: {len(missing_from_blog_index)}")
    for item in missing_from_blog_index:
        lines.append(f"  - {item}")
    lines.append("")

    lines.append("## Weakly Linked Pages")
    lines.append("")
    if weakly_linked:
        for page in sorted(weakly_linked, key=lambda p: (inbound_counts.get(p, 0), p)):
            lines.append(f"### {page} ({inbound_counts.get(page, 0)} inbound)")
            recs = suggestions.get(page, [])
            if recs:
                for src, reason in recs:
                    lines.append(f"- Add link from `{src}` ({reason})")
            else:
                lines.append("- No suggestions found; add at least one contextual link from blog.html or index.html")
            lines.append("")
    else:
        lines.append("All blog pages meet inbound threshold.")
        lines.append("")

    lines.append("## Reinforcement Opportunities")
    lines.append("")
    for page in reinforcement_targets:
        lines.append(f"### {page} ({inbound_counts.get(page, 0)} inbound)")
        recs = suggestions.get(page, [])
        if recs:
            for src, reason in recs:
                lines.append(f"- Add link from `{src}` ({reason})")
        else:
            lines.append("- No suggestions found; add one contextual link from a hub page")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    sitemap = site_dir / "sitemap.xml"
    blog_index = site_dir / "blog.html"

    if not sitemap.exists() or not blog_index.exists():
        print("ERROR: sitemap.xml or blog.html missing", file=sys.stderr)
        return 2

    blog_articles = discover_blog_articles(site_dir)
    html_files = sorted(f.name for f in site_dir.glob("*.html"))
    links_by_file = {name: extract_internal_links(site_dir / name) for name in html_files}

    inbound_counts = {article: 0 for article in blog_articles}
    for source, links in links_by_file.items():
        for target in links:
            if target in inbound_counts and target != source:
                inbound_counts[target] += 1

    sitemap_urls = parse_sitemap(sitemap)
    blog_index_links = links_by_file.get("blog.html", set())

    missing_from_sitemap = [a for a in blog_articles if a not in sitemap_urls]
    missing_from_blog_index = [a for a in blog_articles if a not in blog_index_links]
    weakly_linked = [a for a in blog_articles if inbound_counts.get(a, 0) < args.min_inbound]
    reinforcement_targets = [
        name
        for name, _ in sorted(inbound_counts.items(), key=lambda kv: (kv[1], kv[0]))[: max(args.reinforce_count, 0)]
    ]

    sources = candidate_source_pages(html_files)
    suggestion_targets = sorted(set(weakly_linked) | set(reinforcement_targets))
    suggestions = build_link_suggestions(suggestion_targets, sources, links_by_file, args.suggestion_count)

    print("PacificMeister SEO integrity audit")
    print(f"Blog pages: {len(blog_articles)}")

    if missing_from_sitemap:
        print(f"\nCRITICAL: Missing from sitemap.xml ({len(missing_from_sitemap)})")
        for x in missing_from_sitemap:
            print(f"- {x}")

    if missing_from_blog_index:
        print(f"\nCRITICAL: Missing from blog.html index ({len(missing_from_blog_index)})")
        for x in missing_from_blog_index:
            print(f"- {x}")

    if weakly_linked:
        print(f"\nWARN: Weakly linked pages (< {args.min_inbound} inbound links): {len(weakly_linked)}")
        for x in sorted(weakly_linked, key=lambda k: (inbound_counts[k], k)):
            print(f"- {x}: {inbound_counts[x]} inbound")
            for src, reason in suggestions.get(x, []):
                print(f"    -> {src} ({reason})")

    if reinforcement_targets:
        print("\nINFO: Lowest inbound pages for reinforcement")
        for x in reinforcement_targets:
            print(f"- {x}: {inbound_counts[x]} inbound")

    if not (missing_from_sitemap or missing_from_blog_index or weakly_linked):
        print("\nPASS: No critical issues and all pages meet inbound threshold.")

    if args.write_report:
        out_path = args.write_report
        if not out_path.is_absolute():
            out_path = site_dir / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            render_report(
                min_inbound=args.min_inbound,
                blog_articles=blog_articles,
                inbound_counts=inbound_counts,
                missing_from_sitemap=missing_from_sitemap,
                missing_from_blog_index=missing_from_blog_index,
                weakly_linked=weakly_linked,
                reinforcement_targets=reinforcement_targets,
                suggestions=suggestions,
            ),
            encoding="utf-8",
        )
        print(f"\nReport written: {out_path}")

    return 1 if (missing_from_sitemap or missing_from_blog_index) else 0


if __name__ == "__main__":
    raise SystemExit(main())
