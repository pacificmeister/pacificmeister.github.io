#!/usr/bin/env python3
"""Generate a content freshness backlog for PacificMeister.

Ranks blog articles by refresh priority using:
- days since last update
- current internal-link authority (inbound links)
- evergreen intent signals in the title/slug
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

DOMAIN = "pacificmeister.github.io"
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DATE_PUBLISHED_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.IGNORECASE)
DATE_MODIFIED_RE = re.compile(r'"dateModified"\s*:\s*"([^"]+)"', re.IGNORECASE)
TIME_TAG_RE = re.compile(r"<time[^>]*datetime=[\"']([^\"']+)[\"']", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"[^a-z0-9]+")
EVERGREEN_HINTS = {
    "guide",
    "how",
    "troubleshooting",
    "cost",
    "costs",
    "laws",
    "regulations",
    "best",
    "buying",
    "vs",
    "comparison",
    "calculator",
}


@dataclass
class Article:
    name: str
    title: str
    published: datetime
    modified: datetime
    inbound_links: int
    words: int



def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate PacificMeister content freshness report")
    p.add_argument("--site-dir", type=Path, default=Path("."), help="Path to pacificmeister.github.io root")
    p.add_argument("--stale-after-days", type=int, default=120, help="Days since modified to consider stale")
    p.add_argument("--min-inbound", type=int, default=4, help="Minimum inbound links for high-priority refresh")
    p.add_argument("--top", type=int, default=10, help="Max backlog items in report")
    p.add_argument(
        "--write-report",
        type=Path,
        default=Path("reports/content-freshness-report.md"),
        help="Markdown output path (relative to site-dir when not absolute)",
    )
    return p.parse_args()



def normalize_internal_target(href: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
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



def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)



def extract_title(raw: str, fallback: str) -> str:
    m = TITLE_RE.search(raw)
    return m.group(1).strip() if m else fallback



def extract_dates(raw: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    published = parse_datetime((DATE_PUBLISHED_RE.search(raw) or [None, None])[1])
    modified = parse_datetime((DATE_MODIFIED_RE.search(raw) or [None, None])[1])

    if not published:
        time_match = TIME_TAG_RE.search(raw)
        if time_match:
            published = parse_datetime(time_match.group(1))

    if not published:
        published = now
    if not modified:
        modified = published

    return published, modified



def word_count(raw: str) -> int:
    text = TAG_RE.sub(" ", raw)
    words = [w for w in TOKEN_RE.split(text.lower()) if w]
    return len(words)



def discover_articles(site_dir: Path) -> list[tuple[str, str]]:
    pages = sorted(p for p in site_dir.glob("blog-*.html") if p.name != "blog.html")
    return [(p.name, p.read_text(encoding="utf-8", errors="ignore")) for p in pages]



def inbound_link_counts(site_dir: Path) -> dict[str, int]:
    html_files = sorted(p.name for p in site_dir.glob("*.html"))
    counts: dict[str, int] = {}
    for name in html_files:
        counts.setdefault(name, 0)

    for source in html_files:
        raw = (site_dir / source).read_text(encoding="utf-8", errors="ignore")
        seen: set[str] = set()
        for href in HREF_RE.findall(raw):
            target = normalize_internal_target(href)
            if not target or target == source:
                continue
            if target not in counts:
                continue
            if target in seen:
                continue
            counts[target] += 1
            seen.add(target)

    return counts



def evergreen_score(article: Article) -> int:
    blob = f"{article.name} {article.title}".lower()
    return sum(1 for token in EVERGREEN_HINTS if token in blob)



def refresh_score(article: Article, stale_after_days: int) -> float:
    age_days = max(0, int((datetime.now(timezone.utc) - article.modified).days))
    freshness_gap = max(0, age_days - stale_after_days)
    freshness_ratio = min(age_days / max(stale_after_days, 1), 1.0)
    evergreen = evergreen_score(article)
    stale_bonus = 100 if age_days >= stale_after_days else 0
    return stale_bonus + freshness_gap * 0.8 + freshness_ratio * 30 + min(article.inbound_links, 20) * 1.5 + evergreen * 4



def recommendation(article: Article, age_days: int) -> str:
    now = datetime.now(timezone.utc)
    title = article.title.lower()
    recs: list[str] = []
    if "2026" in title and (now.month >= 10 or age_days >= 240):
        recs.append("roll title/meta/year references to 2027 with fresh examples")
    if age_days >= 60 and ("guide" in title or "how" in title):
        recs.append("add updated step-by-step checks and one new failure pattern")
    if age_days >= 45 and ("cost" in title or "buy" in title or "best" in title):
        recs.append("refresh pricing tables and market alternatives")
    if age_days >= 45 and ("laws" in title or "regulations" in title):
        recs.append("re-verify country/state rules and add dated source notes")
    if not recs:
        recs.append("monitor for now; refresh once older than ~60 days or after major product changes")
    if age_days > 240:
        recs.append("publish a short companion update post and cross-link both ways")
    return "; ".join(recs)



def render_report(articles: list[Article], stale_after_days: int, min_inbound: int, top: int) -> str:
    now = datetime.now(timezone.utc)
    enriched: list[tuple[float, int, int, Article]] = []
    stale_count = 0

    for a in articles:
        age_days = max(0, int((now - a.modified).days))
        if age_days >= stale_after_days:
            stale_count += 1
        score = refresh_score(a, stale_after_days)
        enriched.append((score, age_days, evergreen_score(a), a))

    enriched.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3].name))

    high_priority = [
        (score, age, evg, a)
        for score, age, evg, a in enriched
        if age >= stale_after_days and a.inbound_links >= min_inbound
    ]

    lines: list[str] = []
    lines.append("# PacificMeister Content Freshness Report")
    lines.append("")
    lines.append(f"- Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"- Blog articles scanned: {len(articles)}")
    lines.append(f"- Stale threshold: {stale_after_days} days")
    lines.append(f"- High-priority inbound threshold: {min_inbound}")
    lines.append(f"- Stale articles: {stale_count}")
    lines.append(f"- High-priority refresh candidates: {len(high_priority)}")
    lines.append("")

    lines.append("## Refresh Backlog (ranked)")
    lines.append("")

    if not enriched:
        lines.append("No blog articles found.")
        lines.append("")
        return "\n".join(lines) + "\n"

    for i, (score, age_days, evg, a) in enumerate(enriched[: max(top, 1)], start=1):
        tier = "HIGH" if age_days >= stale_after_days and a.inbound_links >= min_inbound else ("WATCH" if age_days >= 45 else "MONITOR")
        lines.append(f"### {i}) [{tier}] {a.name}")
        lines.append(f"- Title: {a.title}")
        lines.append(f"- Modified: {a.modified.date().isoformat()} ({age_days} days ago)")
        lines.append(f"- Suggested refresh check date: {(a.modified + timedelta(days=60)).date().isoformat()}")
        lines.append(f"- Published: {a.published.date().isoformat()}")
        lines.append(f"- Inbound links: {a.inbound_links}")
        lines.append(f"- Approx words: {a.words}")
        lines.append(f"- Evergreen signals: {evg}")
        lines.append(f"- Priority score: {score:.1f}")
        lines.append(f"- Recommended refresh: {recommendation(a, age_days)}")
        lines.append("")

    if high_priority:
        lines.append("## Suggested Next 3 to Refresh")
        lines.append("")
        for score, age, _, a in high_priority[:3]:
            lines.append(f"- `{a.name}` (score {score:.1f}, {age} days stale, {a.inbound_links} inbound)")
        lines.append("")

    return "\n".join(lines) + "\n"



def main() -> int:
    args = parse_args()
    site_dir = args.site_dir.resolve()

    inbound = inbound_link_counts(site_dir)
    articles_raw = discover_articles(site_dir)

    articles: list[Article] = []
    for name, raw in articles_raw:
        published, modified = extract_dates(raw)
        articles.append(
            Article(
                name=name,
                title=extract_title(raw, name),
                published=published,
                modified=modified,
                inbound_links=inbound.get(name, 0),
                words=word_count(raw),
            )
        )

    report = render_report(articles, args.stale_after_days, args.min_inbound, args.top)

    out = args.write_report
    if not out.is_absolute():
        out = site_dir / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print("PacificMeister content freshness audit complete")
    print(f"Articles: {len(articles)}")
    print(f"Report written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
