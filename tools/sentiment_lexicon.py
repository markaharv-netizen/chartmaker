"""Rule-based bull/bear/neutral labeler for X posts (used for the weekly history backfill and the daily
rolling points, where hand-labeling hundreds of posts per run is not feasible).

Calibrated against the hand labels in tools/sentiment_input/2026-10-18.json; run this file directly to
print the agreement report.  Deterministic, no network.
"""
import re

BULL = r"""bullish|bull case|\bbulls? (are|win)|undervalued|upside|breakout|breaking out|to the moon|moon(ing)?\b|
buy(ing)? the dip|bought the dip|accumulat\w*|loading up|loaded up|adding (more|here)|added more|going long|i'?m long|
price target|upgrade[ds]?|outperform\w*|strong buy|send it|lfg|🚀|💎|🐂|bottom(ed)? is in|cheap|discount|bargain|
opportunit\w*|conviction|generational|parabolic|still early|stack(ing)? sats|hodl|don'?t sell|never selling|
undervalu\w*|re-?rate|mispriced|buy signal|bullish divergence|support hold\w*|held support|great (entry|buy)|
dividend (hike|raise|increase)|hik(e|es|ed) (the |its )?dividend|back (to|above) par|strong demand|oversubscribed|\bwin(ning|ner)?\b|
long[- ]term (hold|bull)|easy money|free money|don'?t fade|massive (upside|opportunity)|insane (upside|value)"""
BEAR = r"""bearish|bear case|\bbears? (are|win)|overvalued|downside|breakdown|breaking down|crash(es|ing)?\b|
dump(ing)?\b|ponzi|scam|fraud|rug ?pull|death spiral|insolven\w*|bankrupt\w*|dilut\w*|liquidat\w*|margin call|capitulat\w*|
top is in|topped out|lower lows|sell signal|exit liquidity|bagholder\w*|rekt|bubble|overpriced|too expensive|frothy|overbought|
below (par|its ipo|ipo price|debut)|under par|cut(s)? (the )?dividend|dividend cut|suspend\w*|going short|i'?m short|shorting|
\bshort (it|here|this)|puts on|bought puts|downgrade[ds]?|underperform\w*|red flag|warning|be careful|stay away|avoid|
sell (now|everything|it)|take profits?|get out|unsustainable|house of cards|collaps\w*|implod\w*|doom\w*|📉|🩸|🐻|💀|
forced sell\w*|index removal|delist\w*|lawsuit|investigation|fud\b(?! is)|worst|terrible|disaster|trouble|overhang"""
NEG = re.compile(r"\b(not|no|never|isn'?t|aren'?t|wasn'?t|don'?t|doesn'?t|won'?t|hardly|stop)\b\W+(\w+\W+){0,2}$", re.I)
_B = re.compile("(?:" + BULL.replace("\n", "") + ")", re.I)
_R = re.compile("(?:" + BEAR.replace("\n", "") + ")", re.I)
STRONG_B = re.compile(r"bullish|undervalued|🚀|buy(ing)? the dip|accumulat|price target|upgrade|outperform|to the moon|generational", re.I)
STRONG_R = re.compile(r"bearish|overvalued|ponzi|bubble|death spiral|downgrade|crash|collaps|scam|fraud|exit liquidity|bankrupt|insolven", re.I)
SPAM = re.compile(r"(signal alert|join (my|our)|free (group|signals)|dm me|link in bio|giveaway|airdrop|whatsapp|telegram|\bCA\b\s*[1-9A-HJ-NP-Za-km-z]{30,})", re.I)

AMBIG = re.compile(r"\b(bull(ish)?|bear(ish)?)\s*(or|vs\.?|/|and)\s*(bull(ish)?|bear(ish)?)\b\??|bull (and|&) bear cases?|(are you|is it|is this|who'?s|anyone|thoughts)[^.?!]{0,40}\b(bullish|bearish)\b[^.?!]{0,30}\?", re.I)

LEVELS = re.compile(r"key levels|support:|resistance:|watchlist|day range|pre-?market outlook|join us|options flow", re.I)

def _hits(rx, text):
    n = 0
    for m in rx.finditer(text):
        n += -1 if NEG.search(text[max(0, m.start() - 40):m.start()]) else 1
    return n

def classify(text):
    """-> 'bull' | 'bear' | 'neutral'"""
    t = re.sub(r"https?://\S+|#\w+", " ", text or "")
    t = AMBIG.sub(" ", t)  # "bullish or bearish?" polls / both-sides framing carry no stance
    if SPAM.search(t) or LEVELS.search(t) or len(set(re.findall(r"\$[A-Za-z]{1,6}\b", t))) >= 4: return "neutral"  # spam, TA level sheets, ticker lists
    b = _hits(_B, t) + 1.5 * len(STRONG_B.findall(t)); r = _hits(_R, t) + 1.5 * len(STRONG_R.findall(t))
    if b >= 1.5 and b >= r + 1: return "bull"
    if r >= 1.5 and r >= b + 1: return "bear"
    return "neutral"

if __name__ == "__main__":
    import json, os, collections
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentiment_input", "2026-10-18.json")
    B = json.load(open(p)); cm = collections.Counter()
    for sym, tk in B["tickers"].items():
        for it in tk["items"]:
            if it["kind"] == "post": cm[(it["label"], classify(it["text"]))] += 1
    tot = sum(cm.values()); acc = sum(v for (a, b), v in cm.items() if a == b) / tot
    pol = [(a, b) for (a, b) in cm if a != "neutral" and b != "neutral"]
    pa = sum(cm[k] for k in pol if k[0] == k[1]) / max(1, sum(cm[k] for k in pol))
    print(f"posts {tot}  3-way agreement {acc:.1%}  bull-vs-bear agreement when both polar {pa:.1%}")
    for a in ("bull", "bear", "neutral"): print(f"  hand {a:7}", {b: cm[(a, b)] for b in ("bull", "bear", "neutral")})
    import math, statistics
    def share(items, f):
        wb = wr = 0
        for it in items:
            l = f(it); w = 1 + math.log1p(max(0, it.get("eng", 0)))
            if l == "bull": wb += w
            elif l == "bear": wr += w
        return wb / (wb + wr) if wb + wr else None
    xs, ys = [], []
    for sym, tk in B["tickers"].items():
        ps = [i for i in tk["items"] if i["kind"] == "post"]
        h, a = share(ps, lambda i: i["label"]), share(ps, lambda i: classify(i["text"]))
        if h is not None and a is not None: xs.append(h); ys.append(a); print(f"  {sym:8} hand {h:.0%}  rules {a:.0%}")
    print(f"per-ticker bull share: corr {statistics.correlation(xs, ys):.2f}  mean abs diff {statistics.mean(abs(x-y) for x,y in zip(xs,ys)):.1%}")
