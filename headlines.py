#!/usr/bin/env python3
"""Headline sweep for the Morning Brief.

Reads feeds.txt, collects what each outlet published inside the window and writes:

  out/headlines.txt    one line per headline, grouped by outlet, for the brief to read
  out/headlines.json   the same items with timestamps and links, plus the feeds that failed

The text file leaves out Google News links, which run to 270 characters; the JSON
keeps every link so the brief can cite and link its sources.

Headlines only, with their time and link. They are leads: the brief still has to
corroborate anything it uses against a fact source or a primary document. A feed
that does not answer is recorded as a gap instead of being hidden.

Standard library only (plus the tzdata package on Windows).
"""
import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LISBON = ZoneInfo("Europe/Lisbon")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PER_FEED, PER_SEARCH = 12, 8   # keep the file small enough to paste into a prompt
ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


def window_hours(now):
    """Same window as the brief: 72h on Monday, 48h at the weekend, 24h otherwise."""
    wd = now.astimezone(LISBON).weekday()
    return 72 if wd == 0 else 48 if wd >= 5 else 24


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml,*/*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
    charset = r.headers.get_content_charset()
    if not charset:
        m = re.search(rb'encoding=["\']([\w-]+)["\']', raw[:200])
        charset = m.group(1).decode() if m else "utf-8"
    return raw.decode(charset, "replace")


def when_published(entry):
    for tag in ("pubDate", "published", ATOM + "published", ATOM + "updated", "updated", DC + "date"):
        el = entry.find(tag)
        if el is None or not (el.text or "").strip():
            continue
        text = el.text.strip()
        try:
            return parsedate_to_datetime(text) if "," in text else datetime.fromisoformat(text)
        except (TypeError, ValueError):
            continue
    return None


def entry_link(entry):
    el = entry.find("link")
    if el is not None and (el.text or "").strip():
        return el.text.strip()
    for el in entry.findall(ATOM + "link"):
        if el.get("rel", "alternate") == "alternate" and el.get("href"):
            return el.get("href")
    return ""


def clean_title(text, outlet):
    text = html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()
    text = re.sub(r"\s+", " ", text)
    # Google News appends " - Outlet" to every title
    return re.sub(r"\s+[-–]\s+[^-–]{2,40}$", "", text) if "news.google" in outlet else text


def parse(xml_text, google):
    root = ET.fromstring(xml_text)
    entries = root.findall(".//item") or root.findall(".//" + ATOM + "entry")
    out = []
    for e in entries:
        title_el = e.find("title") if e.find("title") is not None else e.find(ATOM + "title")
        title = clean_title(title_el.text if title_el is not None else "", "news.google" if google else "")
        if title:
            out.append({"title": title, "url": entry_link(e), "published": when_published(e)})
    return out


def read_feeds(path, when):
    feeds = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, url = (p.strip() for p in line.split("|", 1))
            feeds.append((name, url.replace("{when}", when)))
    return feeds


def collect(feeds, now, hours):
    cutoff = now - timedelta(hours=hours)
    items, failed, quiet, seen = [], [], [], set()
    for name, url in feeds:
        google = "news.google" in url
        try:
            entries = parse(http_get(url), google)
        except urllib.error.HTTPError as e:
            failed.append({"feed": name, "reason": f"HTTP {e.code}"})
            continue
        except Exception as e:
            failed.append({"feed": name, "reason": type(e).__name__})
            continue
        kept = 0
        for it in entries:
            when = it["published"]
            if when is None or when.tzinfo is None:
                continue  # no timestamp means no way to place it in the window
            if when < cutoff or when > now + timedelta(hours=2):
                continue
            key = " ".join(re.sub(r"[^\w\s]", "", it["title"].lower()).split()[:8])
            if key in seen:
                continue  # the same story syndicated across outlets
            seen.add(key)
            # Google News links are redirects: too long for the text file, but the brief
            # needs them to cite a source, so they stay in the JSON.
            items.append({"outlet": name, "title": it["title"], "url": it["url"],
                          "search": google, "published": when.astimezone(timezone.utc)})
            kept += 1
            if kept >= (PER_SEARCH if google else PER_FEED):
                break
        if not kept:
            quiet.append(name)
    items.sort(key=lambda i: i["published"], reverse=True)
    return items, failed, quiet


def render_txt(items, failed, quiet, now, hours):
    today = now.astimezone(LISBON).date()

    def stamp(dt):
        local = dt.astimezone(LISBON)
        return f"{local:%H:%M}" if local.date() == today else f"{local:%d/%m %H:%M}"

    lines = [f"Headlines · janela {hours}h até {now.astimezone(LISBON):%d/%m %H:%M} (Lisboa) · "
             f"{len(items)} títulos", ""]
    for outlet in dict.fromkeys(i["outlet"] for i in items):
        lines.append(outlet)
        for i in [x for x in items if x["outlet"] == outlet]:
            lines.append(f"  {stamp(i['published']):>11}  {i['title']}"
                         + ("" if i["search"] else f"  {i['url']}"))
        lines.append("")
    if quiet:
        lines.append("Sem novidades na janela: " + " · ".join(quiet))
    if failed:
        lines.append("Feeds sem resposta: " + " · ".join(f"{f['feed']} ({f['reason']})" for f in failed))
    return "\n".join(lines) + "\n"


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--feeds", default=here / "feeds.txt")
    ap.add_argument("--out", default=here / "out")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    hours = window_hours(now)
    feeds = read_feeds(args.feeds, f"{hours // 24}d")
    items, failed, quiet = collect(feeds, now, hours)

    sys.stdout.reconfigure(encoding="utf-8")
    if not items:
        print("no headlines retrieved; not overwriting output")
        for f in failed:
            print(f"  {f['feed']}: {f['reason']}")
        sys.exit(1)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "headlines.txt").write_text(render_txt(items, failed, quiet, now, hours), encoding="utf-8")
    (out / "headlines.json").write_text(json.dumps({
        "generated_at": now.astimezone(LISBON).isoformat(timespec="minutes"),
        "window_hours": hours,
        "items": [dict(i, published=i["published"].isoformat(timespec="minutes")) for i in items],
        "failed": failed,
        "quiet": quiet,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(items)} headlines from {len(feeds) - len(failed) - len(quiet)}/{len(feeds)} feeds "
          f"· window {hours}h")
    for f in failed:
        print(f"  gap    {f['feed']}: {f['reason']}")
    if quiet:
        print("  quiet  " + " · ".join(quiet))


if __name__ == "__main__":
    main()
