#!/usr/bin/env python3
"""Submit changed PacificMeister URLs to IndexNow.

Designed for GitHub Actions on push to main.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Submit changed URLs to IndexNow")
    p.add_argument("--site-dir", type=Path, default=Path("."))
    p.add_argument("--host", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--key-location", required=True)
    p.add_argument("--base-ref", default="")
    p.add_argument("--head-ref", default="HEAD")
    p.add_argument("--endpoint", default="https://api.indexnow.org/indexnow")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def list_changed_files(site_dir: Path, base_ref: str, head_ref: str) -> list[str]:
    zero = "0" * 40
    if not base_ref or base_ref == zero:
        out = run_git(["show", "--name-only", "--pretty=", head_ref], site_dir)
    else:
        out = run_git(["diff", "--name-only", f"{base_ref}..{head_ref}"], site_dir)
    return [line.strip() for line in out.splitlines() if line.strip()]


def parse_sitemap_urls(sitemap_path: Path, host: str) -> list[str]:
    if not sitemap_path.exists():
        return []
    tree = ET.parse(sitemap_path)
    root = tree.getroot()
    urls: list[str] = []
    for loc in root.findall(".//sm:loc", SITEMAP_NS):
        if not loc.text:
            continue
        parsed = urlparse(loc.text)
        if parsed.scheme in {"http", "https"} and parsed.netloc == host:
            urls.append(loc.text)
    return urls


def to_url(path: str, host: str) -> str | None:
    if not path.endswith(".html"):
        return None
    name = Path(path).name
    if name.startswith("_"):
        return None
    return f"https://{host}/{name}"


def submit(endpoint: str, host: str, key: str, key_location: str, urls: list[str], dry_run: bool) -> int:
    if not urls:
        print("No URLs to submit.")
        return 0

    payload = {
        "host": host,
        "key": key,
        "keyLocation": key_location,
        "urlList": urls,
    }

    print(f"Submitting {len(urls)} URL(s) to IndexNow")
    for u in urls:
        print(f"- {u}")

    if dry_run:
        print("Dry run enabled, skipping request.")
        return 0

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.getcode()
        body = resp.read().decode("utf-8", errors="ignore")
        print(f"IndexNow response: HTTP {status}")
        if body:
            print(body)
        return 0 if 200 <= status < 300 else 1


def main() -> int:
    args = parse_args()
    site_dir = args.site_dir.resolve()

    try:
        changed = list_changed_files(site_dir, args.base_ref, args.head_ref)
    except Exception as exc:
        print(f"ERROR determining changed files: {exc}", file=sys.stderr)
        return 2

    urls: set[str] = set()
    sitemap_changed = False

    for rel in changed:
        rel_path = Path(rel)
        if rel_path.parts and rel_path.parts[0] == ".github":
            continue
        if rel_path.name == "sitemap.xml":
            sitemap_changed = True
            continue
        u = to_url(rel, args.host)
        if u:
            urls.add(u)

    if sitemap_changed:
        for u in parse_sitemap_urls(site_dir / "sitemap.xml", args.host):
            urls.add(u)

    return submit(
        endpoint=args.endpoint,
        host=args.host,
        key=args.key,
        key_location=args.key_location,
        urls=sorted(urls),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
