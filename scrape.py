#!/usr/bin/env python3
"""Donegal Farm weaner calf price feed.

Reads two public weekly sources and writes prices.json (served on GitHub Pages):
  * RPO  – https://rpo.co.za/carcass-prices/  (weaner calf R/kg, bulls, national; long weekly history)
  * AMT  – https://amtrends.co.za/market-pricesv2/ (weaners 200-250 kg headline) and the latest
           "AMT Weekly Livestock Report" PDF (weaner classes + regional 200-250 kg prices)
RMIS (https://rmis.co.za/prices/) is listed as a link only – its terms forbid automated collection,
so it is never fetched.

Each source is handled on its own: if one fails, its last good figures are kept and marked with the
error, and the other source still updates. Python standard library only; PDF text via poppler's
`pdftotext` (apt package poppler-utils).

Usage: python3 scrape.py [prices.json]
"""
import datetime, html, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request

RPO_URL = "https://rpo.co.za/carcass-prices/"
AMT_URL = "https://amtrends.co.za/market-pricesv2/"
AMT_REPORTS_URL = "https://amtrends.co.za/livestock-weekly-reports-amtrends/"
RMIS_URL = "https://rmis.co.za/prices/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 donegal-prices-feed (private family use)"
SANE = (10.0, 150.0)          # R/kg live – anything outside is treated as a parse error
AMT_KEEP = 104                # weekly AMT points kept (about two years)
AMT_PDF_MAX_NEW = 4           # PDFs downloaded per run at most (each is ~10 MB)

AMT_CLASSES = [("light", "Light weaners (160-200 kg)", ["Light", "weaners"]),
               ("heavy", "Heavy weaners (250-300 kg)", ["Heavy", "weaners"]),
               ("top40", "Weaners top 40% (200-240 kg)", ["top", "40%"])]
AMT_REGIONS = [("FS", "Free State", ["Free", "State"]), ("NW", "North West", ["North", "West"]),
               ("EC", "Eastern Cape", ["Eastern", "Cape"]), ("MP", "Mpumalanga", ["Mpumalanga"]),
               ("WC", "Western Cape", ["Western", "Cape"]), ("KZN", "KwaZulu-Natal", ["Kwazulu", "Natal"])]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch(url, binary=False, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-ZA,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            return data if binary else data.decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def num(s):
    """'R 46,12' / '48.94 (bulls)' / '1 234,5' -> float, or None."""
    if s is None:
        return None
    m = re.search(r"\d[\d\s\u00a0]*(?:[.,]\d+)?", str(s))
    if not m:
        return None
    t = re.sub(r"[\s\u00a0]", "", m.group(0)).replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def sane(v):
    return v is not None and SANE[0] <= v <= SANE[1]


def dmy(s):
    """'13/11/2026' or '7/01/2022' -> '2026-11-13'."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def cell_text(x):
    x = re.sub(r"<br\s*/?>", " ", x, flags=re.I)
    x = re.sub(r"<[^>]+>", " ", x)
    return re.sub(r"\s+", " ", html.unescape(x)).strip()


# ----------------------------------------------------------------------------------- RPO
def parse_rpo(page):
    """All weekly weaner-calf rows from every year table on the RPO carcass-prices page.
    Returns [{date, week_ending, value}] oldest first. 2026 rows give a publication date plus
    'week ended'; older tables list one date per row (headed 'Week Ending' / 'Weekeinde')."""
    out = {}
    for t in re.findall(r"<table.*?</table>", page, re.S | re.I):
        rows = [[cell_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)]
                for r in re.findall(r"<tr.*?</tr>", t, re.S | re.I)]
        col = None
        for r in rows[:4]:
            for i, c in enumerate(r):
                if re.search(r"weaner\s*calf|speenkalf", c, re.I):
                    col = i
            if col is not None:
                break
        if col is None:
            continue
        parsed = []
        for r in rows:
            if len(r) <= col or not dmy(r[0]):
                continue
            v = num(r[col])
            if not sane(v):
                continue
            first = dmy(r[0])
            we = re.search(r"week\s*ended?\s*(\d{1,2}/\d{1,2}/\d{4})", r[0], re.I)
            parsed.append({"date": first if we else None, "week_ending": dmy(we.group(1)) if we else first, "value": round(v, 2)})
        if not parsed:
            continue
        # typo guard: a row whose year differs from the table's year (e.g. '10/11/2021' in the 2023 table)
        years = [p["week_ending"][:4] for p in parsed]
        main_year = max(set(years), key=years.count)
        for p in parsed:
            for k in ("week_ending", "date"):
                if p[k] and p[k][:4] != main_year and abs(int(p[k][:4]) - int(main_year)) > 1:
                    p[k] = main_year + p[k][4:]
        for p in parsed:
            out[p["week_ending"]] = p
    hist = [out[k] for k in sorted(out)]
    if len(hist) < 5:
        raise ValueError("RPO: weaner calf column not found (page layout may have changed)")
    return hist


# ----------------------------------------------------------------------------------- AMT
def parse_amt_html(page):
    """Headline 'Weaners (200-250kg)' row from AMT's market prices page."""
    i = page.find("Weaners (200-250kg)")
    if i < 0:
        raise ValueError("AMT: 'Weaners (200-250kg)' row not found")
    row = page[page.rfind("<tr", 0, i):page.find("</tr>", i)]
    cells = {k: cell_text(v) for k, v in re.findall(r'data-th="([^"]*)"[^>]*>(.*?)</td>', row, re.S)}
    v, prev = num(cells.get("Price")), num(cells.get("Previous Price"))
    d = re.search(r"\d{4}-\d{2}-\d{2}", cells.get("Date", ""))
    if not sane(v) or not d:
        raise ValueError("AMT: weaner price or date unreadable")
    ch = re.search(r"-?\d+(?:[.,]\d+)?", cells.get("Change", "") or "")
    return {"date": d.group(0), "value": round(v, 2), "previous": round(prev, 2) if sane(prev) else None,
            "change_pct": float(ch.group(0).replace(",", ".")) if ch else None}


def find_amt_pdfs(page):
    """[(date, report_no, url)] for weekly livestock report PDFs linked on the AMT reports page, newest last."""
    seen = {}
    for u in re.findall(r'https?://[^"\'\s<>]+?Weekly-Livestock-Report[^"\'\s<>]*?\.pdf', page, re.I):
        m = re.search(r"No-(\d+)-(\d{4})\.(\d{2})\.(\d{2})", u)
        if m:
            seen[u] = ("%s-%s-%s" % (m.group(2), m.group(3), m.group(4)), int(m.group(1)), u)
    return sorted(seen.values())


def pdf_words(pdf_path, last_page=4):
    """Words with positions from the first pages of a PDF (poppler pdftotext -bbox)."""
    out = subprocess.run(["pdftotext", "-bbox", "-f", "1", "-l", str(last_page), pdf_path, "-"],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    return bbox_words(out)


def bbox_words(bbox_html):
    pages, cur = [], None
    for m in re.finditer(r'<page\b[^>]*>|<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', bbox_html):
        if m.group(0).startswith("<page"):
            cur = []
            pages.append(cur)
        elif cur is not None:
            cur.append({"x0": float(m.group(1)), "y0": float(m.group(2)), "x1": float(m.group(3)), "y1": float(m.group(4)),
                        "t": html.unescape(m.group(5))})
    return pages


RVAL = re.compile(r"^R\d{1,3}[,.]\d{2}$")


def _phrase(words, parts):
    """bbox of the first place the words in `parts` appear in order on one line."""
    for i, w in enumerate(words):
        if w["t"].lower() != parts[0].lower():
            continue
        span = [w]
        for p in parts[1:]:
            nxt = [x for x in words if x["t"].lower().startswith(p.lower()) and abs(x["y0"] - w["y0"]) < 4 and 0 <= x["x0"] - span[-1]["x1"] < 40]
            if not nxt:
                break
            span.append(nxt[0])
        if len(span) == len(parts):
            # stretch to the end of the label (e.g. '(200-250kg)')
            line = sorted([x for x in words if abs(x["y0"] - w["y0"]) < 4 and x["x0"] >= w["x0"]], key=lambda x: x["x0"])
            x1 = span[-1]["x1"]
            for x in line:
                if x["x0"] - x1 < 8 and x["x0"] >= span[-1]["x0"]:
                    x1 = max(x1, x["x1"])
            return {"x0": w["x0"], "x1": x1, "y0": w["y0"], "y1": max(s["y1"] for s in span)}
    return None


def _value_below(words, box, max_dy=110):
    """Nearest 'R46,85'-style value below a label and within its column."""
    cands = []
    for w in words:
        if not RVAL.match(w["t"]):
            continue
        cx = (w["x0"] + w["x1"]) / 2
        dy = w["y0"] - box["y1"]
        if dy < -2 or dy > max_dy or not (box["x0"] - 35 <= cx <= box["x1"] + 35):
            continue
        cands.append((dy + 0.3 * abs(cx - (box["x0"] + box["x1"]) / 2), num(w["t"])))
    return min(cands)[1] if cands else None


def parse_amt_pdf(pages):
    """Weaner headline, classes and regional prices from the 'Cattle Market Weekly Report' page."""
    for words in pages:
        text = " ".join(w["t"] for w in words)
        if "Weaner" not in text or "Regional" not in text:
            continue
        res = {"classes": [], "regions": []}
        dm = re.search(r"(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* (\d{4})", text)
        if dm:
            res["date"] = datetime.datetime.strptime("%s %s %s" % dm.groups(), "%d %b %Y").date().isoformat()
        live = _phrase(words, ["-", "Live"]) or _phrase(words, ["Live"])
        if live:
            live = dict(live, x0=live["x0"] - 60, x1=live["x1"] + 20)
            res["headline"] = _value_below(words, live, 80)
        for key, label, parts in AMT_CLASSES:
            b = _phrase(words, parts)
            v = _value_below(words, b) if b else None
            if sane(v):
                res["classes"].append({"id": key, "label": label, "value": v})
        for key, label, parts in AMT_REGIONS:
            b = _phrase(words, parts)
            v = _value_below(words, b) if b else None
            if sane(v):
                res["regions"].append({"id": key, "label": label + " (200-250 kg)", "value": v})
        if len(res["classes"]) + len(res["regions"]) >= 5:
            return res
    raise ValueError("AMT PDF: weaner class / regional table not found")


# ----------------------------------------------------------------------------------- feed
def base_sources():
    return [
        {"id": "rpo", "name": "RPO – Red Meat Producers’ Organisation", "url": RPO_URL,
         "series": "Weaner calf (bulls), national, R/kg live – weekly (from the Absa carcass price table published by RPO)",
         "attribution": "Source: Red Meat Producers’ Organisation (RPO), rpo.co.za – weekly Absa carcass / weaner price table. Reproduced for private family use with attribution.",
         "status": "pending", "error": None, "checked_at": None, "last_success": None, "latest": None, "history": []},
        {"id": "amt", "name": "AMT (amtrends.co.za)", "url": AMT_URL, "reports_url": AMT_REPORTS_URL,
         "series": "Weaners 200-250 kg, R/kg live – weekly; classes and regional prices from the AMT Weekly Livestock Report",
         "attribution": "Source: AMT – amtrends.co.za market prices and AMT Weekly Livestock Report. Reproduced for private family use with attribution.",
         "status": "pending", "error": None, "checked_at": None, "last_success": None, "latest": None,
         "report": None, "classes": [], "regions": [], "history": []},
        {"id": "rmis", "name": "RMIS – Red Meat Industry Services", "url": RMIS_URL, "link_only": True, "status": "link",
         "attribution": "Official red meat price dashboard. Link only – RMIS figures are not collected automatically (their terms do not allow it)."},
    ]


def load_prev(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {s["id"]: s for s in d.get("sources", []) if isinstance(s, dict) and s.get("id")}
    except (OSError, ValueError):
        return {}


def run_rpo(src, get):
    hist = parse_rpo(get(RPO_URL))
    last = hist[-1]
    src["history"] = hist
    src["latest"] = {"date": last["date"], "week_ending": last["week_ending"], "value": last["value"],
                     "previous": hist[-2]["value"] if len(hist) > 1 else None}


def run_amt(src, get, get_pdf):
    errors = []
    head = parse_amt_html(get(AMT_URL))
    hist = {h["date"]: h for h in src.get("history") or [] if h.get("date")}
    pt = hist.setdefault(head["date"], {"date": head["date"]})
    pt["value"] = head["value"]
    # PDFs: classes + regions (optional – headline is kept if this part fails)
    try:
        pdfs = find_amt_pdfs(get(AMT_REPORTS_URL))
        if not pdfs:
            raise ValueError("no weekly livestock report PDF linked")
        todo = [p for p in pdfs if not (hist.get(p[0]) or {}).get("regions")][-AMT_PDF_MAX_NEW:]
        if pdfs[-1] not in todo and not (src.get("report") or {}).get("url") == pdfs[-1][2]:
            todo.append(pdfs[-1])
        for date, no, url in todo:
            try:
                r = parse_amt_pdf(get_pdf(url))
                d = r.get("date") or date
                h = hist.setdefault(d, {"date": d})
                if r.get("headline") and not h.get("value"):
                    h["value"] = r["headline"]
                h["classes"] = {c["id"]: c["value"] for c in r["classes"]}
                h["regions"] = {c["id"]: c["value"] for c in r["regions"]}
                h["report_no"] = no
                if (date, no, url) == pdfs[-1]:
                    src["report"] = {"no": no, "date": d, "url": url}
                    src["classes"], src["regions"] = r["classes"], r["regions"]
            except Exception as e:  # one bad PDF does not stop the rest
                errors.append("report No %s: %s" % (no, str(e)[:120]))
    except Exception as e:
        errors.append("reports page: %s" % str(e)[:120])
    h = [hist[k] for k in sorted(hist) if hist[k].get("value")][-AMT_KEEP:]
    src["history"] = h
    prev = head.get("previous")
    if prev is None and len(h) > 1:
        prev = h[-2]["value"]
    src["latest"] = {"date": head["date"], "week_ending": None, "value": head["value"], "previous": prev, "change_pct": head.get("change_pct")}
    if src.get("report") and src["report"]["date"] != head["date"]:
        errors.append("classes/regions are from report dated %s; headline is %s" % (src["report"]["date"], head["date"]))
    return errors


def pdf_from_url(url):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(fetch(url, binary=True))
        p = f.name
    try:
        return pdf_words(p)
    finally:
        os.unlink(p)


def build(out_path, get=fetch, get_pdf=pdf_from_url):
    prev = load_prev(out_path)
    sources = base_sources()
    t = now_iso()
    for s in sources:
        if s.get("link_only"):
            continue
        old = prev.get(s["id"]) or {}
        for k in ("latest", "history", "report", "classes", "regions", "last_success"):   # start from last good data
            if old.get(k) is not None and k in s:
                s[k] = old[k]
        s["checked_at"] = t
        try:
            warn = run_rpo(s, get) if s["id"] == "rpo" else run_amt(s, get, get_pdf)
            s["status"], s["error"], s["last_success"] = ("partial" if warn else "ok"), ("; ".join(warn) if warn else None), t
        except Exception as e:
            s["status"], s["error"] = "error", "%s: %s" % (type(e).__name__, str(e)[:200])
        print("%-4s %-7s latest=%s %s" % (s["id"], s["status"], json.dumps(s.get("latest")), s.get("error") or ""), flush=True)
    feed = {"schema": 1, "feed": "donegal-prices", "generated_at": t,
            "note": "Weekly weaner calf benchmark prices for the Donegal Farm Herd Tracker. Figures are reproduced for private family use with attribution to their publishers (RPO and AMT). National/regional auction averages – not a valuation of any animal.",
            "sources": sources}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out_path)
    return feed


if __name__ == "__main__":
    feed = build(sys.argv[1] if len(sys.argv) > 1 else "prices.json")
    ok = [s for s in feed["sources"] if s.get("latest")]
    sys.exit(0 if ok else 1)
