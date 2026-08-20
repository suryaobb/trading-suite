#!/usr/bin/env python3
"""
SF Apartment Auto-Scanner
Runs daily via GitHub Actions:
  1. Checks every existing listing link — marks dead ones as expired
  2. Scrapes multiple Craigslist searches for new loft/character listings
  3. Scores matches against Surya's criteria and appends top cards
  4. Commits the updated apartments.html back to GitHub Pages
"""

import os, re, json, base64, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Headless-browser scraper for JS-rendered sites (Zumper/HotPads). Optional: if the
# module or Playwright is missing (e.g. a local run without it), we degrade to
# Craigslist-only rather than failing the whole scan.
try:
    from browser_scan import scan_browser
except Exception:
    def scan_browser(verbose=True):
        print("  ⚠️  browser_scan unavailable; Craigslist-only this run.")
        return []

# ──────────────────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────────────────
REPO      = "suryaobb/trading-suite"
FILE_PATH = "apartments.html"
GH_TOKEN  = os.environ["GITHUB_TOKEN"]
GH_API    = "https://api.github.com"

MAX_PRICE = 5000
MIN_BEDS  = 1
TOP_N        = 6       # max new Craigslist cards to add per run
BROWSER_TOP_N = 12     # max new browser-sourced (Zumper/HotPads) cards per run
MIN_SCORE    = 4       # minimum keyword score to include (Craigslist only)

# Keyword tiers
STRONG  = ["loft", "warehouse", "live/work", "live work", "exposed brick",
           "exposed concrete", "raw space", "open loft", "converted warehouse"]
MED     = ["high ceiling", "high ceilings", "vaulted", "skylights", "industrial",
           "victorian", "edwardian", "craftsman", "period detail", "character unit",
           "open plan", "open floor", "converted", "loft-style", "loft style"]
LIGHT   = ["hardwood", "remodeled", "renovated", "original detail", "vintage",
           "unique", "special", "dramatic", "soaring", "dramatic windows"]
SKIP    = ["roommate", "room for rent", "room only", "shared room",
           "sublet", "short term", "furnished only", "airbnb"]
HOODS   = ["mission", "dolores", "hayes valley", "hayes", "castro",
           "nopa", "no pa", "potrero hill", "potrero", "soma", "south of market",
           "civic center", "tenderloin", "duboce", "bernal heights", "bernal",
           "noe valley", "corona heights", "cole valley", "lower haight",
           "upper haight", "haight", "fillmore", "buena vista", "glen park",
           "mid-market", "market street", "inner sunset", "richmond"]

# Craigslist RSS searches (all SF apartments, max $5k, with photo)
CL_SEARCHES = [
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&query=loft&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&query=warehouse&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&query=exposed+brick&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&query=high+ceilings&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&query=victorian&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&query=converted&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
    f"https://sfbay.craigslist.org/search/sfc/apa?format=rss&max_price={MAX_PRICE}&min_bedrooms={MIN_BEDS}&hasPic=1",
]


# ──────────────────────────────────────────────────────────
#  GitHub API helpers
# ──────────────────────────────────────────────────────────
def gh_api(method, path, body=None):
    url  = f"{GH_API}/{path}"
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {GH_TOKEN}",
        "Content-Type":  "application/json",
        "User-Agent":    "apt-scanner/2.0",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_file():
    j       = gh_api("GET", f"repos/{REPO}/contents/{FILE_PATH}")
    content = base64.b64decode(j["content"].replace("\n", "")).decode("utf-8")
    return content, j["sha"]


def put_file(content, sha, message):
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    gh_api("PUT", f"repos/{REPO}/contents/{FILE_PATH}", {
        "message": message,
        "content": encoded,
        "sha":     sha,
    })


# ──────────────────────────────────────────────────────────
#  Link checker
# ──────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

def check_url(url, timeout=10):
    """Returns (is_live: bool, http_status: int|None)"""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status < 400, r.status
        except urllib.error.HTTPError as e:
            if e.code == 405 and method == "HEAD":
                continue       # retry as GET
            return False, e.code
        except Exception:
            return False, None
    return False, None


# ──────────────────────────────────────────────────────────
#  HTML helpers
# ──────────────────────────────────────────────────────────
def existing_urls(html):
    return set(re.findall(r'href="(https?://[^"]+)"', html))


def existing_card_ids(html):
    return set(re.findall(r'id="(card-[^"]+)"', html))


def card_count(html):
    return len(re.findall(r'class="card"', html))


# Matches the anchor-tag corruption produced by the OLD mark_card_expired() and
# by the earlier browser-injection task: <a class="btn-link" ="btn-link" btn-link" … href=
# (also the site-link variant). Collapses the repeated garbage back to a clean opener.
_CORRUPT_ANCHOR = re.compile(r'<a class="([a-z]+-link)"(?:\s+(?:="|")?[a-z]*-link")+\s+href=')

def repair_corruption(html):
    """Self-healing: undo any accumulated anchor-tag corruption before we touch the file.
    Idempotent — a clean file is returned unchanged. Returns (html, n_fixed)."""
    n = len(_CORRUPT_ANCHOR.findall(html))
    html = _CORRUPT_ANCHOR.sub(lambda m: f'<a class="{m.group(1)}" href=', html)
    # stray leading digit artifact e.g. "     2<div class=\"card-specs\">"
    html = re.sub(r'^\s*\d(<div class="card-specs">)', r'      \1', html, flags=re.M)
    return html, n


def mark_card_expired(html, url):
    """
    Find the card containing `url` and:
      - Change data-status="available" → data-status="expired"
      - Add [EXPIRED] prefix to card-address (once)

    Safe rewrite: isolate the whole card substring by its real boundaries, edit that
    isolated string, then splice it back in ONE piece. The old version mixed a
    length-changed `segment` with a separately-mutated `html`, which duplicated the
    region from card-address through the <a class="btn-link"> opener on every expiry —
    compounding into massive corruption across hourly runs.
    """
    idx = html.find(f'href="{url}"')
    if idx == -1:
        return html
    start = html.rfind('<div class="card"', 0, idx)
    if start == -1:
        return html
    # Card boundary = start of the next card, else end of grid, else EOF.
    nxt = html.find('<div class="card"', idx)
    grid_close = html.find('<!-- /grid -->', idx)
    ends = [e for e in (nxt, grid_close) if e != -1]
    end = min(ends) if ends else len(html)

    card = html[start:end]
    card = card.replace('data-status="available"', 'data-status="expired"', 1)
    card = re.sub(r'(class="card-address">)(?!\[EXPIRED\])', r'\1[EXPIRED] ', card, count=1)
    return html[:start] + card + html[end:]


# ──────────────────────────────────────────────────────────
#  Craigslist RSS scraper
# ──────────────────────────────────────────────────────────
def fetch_cl(rss_url):
    try:
        req = urllib.request.Request(rss_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        root  = ET.fromstring(raw)
        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            link  = item.findtext("link",  "").strip()
            desc  = item.findtext("description", "").strip()
            price_m = re.search(r"\$(\d[\d,]+)", title + " " + desc)
            price = int(price_m.group(1).replace(",", "")) if price_m else 0
            items.append({"title": title, "link": link, "desc": desc, "price": price})
        return items
    except Exception as e:
        print(f"  ⚠️  CL fetch error ({rss_url[:60]}…): {e}")
        return []


def score(item):
    text = (item["title"] + " " + item["desc"]).lower()
    if any(k in text for k in SKIP):
        return -99
    if item["price"] > MAX_PRICE or (0 < item["price"] < 800):
        return -1   # unrealistic price
    s = 0
    for k in STRONG: s += 3 * text.count(k)
    for k in MED:    s += 2 * text.count(k)
    for k in LIGHT:  s += 1 * text.count(k)
    if any(h in text for h in HOODS): s += 2
    return s


def guess_hood(text):
    text = text.lower()
    for h in HOODS:
        if h in text:
            return h.title()
    return "San Francisco"


SRC_COLOR = {"Craigslist": "#1c2940", "Zumper": "#14324a", "HotPads": "#14401f"}

def item_to_card(item, n):
    source = item.get("source", "Craigslist")
    cid    = f"scan-{re.sub(r'[^a-z0-9]', '', source.lower())}-{n}"
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title  = re.sub(r'[<>"&]', '', item["title"])[:100]
    price  = f"${item['price']:,}" if item["price"] else "Price TBD"
    hood   = item.get("hood") or guess_hood(item["title"] + " " + item["desc"])
    beds   = item.get("beds", 1) or 1
    col    = SRC_COLOR.get(source, "#101828")
    link   = item["link"]
    return f"""\n  <!-- auto-scan {today} {source} score={score(item)} -->\n  <div class="card" id="{cid}" data-track="scan" data-price="{item['price']}" data-status="available" data-source="{source}">\n    <div class="card-photo-placeholder" style="background: linear-gradient(135deg, #101828 0%, {col} 100%);">{source}</div>\n    <div class="card-body">\n      <div class="card-top">\n        <div class="card-price">{price}</div>\n        <div class="card-badges">\n          <span class="badge badge-new">🤖 Auto</span>\n          <span class="badge badge-src">{source}</span>\n        </div>\n      </div>\n      <div>\n        <div class="card-address">{title}</div>\n        <div class="card-hood">{hood}</div>\n      </div>\n      <div class="card-specs">\n        <span class="spec">{beds}+ BR</span>\n        <span class="spec">Unverified</span>\n      </div>\n      <div class="card-features">\n        <span class="feature hi">New listing — verify details</span>\n      </div>\n      <div class="card-note">Auto-found on {today} via {source}. Click through to see photos, full description, and contact info. Verify before you tour.</div>\n      <div class="card-footer">\n        <a class="btn-link" href="{link}" target="_blank">View on {source}</a>\n        <div class="rating-bar">\n          <button class="btn-love" onclick="rateCard('{cid}','love',this)">Save</button>\n          <button class="btn-pass" onclick="rateCard('{cid}','pass',this)">Pass</button>\n        </div>\n      </div>\n      <div class="loved-stamp">Saved</div>\n    </div>\n  </div>\n"""


# ──────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────
def main():
    print("📡  Fetching apartments.html from GitHub…")
    html, sha = get_file()
    changed = False

    # ── 0. Self-heal any accumulated anchor-tag corruption ──
    html, n_repaired = repair_corruption(html)
    if n_repaired:
        changed = True
        print(f"🧹  Repaired {n_repaired} corrupted anchor tags")

    n_cards_before = card_count(html)
    print(f"    {n_cards_before} cards, {len(existing_urls(html))} links\n")

    # ── 1. Link check ───────────────────────────────────────
    print("🔗  Checking existing links…")
    dead, live = [], []
    for url in sorted(existing_urls(html)):
        ok, status = check_url(url)
        label = "✅" if ok else "❌"
        print(f"    {label} {status or '---'}  {url[:90]}")
        (live if ok else dead).append(url)
        time.sleep(0.4)   # polite delay

    if dead:
        for url in dead:
            html = mark_card_expired(html, url)
        changed = True
        print(f"\n    Marked {len(dead)} expired links\n")

    seen_urls = existing_urls(html)

    # ── 2a. Craigslist scan (RSS, keyword-scored) ───────────
    print("🔍  Scanning Craigslist…")
    all_items: list[dict] = []
    for rss_url in CL_SEARCHES:
        items = fetch_cl(rss_url)
        all_items += [i for i in items if i["link"] not in seen_urls]
        time.sleep(1.2)
    seen_cl: set[str] = set()
    unique: list[dict] = []
    for i in all_items:
        if i["link"] not in seen_cl:
            seen_cl.add(i["link"])
            i["source"] = "Craigslist"
            unique.append(i)
    scored = sorted([(score(i), i) for i in unique if score(i) >= MIN_SCORE], reverse=True)
    print(f"    {len(unique)} new items · {len(scored)} pass score ≥ {MIN_SCORE}\n")

    # ── 2b. Browser scan (Zumper/HotPads, pre-filtered to criteria) ──
    print("🌐  Scanning JS-rendered sites via headless browser…")
    browser_items = [b for b in scan_browser() if b["link"] not in seen_urls]
    print(f"    {len(browser_items)} new browser listings\n")

    # ── 2c. Merge, dedupe, add cards ─────────────────────────
    to_add: list[dict] = []
    picked: set[str] = set()
    # Craigslist keyword winners first, then browser-sourced units.
    for _, item in scored[:TOP_N]:
        if item["link"] not in picked:
            picked.add(item["link"]); to_add.append(item)
    for item in browser_items[:BROWSER_TOP_N]:
        if item["link"] not in picked:
            picked.add(item["link"]); to_add.append(item)

    n_added = 0
    next_num = n_cards_before + 1
    for item in to_add:
        print(f"    ✨ {item.get('source','?'):10} ${item['price']:,}  {item['title'][:52]}")
        card_html = item_to_card(item, next_num)
        html = html.replace("</div><!-- /grid -->", card_html + "</div><!-- /grid -->")
        changed = True
        n_added += 1
        next_num += 1

    # Ensure the source-badge style exists (older pages may lack it).
    if ".badge-src" not in html and ".filter-btn {" in html:
        html = html.replace(".filter-btn {",
            ".badge-src { background:#1a2b45; color:#8fc4ff; border:1px solid #2f5a8f; }\n.filter-btn {", 1)

    # ── 3. Commit ────────────────────────────────────────────
    if changed:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n_browser = sum(1 for i in to_add if i.get("source") in ("Zumper", "HotPads"))
        repaired = f", repaired {n_repaired}" if n_repaired else ""
        msg = (f"Auto-scan {today}: +{n_added} new ({n_browser} browser), "
               f"{len(dead)} expired{repaired}")
        print(f"\n📤  Pushing: {msg}")
        put_file(html, sha, msg)
        print("✅  Done!")
    else:
        print("\n✅  No changes.")


if __name__ == "__main__":
    main()
