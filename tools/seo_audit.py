#!/usr/bin/env python3
"""SEO integrity guard for PacificMeister blog pages.

Checks:
1) every blog-*.html page is present in sitemap.xml
2) every blog-*.html page is linked from blog.html
3) every blog page has at least --min-inbound internal links

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
from pathlib import Path
from urllib.parse import urlparse

DOMAIN = "pacificmeister.github.io"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PacificMeister SEO integrity audit")
    p.add_argument("--site-dir", type=Path, default=Path("."), help="Path to pacificmeister.github.io root")
    p.add_argument("--min-inbound", type=int, default=5, help="Warn threshold for inbound links")
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


def main() -> int:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    sitemap = site_dir / "sitemap.xml"
    blog_index = site_dir / "blog.html"

    if not sitemap.exists() or not blog_index.exists():
        print("ERROR: sitemap.xml or blog.html missing", file=sys.stderr)
        return 2

    blog_articles = discover_blog_articles(site_dir)
    html_files = list(site_dir.glob("*.html"))
    links_by_file = {f.name: extract_internal_links(f) for f in html_files}

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

    if not (missing_from_sitemap or missing_from_blog_index or weakly_linked):
        print("\nPASS: No critical issues and all pages meet inbound threshold.")

    return 1 if (missing_from_sitemap or missing_from_blog_index) else 0


if __name__ == "__main__":
    raise SystemExit(main())
