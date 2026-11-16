"""Parser tests against pages saved from the real sites on 16 Nov 2026 (tests/fixtures)."""
import glob, json, os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import scrape  # noqa: E402

FX = os.path.join(HERE, "fixtures")
def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()
def amt_pdf(no):
    return scrape.bbox_words(fx(glob.glob(os.path.join(FX, "AMT-Weekly-Livestock-Report-No-%d-*.bbox.html" % no))[0].split(os.sep)[-1]))

PAGES = {scrape.RPO_URL: "rpo.html", scrape.AMT_URL: "amt.html", scrape.AMT_REPORTS_URL: "amt-weekly.html"}
def fake_get(url):
    return fx(PAGES[url])
def fake_pdf(url):
    import re
    return amt_pdf(int(re.search(r"No-(\d+)", url).group(1)))
def broken(url):
    raise OSError("network down (test)")


class RPO(unittest.TestCase):
    def setUp(self):
        self.h = scrape.parse_rpo(fx("rpo.html"))
    def test_latest(self):
        self.assertEqual(self.h[-1], {"date": "2026-11-13", "week_ending": "2026-11-06", "value": 48.94})
        self.assertEqual(self.h[-2]["value"], 49.33)
    def test_full_history(self):
        self.assertGreater(len(self.h), 200)
        self.assertEqual(self.h[0]["week_ending"][:4], "2022")
        wk = [x["week_ending"] for x in self.h]
        self.assertEqual(wk, sorted(wk)); self.assertEqual(len(wk), len(set(wk)))
    def test_values_sane(self):
        self.assertTrue(all(25 < x["value"] < 80 for x in self.h))
    def test_afrikaans_header_and_no_bulls_suffix(self):
        x = [p for p in self.h if p["week_ending"] == "2024-01-19"][0]
        self.assertEqual(x["value"], 36.34)
    def test_year_typo_fixed(self):   # source lists '10/11/2021' inside the 2023 table
        self.assertFalse(any(p["week_ending"].startswith("2021") for p in self.h))
        self.assertTrue(any(p["week_ending"] == "2023-11-10" for p in self.h))
    def test_layout_change_raises(self):
        with self.assertRaises(ValueError):
            scrape.parse_rpo("<html><table><tr><td>nothing</td></tr></table></html>")


class AMT(unittest.TestCase):
    def test_html_headline(self):
        self.assertEqual(scrape.parse_amt_html(fx("amt.html")), {"date": "2026-11-13", "value": 46.12, "previous": 47.08, "change_pct": -2.04})
    def test_html_missing_row(self):
        with self.assertRaises(ValueError):
            scrape.parse_amt_html("<html></html>")
    def test_pdf_links(self):
        l = scrape.find_amt_pdfs(fx("amt-weekly.html"))
        self.assertEqual([x[1] for x in l], [43, 44, 45, 46])
        self.assertEqual(l[-1][0], "2026-11-13")
        self.assertTrue(l[-1][2].endswith("AMT-Weekly-Livestock-Report-No-46-2026.11.13.pdf"))
    def test_pdf_46(self):
        r = scrape.parse_amt_pdf(amt_pdf(46))
        self.assertEqual(r["date"], "2026-11-13"); self.assertEqual(r["headline"], 46.12)
        self.assertEqual({c["id"]: c["value"] for c in r["classes"]}, {"light": 46.35, "heavy": 44.6, "top40": 48.27})
        self.assertEqual({c["id"]: c["value"] for c in r["regions"]}, {"FS": 46.85, "NW": 46.74, "EC": 46.61, "MP": 43.72, "WC": 43.27, "KZN": 41.03})
    def test_pdf_44_and_43(self):
        r = scrape.parse_amt_pdf(amt_pdf(44))
        self.assertEqual(r["headline"], 48.45)
        self.assertEqual({c["id"]: c["value"] for c in r["regions"]}, {"FS": 48.68, "NW": 48.88, "EC": 48.37, "MP": 46.2, "WC": 48.0, "KZN": 48.45})
        r = scrape.parse_amt_pdf(amt_pdf(43))
        self.assertEqual({c["id"]: c["value"] for c in r["classes"]}, {"light": 50.56, "heavy": 50.23, "top40": 53.16})
        self.assertEqual(r["regions"][-1], {"id": "KZN", "label": "KwaZulu-Natal (200-250 kg)", "value": 42.79})
    def test_pdf_without_table(self):
        with self.assertRaises(ValueError):
            scrape.parse_amt_pdf([[{"x0": 1, "y0": 1, "x1": 2, "y1": 2, "t": "Hello"}]])
    @unittest.skipUnless(shutil.which("pdftotext") and glob.glob("/workspace/donegal-prices-cache/*No-46*.pdf"), "real PDF not on this machine")
    def test_real_pdf_via_pdftotext(self):
        r = scrape.parse_amt_pdf(scrape.pdf_words(glob.glob("/workspace/donegal-prices-cache/*No-46*.pdf")[0]))
        self.assertEqual(r["headline"], 46.12); self.assertEqual(len(r["regions"]), 6)


class Feed(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "prices.json")
    def tearDown(self):
        shutil.rmtree(self.d)
    def test_full_build(self):
        f = scrape.build(self.p, fake_get, fake_pdf)
        s = {x["id"]: x for x in f["sources"]}
        self.assertEqual(f["schema"], 1); self.assertTrue(f["generated_at"].endswith("Z"))
        self.assertEqual(s["rpo"]["status"], "ok"); self.assertEqual(s["rpo"]["latest"]["value"], 48.94); self.assertEqual(s["rpo"]["latest"]["previous"], 49.33)
        self.assertEqual(s["amt"]["status"], "ok"); self.assertEqual(s["amt"]["latest"]["value"], 46.12)
        self.assertEqual(len(s["amt"]["history"]), 4); self.assertEqual(s["amt"]["report"]["no"], 46)
        self.assertEqual(len(s["amt"]["regions"]), 6); self.assertEqual(len(s["amt"]["classes"]), 3)
        self.assertTrue(s["rmis"]["link_only"]); self.assertNotIn("history", s["rmis"])
        for k in ("rpo", "amt"):
            self.assertIn("private family use", s[k]["attribution"])
        with open(self.p) as fh: self.assertEqual(json.load(fh)["sources"][0]["latest"]["value"], 48.94)
    def test_keeps_last_good_when_source_fails(self):
        scrape.build(self.p, fake_get, fake_pdf)
        def half(url):
            if url == scrape.RPO_URL: raise OSError("RPO down (test)")
            return fake_get(url)
        f = scrape.build(self.p, half, fake_pdf)
        s = {x["id"]: x for x in f["sources"]}
        self.assertEqual(s["rpo"]["status"], "error"); self.assertIn("RPO down", s["rpo"]["error"])
        self.assertEqual(s["rpo"]["latest"]["value"], 48.94); self.assertGreater(len(s["rpo"]["history"]), 200)
        self.assertEqual(s["amt"]["status"], "ok")
    def test_everything_down_keeps_everything(self):
        scrape.build(self.p, fake_get, fake_pdf)
        f = scrape.build(self.p, broken, broken)
        s = {x["id"]: x for x in f["sources"]}
        self.assertEqual(s["rpo"]["status"], "error"); self.assertEqual(s["amt"]["status"], "error")
        self.assertEqual(s["amt"]["latest"]["value"], 46.12); self.assertEqual(len(s["amt"]["regions"]), 6)
    def test_pdf_failure_keeps_headline_and_last_classes(self):
        scrape.build(self.p, fake_get, fake_pdf)
        f = scrape.build(self.p, fake_get, broken)
        s = {x["id"]: x for x in f["sources"]}["amt"]
        self.assertIn(s["status"], ("ok", "partial")); self.assertEqual(s["latest"]["value"], 46.12); self.assertEqual(len(s["regions"]), 6)
    def test_first_run_pdf_failure_is_partial(self):
        f = scrape.build(self.p, fake_get, broken)
        s = {x["id"]: x for x in f["sources"]}["amt"]
        self.assertEqual(s["status"], "partial"); self.assertEqual(s["latest"]["value"], 46.12); self.assertEqual(s["regions"], [])
        self.assertIn("report No 46", s["error"])


if __name__ == "__main__":
    unittest.main()
