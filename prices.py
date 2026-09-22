#!/usr/bin/env python3
"""Watchlist prices for the Morning Brief.

Reads watchlist.txt, pulls daily closes from free, keyless sources (Yahoo chart
API, CNBC, FRED) and writes two files:

  out/watchlist.json   every number, with its dates and source
  out/watchlist.html   the Watchlist block (email-safe table), ready to paste into the brief

The rule is one line: last completed close vs the close before it, each dated.
On Tue-Fri that is yesterday vs the day before; on Sat-Mon it is Friday vs
Thursday, with no special casing. A session still running is never used for
the delta; it goes to the "Em curso" block with its time. Anything that cannot
be retrieved is n/d. Nothing is estimated, interpolated or carried forward.

Standard library only (plus the tzdata package on Windows).
"""
import argparse
import csv
import html
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LISBON = ZoneInfo("Europe/Lisbon")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
NNBSP, MINUS = " ", "−"
LIVE_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}


# ---------- transport ----------

def http_get(url, ua=BROWSER_UA, tries=3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            err = e
        except Exception as e:  # timeouts, resets
            err = e
        time.sleep(1.5 * (i + 1))
    raise err


# ---------- sources ----------
# Each returns (closes, live): closes is [(date, float)] of completed sessions,
# oldest first; live is None or {"price", "time", "in_session", "type"}.

def yahoo(symbol, now):
    path = f"/v8/finance/chart/{urllib.parse.quote(symbol)}?range=1mo&interval=1d"
    try:
        raw = http_get("https://query1.finance.yahoo.com" + path)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise
        raw = http_get("https://query2.finance.yahoo.com" + path)
    res = json.loads(raw)["chart"]["result"][0]
    meta = res["meta"]
    off = meta.get("gmtoffset") or 0

    def local_date(epoch):
        return datetime.fromtimestamp(epoch + off, timezone.utc).date()

    bars = {}
    for t, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0].get("close") or []):
        if c is not None:
            bars[local_date(t)] = c  # a later bar on the same date wins

    today = local_date(now.timestamp())
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = reg.get("start") or 0, reg.get("end") or 0
    # Today's bar counts as a close only once today's regular session has ended.
    today_closed = bool(end) and local_date(end) == today and now.timestamp() >= end
    live = None
    if today in bars and not today_closed:
        live = {"price": meta.get("regularMarketPrice") or bars[today],
                "time": meta.get("regularMarketTime"),
                "in_session": start <= now.timestamp() < end,
                "type": meta.get("instrumentType")}
        del bars[today]
    return sorted(bars.items()), live


def cnbc(symbol, now):
    start = (now - timedelta(days=40)).strftime("%Y%m%d000000")
    end = now.strftime("%Y%m%d235959")
    url = (f"https://ts-api.cnbc.com/harmony/app/bars/{urllib.parse.quote(symbol)}"
           f"/1D/{start}/{end}/adjusted/EST5EDT.json")
    bars = json.loads(http_get(url))["barData"]["priceBars"] or []
    # European bonds and Stoxx indices are done by 18:00 CET; the rest rolls at 17:00 New York.
    eu_clock = symbol.endswith(("-DE", "-PT", "-IT", "-ES", "-FR")) or symbol.startswith(".SX")
    tz, close_hour = (ZoneInfo("Europe/Berlin"), 18) if eu_clock else (ZoneInfo("America/New_York"), 17)
    out = {}
    for b in bars:
        d = datetime.strptime(b["tradeTime"][:8], "%Y%m%d").date()
        if d.weekday() >= 5 or b.get("close") in (None, ""):
            continue  # CNBC emits stray Saturday bars with post-close ticks
        out[d] = float(b["close"])
    local = now.astimezone(tz)
    live = None
    if local.date() in out and local.hour < close_hour:
        live = {"price": out.pop(local.date()), "time": None, "in_session": True, "type": "RATE"}
    return sorted(out.items()), live


def fred(series, now):
    since = (now - timedelta(days=45)).date().isoformat()
    # FRED stalls on browser user agents from scripts; a plain one works.
    raw = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={since}",
                   ua="curl/8.9.1")
    rows = list(csv.reader(io.StringIO(raw)))[1:]
    return [(date.fromisoformat(d), float(v)) for d, v in rows if v not in (".", "")], None


FETCHERS = {"yahoo": yahoo, "cnbc": cnbc, "fred": fred}


# ---------- formatting (pt-PT) ----------

def num(x, dp, signed=False):
    x = round(x, dp)
    body = f"{abs(x):,.{dp}f}".replace(",", NNBSP).replace(".", ",")
    if x == 0:
        return body
    if signed:
        return ("+" if x > 0 else MINUS) + body
    return (MINUS if x < 0 else "") + body


def auto_dp(x):
    return 0 if abs(x) >= 1000 else 2 if abs(x) >= 10 else 3


def measure(unit, p0, p1):
    """Return (level, change, level_str, change_str) in the unit's convention."""
    if unit == "yld":
        chg = (p1 - p0) * 100
        return p1, chg, num(p1, 2) + "%", num(chg, 1, True) + " pb"
    if unit == "bp":
        chg = p1 - p0
        return p1, chg, num(p1, 0) + " pb", num(chg, 1, True) + " pb"
    if unit == "oas":
        chg = (p1 - p0) * 100
        return p1 * 100, chg, num(p1 * 100, 0) + " pb", num(chg, 0, True) + " pb"
    if unit == "pts":
        chg = p1 - p0
        return p1, chg, num(p1, 2), num(chg, 2, True) + " pts"
    chg = (p1 / p0 - 1) * 100
    if unit in ("fx4", "fx2"):
        return p1, chg, num(p1, 4 if unit == "fx4" else 2), num(chg, 2, True) + "%"
    return p1, chg, num(p1, auto_dp(p1)), num(chg, 1, True) + "%"


def streak(closes):
    """Consecutive sessions moving the same way, signed (+3 = three up days)."""
    n, sign = 0, 0
    pairs = list(zip(closes, closes[1:]))
    for (_, a), (_, b) in reversed(pairs):
        s = (b > a) - (b < a)
        if s == 0 or (sign and s != sign):
            break
        sign, n = s, n + 1
    return n * sign


def ddmm(d):
    return d.strftime("%d/%m")


# ---------- core ----------

def read_watchlist(path):
    lines = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        group, name, source, unit = [p.strip() for p in raw.split("|")]
        lines.append({"group": group, "name": name, "source": source, "unit": unit})
    return lines


def build(watchlist, now):
    cache = {}

    def series(kind, sym):
        key = f"{kind}:{sym}"
        if key not in cache:
            try:
                cache[key] = FETCHERS[kind](sym, now)
            except Exception as e:
                cache[key] = e
            time.sleep(0.25)
        return cache[key]

    rows = []
    for w in watchlist:
        row = dict(w, status="nd", reason="", closes=[], live=None)
        if w["source"] == "manual":
            row["reason"] = "sem fonte gratuita"
            rows.append(row)
            continue

        for src in [s.strip() for s in w["source"].split(",")]:
            kind, sym = src.split(":", 1)
            if kind == "spread":
                a, b = (series("cnbc", s) for s in sym.split("/"))
                if isinstance(a, Exception) or isinstance(b, Exception):
                    row["reason"] = "falha na fonte"
                    continue
                db = dict(b[0])
                closes = [(d, (v - db[d]) * 100) for d, v in a[0] if d in db]
                live = None
            else:
                got = series(kind, sym)
                if isinstance(got, Exception):
                    row["reason"] = "falha na fonte"
                    continue
                closes, live = got
            if len(closes) < 2:
                row["reason"] = "sem fechos suficientes"
                continue
            row.update(closes=closes, live=live, used=src, status="ok", reason="")
            break
        rows.append(row)

    for r in rows:
        if r["status"] != "ok":
            continue
        (d0, p0), (d1, p1) = r["closes"][-2], r["closes"][-1]
        lvl, chg, ls, cs = measure(r["unit"], p0, p1)
        r.update(close_date=d1, prev_date=d0, level=lvl, change=chg,
                 level_fmt=ls, change_fmt=cs, streak=streak(r["closes"][-12:]))
    return rows


def live_rows(rows, now):
    """Sessions running now: cash equities in regular hours, plus the Em curso group."""
    out = []
    for r in rows:
        lv = r.get("live")
        if not lv or r["status"] != "ok":
            continue
        wanted = r["group"] == "Em curso" or (
            lv["in_session"] and lv["type"] in LIVE_TYPES and r["unit"] in ("idx", "px"))
        if not wanted:
            continue
        _, chg, ls, cs = measure(r["unit"], r["closes"][-1][1], lv["price"])
        t = datetime.fromtimestamp(lv["time"], LISBON).strftime("%H:%M") if lv["time"] else ""
        out.append({"name": r["name"], "price": lv["price"], "change": chg,
                    "level_fmt": ls, "change_fmt": cs, "time": t,
                    "vs_close": r["closes"][-1][0]})
    return out


def reference_dates(rows):
    ok = [r for r in rows if r["status"] == "ok" and r["group"] != "Em curso"]
    if not ok:
        return None, None
    close = Counter(r["close_date"] for r in ok).most_common(1)[0][0]
    prev = Counter(r["prev_date"] for r in ok if r["close_date"] == close).most_common(1)[0][0]
    return close, prev


# Email-safe markup: tables and inline styles only, because Gmail drops CSS
# variables and colour-scheme media queries. Colours match template.html.
INK, STRONG, MUTED, RULE, RULE2 = "#E8E8E8", "#FFFFFF", "#8C8C8C", "#1F1F1F", "#3A3A3A"
UP, DOWN = "#8FD9B0", "#F29C94"
SANS = "'Helvetica Neue',Helvetica,Arial,sans-serif"


def render_html(rows, live, now, ref_close, ref_prev):
    e = html.escape
    cell = f"padding:9px 0;border-bottom:1px solid {RULE};vertical-align:top"

    def group(title):
        return (f'<tr><td colspan="3" style="padding:22px 0 7px;border-bottom:1px solid {RULE2};'
                f'font-size:11px;font-weight:bold;letter-spacing:2px;text-transform:uppercase;'
                f'color:{MUTED}">{e(title)}</td></tr>')

    def line(name, sub, level, change, na=False):
        # Direction follows the sign as displayed, so "0,0%" never gets an arrow.
        d = "up" if change.startswith("+") else "down" if change.startswith(MINUS) else ""
        colour = MUTED if na or not d else UP if d == "up" else DOWN
        arrow = "" if na or not d else "▲ " if d == "up" else "▼ "
        weight = "normal" if na else "bold"
        sub = f'<br><span style="font-size:11px;color:{MUTED}">{sub}</span>' if sub else ""
        return (f'<tr><td style="{cell}">{e(name)}{sub}</td>'
                f'<td align="right" style="{cell};padding-right:14px;white-space:nowrap;'
                f'color:{MUTED if na else STRONG}">{level}</td>'
                f'<td align="right" style="{cell};white-space:nowrap;font-weight:{weight};'
                f'color:{colour}">{arrow}{change}</td></tr>')

    out = [f'<tr><td style="padding:30px 0 0;border-top:1px solid {RULE2}">',
           f'<div style="font-size:11px;font-weight:bold;letter-spacing:3px;text-transform:uppercase;'
           f'color:{STRONG}"><span style="color:#6E6E6E">&#9670;</span>&nbsp;&nbsp;Watchlist</div>']
    if ref_close:
        out.append(f'<div style="margin-top:8px;font-size:13px;line-height:1.5;color:{MUTED}">'
                   f'Fechos de {ddmm(ref_close)} face a {ddmm(ref_prev)} · recolha '
                   f'{now.astimezone(LISBON):%H:%M} (Lisboa)</div>')
    out.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
               f'style="font-family:{SANS};font-size:15px;line-height:1.35;color:{INK};'
               f'font-variant-numeric:tabular-nums">')
    group_name = None
    for r in rows:
        if r["group"] == "Em curso":
            continue
        if r["group"] != group_name:
            group_name = r["group"]
            out.append(group(group_name))
        if r["status"] != "ok":
            out.append(line(r["name"], "", "n/d", e(r["reason"]), na=True))
            continue
        sub = []
        if r["close_date"] != ref_close:
            sub.append(f"fecho {ddmm(r['close_date'])}")
        if abs(r["streak"]) >= 3:
            sub.append(f"{'↑' if r['streak'] > 0 else '↓'}{abs(r['streak'])} sessões")
        out.append(line(r["name"], " · ".join(sub), r["level_fmt"], r["change_fmt"]))
    if live:
        out.append(group("Em curso"))
        for lv in live:
            out.append(line(lv["name"], f"{lv['time']} · em curso" if lv["time"] else "em curso",
                            lv["level_fmt"], lv["change_fmt"]))
    out.append('</table>')
    out.append('<!-- NOTAS: comentários da watchlist entram aqui, antes do fecho da célula -->')
    out.append('</td></tr>')
    return "\n".join(out) + "\n"


def to_json(rows, live, now, ref_close, ref_prev):
    def clean(r):
        keep = ["group", "name", "unit", "status", "reason", "used", "level", "change",
                "level_fmt", "change_fmt", "streak", "close_date", "prev_date"]
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items() if k in keep}
    return {
        "generated_at": now.astimezone(LISBON).isoformat(timespec="minutes"),
        "reference": {"close": ref_close and ref_close.isoformat(),
                      "prev": ref_prev and ref_prev.isoformat()},
        "lines": [clean(r) for r in rows if r["group"] != "Em curso"],
        "live": [dict(lv, vs_close=lv["vs_close"].isoformat()) for lv in live],
    }


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--watchlist", default=here / "watchlist.txt")
    ap.add_argument("--out", default=here / "out")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    rows = build(read_watchlist(args.watchlist), now)
    live = live_rows(rows, now)
    ref_close, ref_prev = reference_dates(rows)

    sys.stdout.reconfigure(encoding="utf-8")
    table = [r for r in rows if r["group"] != "Em curso"]
    ok = sum(r["status"] == "ok" for r in table)
    if ok < len(table) / 2:
        # A source is down or blocking us. Leave yesterday's files untouched, so the
        # brief sees a stale timestamp and says so, and fail so GitHub sends an email.
        print(f"only {ok}/{len(table)} lines retrieved; not overwriting output")
        for r in table:
            if r["status"] != "ok":
                print(f"  n/d  {r['name']}: {r['reason']}")
        sys.exit(1)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "watchlist.html").write_text(render_html(rows, live, now, ref_close, ref_prev), encoding="utf-8")
    (out / "watchlist.json").write_text(
        json.dumps(to_json(rows, live, now, ref_close, ref_prev), ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"{ok}/{len(table)} lines · reference {ref_close} vs {ref_prev} · {len(live)} live")
    for r in table:
        if r["status"] != "ok":
            print(f"  n/d  {r['name']}: {r['reason']}")


if __name__ == "__main__":
    main()
