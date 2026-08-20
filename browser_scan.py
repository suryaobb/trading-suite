#!/usr/bin/env python3
"""
Headless-browser scraper for the JS-rendered rental sites that curl/urllib cannot read
(Zumper, HotPads). Uses Playwright (already installed by the daily workflow).

Exposes scan_browser() -> list[dict] with the SAME shape apt_scanner.py's Craigslist
items use: {"title","link","desc","price","source","hood","beds"}.

Design notes:
  * Every site is wrapped in try/except and short timeouts. If a site is blocked
    (datacenter IPs sometimes are) or slow, it contributes [] and the scan still
    succeeds on the other sources — never raises.
  * The in-page extractor is the exact DOM walk validated interactively: find price
    text nodes, climb to the enclosing card, grab its <a href> + innerText.
"""
import re, sys, json, time

# Target neighborhoods (substring match against listing text). Mirrors Surya's criteria.
HOODS = ["duboce", "castro", "mission", "hayes", "alamo", "dolores", "polk",
         "market st", "mccoppin", "waller", "fell", "guerrero", "steiner",
         "fillmore", "civic center", "upper market", "buena vista", "lower haight",
         "cole valley", "nopa", "western addition", "laguna", "oak st", "fulton",
         "scott", "pierce", "broderick", "franklin", "grove", "van ness", "natoma",
         "linda", "church", "noe"]
MIN_PRICE, MAX_PRICE = 2000, 5500

ZUMPER_HOODS = ["duboce-triangle", "castro", "hayes-valley", "alamo-square",
                "mission-dolores", "lower-haight"]

# Craigslist static-HTML search pages (no JS needed, but the browser fingerprint
# gets past the 403 that urllib/RSS now hits from datacenter IPs).
CRAIGSLIST_URLS = [
    "https://sfbay.craigslist.org/search/sfc/apa?min_price=2000&max_price=5500&bedrooms=1&search_distance=0.8&postal=94114&sort=date",
    "https://sfbay.craigslist.org/search/sfc/apa?min_price=2000&max_price=5500&bedrooms=1&search_distance=0.8&postal=94117&sort=date",
    "https://sfbay.craigslist.org/search/sfc/apa?query=loft+high+ceilings&min_price=2000&max_price=5500",
    "https://sfbay.craigslist.org/search/sfc/apa?query=victorian+renovated&min_price=2000&max_price=5500",
]
_CL_BLOCK = re.compile(r'<li class="cl-static-search-result"[^>]*>(.*?)</li>', re.S)

# In-page extractor: returns [{price, url, text}] for every listing card on screen.
JS_EXTRACT = r"""
() => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const out = [], seen = new Set(); let n;
  while ((n = w.nextNode())) {
    const t = n.nodeValue.trim();
    const pm = t.match(/^\$([\d,]{4,})/);
    if (!pm) continue;
    const price = parseInt(pm[1].replace(/,/g, ''));
    let c = n.parentElement;
    for (let i = 0; i < 8 && c; i++) {
      if (c.querySelector && c.querySelector('a[href]') && (c.innerText || '').length > 40) break;
      c = c.parentElement;
    }
    if (!c) continue;
    const a = c.querySelector('a[href*="/pad/"],a[href*="/apartment-buildings/"],a[href*="/listings/"],a[href*="/address/"],a[href]');
    let h = a ? a.getAttribute('href') : null;
    if (!h || /mapbox|\/about\//.test(h)) continue;
    const full = (c.innerText || '').replace(/\s+/g, ' ').trim();
    const url = h.indexOf('http') === 0 ? h : location.origin + h;
    if (seen.has(url)) continue; seen.add(url);
    out.push({ price, url, text: full.slice(0, 160) });
  }
  return out;
}
"""


def _parse(rec, source):
    """Turn a raw {price,url,text} into the apt_scanner item shape, or None if off-criteria."""
    txt = rec.get("text", "")
    low = txt.lower()
    price = rec.get("price") or 0
    if not (MIN_PRICE <= price <= MAX_PRICE):
        return None
    bm = re.search(r"(\d+)\s*bed", low)
    beds = int(bm.group(1)) if bm else (0 if "studio" in low else 1)
    if beds < 1:                       # Surya wants 1BR+
        return None
    if not any(h in low for h in HOODS):
        return None
    # Strip leading "$x,xxx - $y,yyy [Total price]" then any bed/bath/units/status meta,
    # so neither the price nor a unit-count is mistaken for a street number.
    body = re.sub(r"^\$[\d,]+(?:\s*-\s*\$[\d,]+)?(?:\s*Total price)?\s*", "", txt).strip()
    meta = (r"(?:studio|\d+\s*-?\s*\d*\s*(?:beds?|baths?)|\d+\s*units? available|"
            r"total price|new today|price drop|verified|featured|·|\||[\s.,–-])")
    body = re.sub(rf"^(?:{meta})+", "", body, flags=re.I).strip()
    # Prefer a street-number-anchored address; else the text just before "San Francisco".
    am = re.search(r"\d{2,5}\s+[A-Za-z][A-Za-z0-9 .'#/&-]*?(?=\s+San Francisco|,)", body)
    addr = am.group(0).strip() if am else re.split(r"\s+San Francisco", body)[0].strip()
    addr = " ".join(addr.split()[:7])[:70] or "San Francisco"
    hood = next((h for h in HOODS if h in low), "san francisco").title()
    return {"title": addr, "link": rec["url"], "desc": txt, "price": price,
            "source": source, "hood": hood, "beds": beds}


def scan_browser(verbose=True):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        if verbose:
            print(f"  ⚠️  Playwright unavailable ({e}); skipping browser sources.")
        return []

    raw, out = [], []
    seen_urls = set()

    def collect(recs, source):
        for r in recs:
            if r["url"] in seen_urls:
                continue
            seen_urls.add(r["url"])
            item = _parse(r, source)
            if item:
                out.append(item)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}, locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(20000)

        # ---- Craigslist (static HTML, character units — best-quality source) ----
        for url in CRAIGSLIST_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                html = page.content()
                recs = []
                for blk in _CL_BLOCK.findall(html):
                    href = re.search(r'<a href="([^"]+)"', blk)
                    title = re.search(r'<div class="title">(.*?)</div>', blk, re.S)
                    price = re.search(r'<div class="price">\$?([\d,]+)</div>', blk)
                    loc = re.search(r'<div class="location">\s*(.*?)\s*</div>', blk, re.S)
                    if href and title:
                        p = int(price.group(1).replace(",", "")) if price else 0
                        # Let _parse() infer beds (drops studios) & hood from the title text.
                        recs.append({"price": p, "url": href.group(1),
                                     "text": f"{title.group(1).strip()} {loc.group(1).strip() if loc else ''} San Francisco, CA"})
                collect(recs, "Craigslist")
                if verbose:
                    print(f"    Craigslist: {len(recs)} static results")
                page.wait_for_timeout(400)
            except Exception as e:
                if verbose:
                    print(f"    ⚠️  Craigslist failed: {str(e)[:80]}")

        # ---- Zumper (best source: individual character units) ----
        for slug in ZUMPER_HOODS:
            url = (f"https://www.zumper.com/apartments-for-rent/san-francisco-ca/{slug}"
                   f"?min-price={MIN_PRICE}&max-price={MAX_PRICE}&min-beds=1")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3500)          # let the map/list API hydrate
                recs = page.evaluate(JS_EXTRACT)
                collect(recs, "Zumper")
                if verbose:
                    print(f"    Zumper/{slug}: {len(recs)} cards seen")
            except Exception as e:
                if verbose:
                    print(f"    ⚠️  Zumper/{slug} failed: {str(e)[:80]}")

        # ---- HotPads (works, but mostly big complexes; hood filter trims it) ----
        try:
            page.goto("https://hotpads.com/san-francisco-ca/apartments-for-rent"
                      f"?price={MIN_PRICE}-{MAX_PRICE}&beds=1-5",
                      wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3500)
            recs = page.evaluate(JS_EXTRACT)
            collect(recs, "HotPads")
            if verbose:
                print(f"    HotPads: {len(recs)} cards seen")
        except Exception as e:
            if verbose:
                print(f"    ⚠️  HotPads failed: {str(e)[:80]}")

        browser.close()

    # de-dupe by (address, price)
    dedup, keys = [], set()
    for it in out:
        k = re.sub(r"[^a-z0-9]", "", it["title"].lower()) + str(it["price"])
        if k not in keys:
            keys.add(k)
            dedup.append(it)
    if verbose:
        print(f"    → {len(dedup)} in-criteria 1BR+ listings from browser sources")
    return dedup


if __name__ == "__main__":
    items = scan_browser()
    print(json.dumps(items, indent=2))
