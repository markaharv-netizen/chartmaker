"""Charts tab chart footer no longer shows a Source: line.

Price line, % change, ratio, candles, bars, and drawdown used to print
"Source: Yahoo Finance" (sometimes with as-of or methodology) at the
bottom of the 1600x900 PNG. Data still comes from Yahoo; stitch, short
history, and event-marker notes can still use the footer. Other tabs
keep their own source footnotes.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def html():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def fn(src, start, end):
    return src[src.index(start):src.index(end)]


class ChartsSourceFootnoteTest(unittest.TestCase):
    def test_charts_chart_types_omit_source_caption(self):
        src = html()
        build = fn(src, "function buildOpt(", "function download()")
        dd = fn(src, "function ddOpt(", "function colors(")
        candle = fn(src, "function candleOpt(", "function attachMarkers(")
        for body, name in ((build, "buildOpt"), (dd, "ddOpt"), (candle, "candleOpt")):
            self.assertNotIn("Source: Yahoo Finance", body, name)
            self.assertNotIn("Source:", body, name)
        self.assertIn("base(title,'\\u00a0')", build)
        # Notes can still land on the shared footer graphic.
        self.assertIn("graphic[0].style.text+=stitchNote", src)
        self.assertIn("graphic[0].style.text+=' · event markers'", src)

    def test_other_tabs_keep_source_footnotes(self):
        src = html()
        self.assertIn("src||'Source: Yahoo Finance'", fn(src, "function base(", "function hoverTip("))
        self.assertIn("Source: SEC filings", fn(src, "async function frender()", "function hrender("))
        heat = fn(src, "function hrender(", "async function trender(")
        self.assertIn("Source: ${j.source||'Yahoo Finance'}", heat)
        val = fn(src, "async function vhrender()", "function wdraw(")
        self.assertIn("Source: Yahoo Finance closes × SEC XBRL", val)
        self.assertIn("Source: ${srcs}", val)
        self.assertIn("const src='Source: '+WSRC", fn(src, "function wdraw(", "function senter("))
        sent = fn(src, "function senter(", "async function ptenter(")
        self.assertIn("Source: '+(SDATA.source", sent)
        self.assertIn("Source: X posts only", sent)


if __name__ == "__main__":
    unittest.main()
