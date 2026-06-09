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

# ──────────────────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────────────────
REPO      = "suryaobb/trading-suite"
FILE_PATH = "apartments.html"
GH_TOKEN  = os.environ["GITHUB_TOKEN"]
GH_API    = "https://api.github.com"

MAX_PRICE = 5000
MIN_BEDS  = 1
TOP_N     = 6          # max new cards to add per run
MIN_SCORE = 4          # minimum keyword score to include

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


def mark_card_expired(html, url):
    """
    Find the card containing `url` and:
      - Change data-status="available" → data-status="expired"
      - Add [EXPIRED] prefix to card-address
    """
    # Find which card contains this URL
    idx = html.find(f'href="{url}"')
    if idx == -1:
        return html

    # Walk backwards to find the opening <div class="card"
    start = html.rfind('<div class="card"', 0, idx)
    if start == -1:
        return html

    # Replace status
    segment = html[start:idx]
    segment = re.sub(r'data-status="available"', 'data-status="expired"', segment)

    # Tag the address line
    addr_idx = segment.find('class="card-address">')
    if addr_idx != -1:
        insert_at = start + addr_idx + len('class="card-address">')
        if not html[insert_at:insert_at + 9].startswith("[EXPIRED]"):
            html = html[:insert_at] + "[EXPIRED] " + html[insert_at:]
            # Adjust idx since we inserted text
            idx += len("[EXPIRED] ")

    html = html[:start] + segment + html[start + len(segment):]
    return html


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


def item_to_card(item, n):
    cid   = f"cl-scan-{n}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = re.sub(r'[<>"&]', '', item["title"])[:100]
    price = f"${item['price']:,}" if item["price"] else "Price TBD"
    hood  = guess_hood(item["title"] + " " + item["desc"])
    link  = item["link"]
    return f"""\n  <!-- auto-scan {today} score={score(item)} -->\n  <div class="card" id="{cid}" data-track="scan" data-price="{item['price']}" data-status="available">\n    <div class="card-photo-placeholder" style="background: linear-gradient(135deg, #101828 0%, #1c2940 100%);">Auto-Found</div>\n    <div class="card-body">\n      <div class="card-top">\n        <div class="card-price">{price}</div>\n        <div class="card-badges">\n          <span class="badge badge-new">🤖 Auto</span>\n          <span class="badge badge-watch">Craigslist</span>\n        </div>\n      </div>\n      <div>\n        <div class="card-address">{title}</div>\n        <div class="card-hood">{hood}</div>\n      </div>\n      <div class="card-specs">\n        <span class="spec">1+ BR</span>\n        <span class="spec">Unverified</span>\n      </div>\n      <div class="card-features">\n        <span class="feature hi">New listing — verify details</span>\n      </div>\n      <div class="card-note">Auto-found on {today}. Click through to the Craigslist listing to see photos, full description, and contact info. Links expire after ~30 days.</div>\n      <div class="card-footer">\n        <a class="btn-link" href="{link}" target="_blank">View on Craigslist</a>\n        <div class="rating-bar">\n          <button class="btn-love" onclick="rateCard('{cid}','love',this)">Save</button>\n          <button class="btn-pass" onclick="rateCard('{cid}','pass',this)">Pass</button>\n        </div>\n      </div>\n      <div class="loved-stamp">Saved</div>\n    </div>\n  </div>\n"""


# ──────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────
def main():
    print("📡  Fetching apartments.html from GitHub…")
    html, sha = get_file()
    n_cards_before = card_count(html)
    print(f"    {n_cards_before} cards, {len(existing_urls(html))} links\n")
    changed = False

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

    # ── 2. Craigslist scan ──────────────────────────────────
    print("🔍  Scanning Craigslist…")
    seen_urls = existing_urls(html)
    all_items: list[dict] = []
    for rss_url in CL_SEARCHES:
        items = fetch_cl(rss_url)
        all_items += [i for i in items if i["link"] not in seen_urls]
        time.sleep(1.2)

    # Deduplicate by URL
    seen_cl: set[str] = set()
    unique: list[dict] = []
    for i in all_items:
        if i["link"] not in seen_cl:
            seen_cl.add(i["link"])
            unique.append(i)

    scored = sorted([(score(i), i) for i in unique if score(i) >= MIN_SCORE], reverse=True)
    print(f"    {len(unique)} new items · {len(scored)} pass score ≥ {MIN_SCORE}\n")

    n_added = 0
    next_num = n_cards_before + 1
    for s, item in scored[:TOP_N]:
        print(f"    ✨ score={s:3d}  ${item['price']:,}  {item['title'][:60]}")
        card_html = item_to_card(item, next_num)
        html = html.replace("</div><!-- /grid -->", card_html + "</div><!-- /grid -->")
        changed = True
        n_added += 1
        next_num += 1

    # ── 3. Commit ────────────────────────────────────────────
    if changed:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        msg   = f"Auto-scan {today}: +{n_added} new, {len(dead)} expired"
        print(f"\n📤  Pushing: {msg}")
        put_file(html, sha, msg)
        print("✅  Done!")
    else:
        print("\n✅  No changes.")


if __name__ == "__main__":
    main()
