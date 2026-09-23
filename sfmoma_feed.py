#!/usr/bin/env python3
"""
Build RSS feeds for SFMOMA press releases and Stories ("Read") articles.

Outputs (in docs/, for GitHub Pages):
  press.xml    press releases only
  stories.xml  Stories articles only
  all.xml      both combined

state.json remembers every item seen, so each article page is fetched once
and items keep stable dates across runs.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------------------------------------------------------
# Configuration: edit these
# --------------------------------------------------------------------------
FEED_BASE_URL = "https://geoweb999.github.io/sfmoma-feed"  # GitHub Pages URL
USER_AGENT = "sfmoma-feed/1.0 (personal RSS reader; contact: you@example.com)"

MAX_ITEMS_PER_SOURCE = 30   # how far down each listing page to look
MAX_ITEMS_PER_FEED = 50     # entries written to each feed file
MAX_DETAIL_FETCHES = 60     # article pages fetched per run (first run is the big one)
REQUEST_DELAY = 1.5         # seconds between requests, to be polite

BASE = "https://www.sfmoma.org"
PACIFIC = ZoneInfo("America/Los_Angeles")
ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "docs"
STATE_FILE = ROOT / "state.json"

SOURCES = {
    "press": {
        "title": "SFMOMA Press Releases",
        "description": "Press releases from the SFMOMA Press Room",
        "listing": f"{BASE}/press/release/",
        "path_pattern": re.compile(r"^/press-release/[^/?#]+/?$"),
    },
    "stories": {
        "title": "SFMOMA Stories",
        "description": "Articles from SFMOMA Stories (Read)",
        "listing": f"{BASE}/read/",
        "path_pattern": re.compile(r"^/read/[^/?#]+/?$"),
    },
}

MONTHS = ("January|February|March|April|May|June|July|August|"
          "September|October|November|December")
DATE_RE = re.compile(rf"\b({MONTHS})\s+\d{{1,2}},\s+\d{{4}}\b")
GENERIC_DESCRIPTION = "San Francisco Museum of Modern Art"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})
    retry = Retry(total=3, backoff_factor=2,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch(session: requests.Session, url: str) -> str:
    time.sleep(REQUEST_DELAY)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
@dataclass
class ListingEntry:
    url: str
    title: str | None
    date: datetime | None


def parse_listing_date(text: str) -> datetime:
    """'March 26, 2026' -> noon Pacific that day (avoids off-by-one in readers)."""
    d = datetime.strptime(re.sub(r"\s+", " ", text), "%B %d, %Y")
    return d.replace(hour=12, tzinfo=PACIFIC)


def canonical(url: str) -> str:
    p = urlparse(url)
    path = p.path if p.path.endswith("/") else p.path + "/"
    return f"https://www.sfmoma.org{path}"


def parse_listing(html: str, cfg: dict) -> list[ListingEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ListingEntry] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        p = urlparse(urljoin(BASE, a["href"]))
        if not p.netloc.endswith("sfmoma.org") or not cfg["path_pattern"].match(p.path):
            continue
        url = canonical(p.geturl())
        if url in seen:
            continue
        seen.add(url)

        text = " ".join(a.get_text(" ", strip=True).split())
        date = None
        m = DATE_RE.search(text)
        if m:
            date = parse_listing_date(m.group(0))
            text = text[m.end():].strip()
        # Stories cards prefix the title with a "read" label
        text = re.sub(r"^read\s+", "", text, flags=re.IGNORECASE).strip()
        entries.append(ListingEntry(url=url, title=text or None, date=date))

    return entries


def meta_content(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def parse_article(html: str) -> dict:
    """Pull title, summary, image and publish date from an article page."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {}

    title = meta_content(soup, "og:title")
    if title:
        out["title"] = re.sub(r"\s*[·|]\s*SFMOMA\s*$", "", title)

    desc = meta_content(soup, "og:description") or meta_content(soup, "description")
    if not desc or desc == GENERIC_DESCRIPTION:
        # Fall back to the first substantial paragraph of body text
        main = soup.find("main") or soup.find("article") or soup
        for p in main.find_all("p"):
            t = " ".join(p.get_text(" ", strip=True).split())
            if len(t) > 80 and not t.startswith("Released:"):
                desc = t
                break
    if desc and desc != GENERIC_DESCRIPTION:
        out["summary"] = desc if len(desc) <= 300 else desc[:297].rsplit(" ", 1)[0] + "…"

    image = meta_content(soup, "og:image")
    if image:
        out["image"] = image

    published = meta_content(soup, "article:published_time")
    if published:
        try:
            out["published"] = datetime.fromisoformat(published.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    if "published" not in out:
        m = re.search(rf"Released:\s*({MONTHS})\s+\d{{1,2}},\s+\d{{4}}", soup.get_text(" "))
        if m:
            out["published"] = parse_listing_date(m.group(0).split(":", 1)[1].strip()).isoformat()

    return out


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"items": {}, "fingerprints": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Feed output
# --------------------------------------------------------------------------
def item_date(rec: dict) -> datetime:
    return datetime.fromisoformat(rec.get("published") or rec["first_seen"])


def build_feed(name: str, title: str, description: str, link: str,
               records: list[tuple[str, dict]], state: dict) -> None:
    records = sorted(records, key=lambda kv: item_date(kv[1]), reverse=True)[:MAX_ITEMS_PER_FEED]

    # Skip rewriting if nothing changed, so we don't commit on every run
    fingerprint = hashlib.sha256(
        json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    out_path = OUT_DIR / f"{name}.xml"
    if state["fingerprints"].get(name) == fingerprint and out_path.exists():
        print(f"{name}.xml unchanged")
        return

    fg = FeedGenerator()
    fg.id(f"{FEED_BASE_URL}/{name}.xml")
    fg.title(title)
    fg.description(description)
    fg.link(href=f"{FEED_BASE_URL}/{name}.xml", rel="self")
    fg.link(href=link, rel="alternate")  # set last so <link> points at the site
    fg.language("en")

    # feedgen prepends entries, so add oldest first to get newest-first output
    for url, rec in reversed(records):
        fe = fg.add_entry()
        fe.id(url)
        fe.guid(url, permalink=True)
        fe.link(href=url)
        label = "Press release" if rec["source"] == "press" else "Story"
        fe.title(rec.get("title") or url)
        fe.category(term=label)
        fe.published(item_date(rec))
        body = ""
        if rec.get("image"):
            body += f'<p><img src="{escape(rec["image"])}" alt="" style="max-width:100%"></p>'
        body += f"<p>{escape(rec.get('summary') or rec.get('title') or '')}</p>"
        body += f'<p><a href="{escape(url)}">Read on sfmoma.org</a></p>'
        fe.description(body)

    OUT_DIR.mkdir(exist_ok=True)
    fg.rss_file(str(out_path), pretty=True)
    state["fingerprints"][name] = fingerprint
    print(f"wrote {out_path.name} ({len(records)} items)")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    state = load_state()
    session = make_session()
    now = datetime.now(timezone.utc).isoformat()
    detail_budget = MAX_DETAIL_FETCHES
    problems: list[str] = []

    for key, cfg in SOURCES.items():
        try:
            html = fetch(session, cfg["listing"])
        except requests.RequestException as e:
            problems.append(f"{key}: could not fetch listing ({e})")
            continue

        entries = parse_listing(html, cfg)[:MAX_ITEMS_PER_SOURCE]
        if not entries:
            problems.append(f"{key}: no items found on {cfg['listing']} "
                            "(the page markup or URL pattern may have changed)")
            continue
        print(f"{key}: {len(entries)} items on listing")

        for e in entries:
            rec = state["items"].setdefault(e.url, {"source": key, "first_seen": now})
            if e.title and not rec.get("title"):
                rec["title"] = e.title
            if e.date:  # the press listing date is authoritative
                rec["published"] = e.date.isoformat()

            if not rec.get("details_fetched") and detail_budget > 0:
                detail_budget -= 1
                try:
                    details = parse_article(fetch(session, e.url))
                except requests.RequestException as ex:
                    print(f"  warning: could not fetch {e.url}: {ex}")
                    continue
                if e.date:  # press: listing title and date beat page metadata
                    details.pop("published", None)
                    details.pop("title", None)
                rec.update(details)
                rec["details_fetched"] = True
                print(f"  new: {rec.get('title', e.url)}")

    save_state(state)

    by_source = {k: [(u, r) for u, r in state["items"].items() if r["source"] == k]
                 for k in SOURCES}
    for key, cfg in SOURCES.items():
        build_feed(key, cfg["title"], cfg["description"], cfg["listing"], by_source[key], state)
    build_feed("all", "SFMOMA: Press Releases + Stories",
               "SFMOMA press releases and Stories articles, combined",
               BASE, [kv for recs in by_source.values() for kv in recs], state)
    save_state(state)

    for p in problems:
        print(f"::warning::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
