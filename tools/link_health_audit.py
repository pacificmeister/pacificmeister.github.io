#!/usr/bin/env python3
"""PacificMeister link health audit.

Checks all HTML pages for:
1) Broken internal links (missing target files or missing #anchors)
2) Optional external URL reachability checks

Exit code:
- 0: pass
- 1: broken internal links (or external when --fail-on-external)
- 2: runtime/config error
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
ID_RE = re.compile(r'id=["\']([^"\']+)["\']', re.IGNORECASE)
NAME_RE = re.compile(r'name=["\']([^"\']+)["\']', re.IGNORECASE)

IGNORE_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")
IGNORE_EXTENSIONS = {
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".xml",
    ".txt",
    ".pdf",
    ".zip",
    ".mp3",
    ".mp4",
}

# Hosted on same domain but outside this repo (project pages / external apps).
CROSS_REPO_PATH_PREFIXES = (
    "towboogie-build",
)


@dataclass
class Issue:
    source: str
    href: str
    reason: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit internal/external links for PacificMeister site")
    p.add_argument("--site-dir", type=Path, default=Path("."), help="Site root (contains *.html)")
    p.add_argument("--check-external", action="store_true", help="Also check external URLs")
    p.add_argument("--external-limit", type=int, default=200, help="Max external URLs to check")
    p.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout seconds for external checks")
    p.add_argument("--fail-on-external", action="store_true", help="Return exit=1 when external checks fail")
    p.add_argument("--write-report", type=Path, default=None, help="Write markdown report path")
    return p.parse_args()


def discover_html(site_dir: Path) -> list[Path]:
    return sorted(site_dir.glob("*.html"))


def parse_hrefs(html_text: str) -> list[str]:
    return [unescape(href.strip()) for href in HREF_RE.findall(html_text)]


def parse_anchors(html_text: str) -> set[str]:
    anchors = {a.strip() for a in ID_RE.findall(html_text)}
    anchors |= {a.strip() for a in NAME_RE.findall(html_text)}
    return {a for a in anchors if a}


def is_external(href: str) -> bool:
    p = urlparse(href)
    return p.scheme in {"http", "https"}


def is_template_href(href: str) -> bool:
    return "${" in href or "{{" in href


def should_ignore_internal_asset(href: str) -> bool:
    path = (urlparse(href).path or "").lower()
    return any(path.endswith(ext) for ext in IGNORE_EXTENSIONS)


def is_cross_repo_same_domain_path(href: str) -> bool:
    parsed = urlparse(href)
    path = (parsed.path or "").lstrip("/")
    if not path:
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in CROSS_REPO_PATH_PREFIXES)


def normalize_internal_target(source: Path, href: str, site_dir: Path) -> tuple[Path, str | None] | None:
    href = href.strip()
    if not href or href.startswith("#"):
        return source, href[1:] if href.startswith("#") and len(href) > 1 else None
    if href.startswith(IGNORE_SCHEMES):
        return None
    if is_external(href):
        return None

    parsed = urlparse(href)
    rel = parsed.path or ""
    anchor = parsed.fragment if parsed.fragment else None

    if rel.startswith("/"):
        target = site_dir / rel.lstrip("/")
    else:
        target = (source.parent / rel).resolve()

    return target, anchor


def check_external(url: str, timeout: float) -> tuple[bool, str]:
    ua = "Mozilla/5.0 (compatible; PacificMeisterLinkAudit/1.0)"
    req = Request(url, headers={"User-Agent": ua}, method="HEAD")
    try:
        with urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            if code >= 400:
                return False, f"HTTP {code}"
            return True, f"HTTP {code}"
    except HTTPError as e:
        # Retry with GET for servers that reject HEAD.
        if e.code in {403, 405, 406, 429}:
            try:
                req_get = Request(url, headers={"User-Agent": ua}, method="GET")
                with urlopen(req_get, timeout=timeout) as resp:
                    code = getattr(resp, "status", 200)
                    return (code < 400), f"HTTP {code}"
            except HTTPError as e2:
                return False, f"HTTP {e2.code}"
            except URLError as e2:
                return False, f"URL error: {e2.reason}"
        return False, f"HTTP {e.code}"
    except URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"Error: {type(e).__name__}: {e}"


def write_report(
    out_path: Path,
    scanned_pages: int,
    scanned_links: int,
    broken_internal: list[Issue],
    external_checked: int,
    broken_external: list[Issue],
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# PacificMeister Link Health Report",
        "",
        f"- Generated: {now}",
        f"- HTML pages scanned: {scanned_pages}",
        f"- Links scanned: {scanned_links}",
        f"- Broken internal links: {len(broken_internal)}",
        f"- External URLs checked: {external_checked}",
        f"- External issues: {len(broken_external)}",
        "",
        "## Broken Internal Links",
        "",
    ]

    if broken_internal:
        for i in broken_internal:
            lines.append(f"- `{i.source}` → `{i.href}` ({i.reason})")
    else:
        lines.append("- None ✅")

    lines.extend(["", "## External Link Issues", ""])
    if broken_external:
        for i in broken_external:
            lines.append(f"- `{i.source}` → `{i.href}` ({i.reason})")
    else:
        lines.append("- None ✅")

    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    if not site_dir.exists():
        print(f"ERROR: site dir not found: {site_dir}", file=sys.stderr)
        return 2

    html_files = discover_html(site_dir)
    if not html_files:
        print("ERROR: no HTML files found", file=sys.stderr)
        return 2

    anchor_map: dict[Path, set[str]] = {}
    href_map: dict[Path, list[str]] = {}
    for html in html_files:
        text = html.read_text(encoding="utf-8", errors="ignore")
        anchor_map[html.resolve()] = parse_anchors(text)
        href_map[html.resolve()] = parse_hrefs(text)

    broken_internal: list[Issue] = []
    seen_internal: set[tuple[str, str, str]] = set()
    external_sources: dict[str, str] = {}
    scanned_links = 0

    for source, hrefs in href_map.items():
        for href in hrefs:
            scanned_links += 1
            if not href or href.startswith(IGNORE_SCHEMES) or is_template_href(href):
                continue

            if is_external(href):
                external_sources.setdefault(href, source.name)
                continue

            if should_ignore_internal_asset(href):
                continue

            if is_cross_repo_same_domain_path(href):
                path = urlparse(href).path or href
                if not path.startswith("/"):
                    path = "/" + path
                external_sources.setdefault(f"https://pacificmeister.github.io{path}", source.name)
                continue

            target_info = normalize_internal_target(source, href, site_dir)
            if target_info is None:
                continue
            target_path, anchor = target_info
            target_path = target_path.resolve()

            if target_path.is_dir():
                target_path = (target_path / "index.html").resolve()

            if not target_path.exists():
                issue_key = (source.name, href, "target file missing")
                if issue_key not in seen_internal:
                    seen_internal.add(issue_key)
                    broken_internal.append(Issue(source.name, href, "target file missing"))
                continue

            if anchor:
                anchors = anchor_map.get(target_path)
                if anchors is None:
                    text = target_path.read_text(encoding="utf-8", errors="ignore")
                    anchors = parse_anchors(text)
                    anchor_map[target_path] = anchors
                if anchor not in anchors:
                    reason = f"missing anchor #{anchor}"
                    issue_key = (source.name, href, reason)
                    if issue_key not in seen_internal:
                        seen_internal.add(issue_key)
                        broken_internal.append(Issue(source.name, href, reason))

    broken_external: list[Issue] = []
    external_checked = 0

    if args.check_external:
        for url in sorted(external_sources.keys())[: max(args.external_limit, 0)]:
            ok, detail = check_external(url, timeout=args.timeout)
            external_checked += 1
            if not ok:
                broken_external.append(Issue(external_sources[url], url, detail))

    print("PacificMeister link health audit")
    print(f"HTML pages scanned: {len(html_files)}")
    print(f"Links scanned: {scanned_links}")
    print(f"Broken internal links: {len(broken_internal)}")
    print(f"External URLs checked: {external_checked}")
    print(f"External issues: {len(broken_external)}")

    if args.write_report:
        out_path = args.write_report
        if not out_path.is_absolute():
            out_path = site_dir / out_path
        write_report(out_path, len(html_files), scanned_links, broken_internal, external_checked, broken_external)
        print(f"Report written: {out_path}")

    if broken_internal:
        return 1
    if args.fail_on_external and broken_external:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
