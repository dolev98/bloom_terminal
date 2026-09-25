"""Entity linking: ticker/alias matching over normalised text (compiled alternation regex), seeding of
`entities`/`entity_aliases` from SEC company_tickers.json + the Hebrew alias seed.

Rules (a headline/snippet is linked to a ticker when ANY of these holds):
1. Ticker form: $AAPL, NASDAQ:AAPL, "(AAPL)", or a bare upper-case ticker of >= 4 letters in mixed-case text
   (not for tickers that are ordinary words, e.g. NICE).
2. A distinctive name: the full name ("Apple Inc", "Teva Pharmaceutical Industries"), a non-dictionary short
   name ("Nvidia", "Teva") or a Hebrew seed alias.
3. A short name that is also an ordinary word (Apple, Amazon, Target, Visa, Oracle, Nice, Tower, Monday, Wix,
   Check Point, טבע ...; see COMMON_WORD_NAMES) only when it is capitalised AND supported by a finance/business
   context word within WINDOW words (shares, stock, earnings, CEO, analyst, price target, Nasdaq, מניה ...)
   or directly followed by a corporate-action verb ("Apple says/unveils/sued ..."). A context phrase that
   contains the name itself ("price target") is not a mention of Target.
Provider-supplied tickers are unioned by the caller only for sources that tag entities themselves (Finnhub,
SEC, Massive, Alpha Vantage, wire categories); search-query hints (Google News, GDELT) are NOT passed in and
must be confirmed by the text. Option-chain quote pages ("AAPL Sep 2026 160.000 call (AAPL260925C...)") are
never linked by text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select

from app.data.store.sqlite import session_scope
from app.news.dedup import FINALS
from app.news.models import Entity, EntityAlias

log = logging.getLogger(__name__)
ALIASES_HE_PATH = Path(__file__).with_name("seeds") / "aliases_he.yaml"

LEGAL_SUFFIXES = (
    "incorporated",
    "corporation",
    "company",
    "limited",
    "holdings",
    "holding",
    "group",
    "inc",
    "corp",
    "ltd",
    "plc",
    "llc",
    "co",
    "nv",
    "sa",
    "ag",
    "se",
    "lp",
    "adr",
    "ads",
    "the",
)
# Common English words that must never become a bare first-word alias (NICE, Check Point, ...).
STOP_FIRST_WORDS = {
    "nice",
    "check",
    "first",
    "united",
    "american",
    "general",
    "national",
    "international",
    "global",
    "new",
    "north",
    "south",
    "west",
    "east",
    "bank",
    "capital",
    "energy",
    "digital",
    "micro",
    "advanced",
    "applied",
    "alpha",
    "beta",
    "target",
    "gap",
    "ford",
    "visa",
    "block",
    "match",
    "snap",
    "square",
    "box",
    "zoom",
    "shell",
    "ball",
    "dollar",
    "tree",
    "monday",
    "wix",
    "icl",
    "tower",
}
# Company short names that are also ordinary words: a match needs capitalisation + context (rule 3 above).
COMMON_WORD_NAMES_EN = frozenset(
    STOP_FIRST_WORDS
    | {
        "apple",
        "amazon",
        "alphabet",
        "oracle",
        "meta",
        "intel",
        "adobe",
        "unity",
        "arm",
        "crown",
        "pool",
        "delta",
        "chase",
        "discover",
        "coach",
        "progressive",
        "southern",
        "dominion",
        "paramount",
        "fox",
        "ally",
        "citizens",
        "regions",
        "carrier",
        "republic",
        "nova",
        "partner",
        "phoenix",
        "best buy",
        "check point",
        "waste management",
    }
)
# Hebrew: טבע = "nature", לאומי = "national", דלק = "fuel", בזק = "flash", נובה (Nova festival) ...
COMMON_WORD_NAMES_HE = frozenset(
    {"טבע", "לאומי", "הפועלים", "דלק", "בזק", "מזרחי", "דיסקונט", "פרטנר", "הפניקס", "נובה", "כיל"}
)
WINDOW = 8  # context words must be within this many words of the name
_CONTEXT_EN = """
shares share shareholder shareholders stock stocks equity earnings eps revenue revenues sales profit profits
margin margins guidance outlook forecast results quarter quarterly q1 q2 q3 q4 fiscal ceo cfo coo executive
executives chairman analyst analysts investor investors invest invests invested investing investment stake
valuation overvalued undervalued overpriced trillion billion rating ratings upgrade upgrades upgraded downgrade
downgrades downgraded outperform underperform overweight underweight nasdaq nyse tase dow market markets
dividend dividends buyback buybacks repurchase ipo acquisition acquire acquires acquired merger takeover
lawsuit lawsuits sues sued antitrust regulator regulators regulatory settlement court doj ftc sec fda eu
penalty fined tariff tariffs supplier suppliers layoffs chipmaker
"""
_CONTEXT_PHRASES_EN = (
    "price target",
    "target price",
    "market cap",
    "market value",
    "chief executive",
    "wall street",
    "tech giant",
    "all time high",
)
_CONTEXT_HE = """
מניה מניות בורסה בורסת דוח דוחות רבעון הכנסות רווח רווחים הפסד הפסדים מכירות תחזית אנליסט אנליסטים משקיע
משקיעים דיבידנד רכישה רכישת מיזוג הנפקה שווי דולר מיליארד תביעה ייצוגית המלצה המלצת רגולטור מנכ"ל נאסד"ק ת"א
"""
_CONTEXT_PHRASES_HE = ("מחיר יעד", "שווי שוק", "וול סטריט", "תל אביב 35")
# Corporate-action verbs that make "<Name> <verb>" a company mention ("Apple says", "Target cuts").
CORPORATE_VERBS = frozenset(
    """
says said announces announced unveils unveiled launches launched reports reported releases released sues sued
files filed plans prepares acquires acquired buys bought sells sold hires appoints names cuts recalls faces
wins loses beats misses raises lowers agrees settles partners invests confirms denies delays expands warns posts
tops slips falls rises gains jumps drops slides surges soars tumbles plunges rallies climbs sinks hits touches
takes pulls moves bets debuts introduces reveals ships halts pauses explores
מדווחת דיווחה מודיעה הודיעה משיקה השיקה רוכשת רכשה מוכרת מכרה תובעת נתבעה מפטרת פיטרה מגייסת גייסה
מזנקת זינקה צונחת צנחה חותמת חתמה מציגה הציגה מעדכנת עדכנה
""".split()
)
_ADVERBS = frozenset({"just", "now", "reportedly", "also", "quietly", "finally", "officially", "still"})
# Option-chain quote pages (OCC symbol in the title) are listings, not news.
_OPTION_RE = re.compile(r"\b[A-Z]{1,6}\d{6}[CP]\d{8}\b")
_PAREN_RE = re.compile(r"\(([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\)")
_HE_PREFIX = "(?:[בלהומשכ]{1,2})?"
_HE_RE = re.compile(r"[֐-׿]")
_CASHTAG_RE = re.compile(r"(?<![\w$])\$([A-Za-z]{1,6}(?:\.[A-Za-z]{1,3})?)\b")
_EXCH_RE = re.compile(
    r"\b(?:NASDAQ|NYSE|TASE|AMEX|NYSE\s*American|NYSE\s*Arca|OTC(?:QB|QX)?|LSE|TSX)\s*:\s*([A-Za-z]{1,6}(?:\.[A-Za-z]{1,3})?)\b",
    re.I,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z.]{0,7}")


def normalize_name(name: str) -> str:
    """Lower-case, strip punctuation and trailing legal suffixes: 'Apple Inc.' -> 'apple'."""
    s = (name or "").lower().translate(FINALS)
    s = re.sub(r"[^\w\s]+", " ", s)
    toks = s.split()
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    while toks and toks[0] == "the":
        toks.pop(0)
    return " ".join(toks)


def normalize_text(text: str) -> str:
    s = (text or "").lower().translate(FINALS)
    s = s.replace("’", "'").replace("׳", "'")
    s = re.sub(r"[^\w\s$'׳]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def derived_aliases(ticker: str, name: str) -> list[tuple[str, float]]:
    """Aliases generated from a company name: full normalised name (1.0), the name with its legal form when
    the name is a single word ('apple inc' - distinctive even though 'apple' is a common word), plus a
    distinctive first word (0.7)."""
    out: list[tuple[str, float]] = []
    n = normalize_name(name)
    if not n:
        return out
    out.append((n, 1.0))
    toks = n.split()
    if len(toks) == 1:
        raw = re.sub(r"[^\w\s]+", " ", (name or "").lower()).split()
        if len(raw) > 1 and raw[0] == toks[0] and raw[1] in LEGAL_SUFFIXES and raw[1] != "the":
            out.append((f"{toks[0]} {raw[1]}", 1.0))
    first = toks[0]
    if len(toks) > 1 and len(first) >= 4 and first not in STOP_FIRST_WORDS and not first.isdigit():
        out.append((first, 0.7))
    return out


def is_common_word_name(alias: str) -> bool:
    """True when a (normalised) alias is also an ordinary word and so needs context to count as a mention."""
    key = normalize_text(alias)
    return key in COMMON_WORD_NAMES_EN or key in _COMMON_HE_NORM


def is_quote_page(text: str) -> bool:
    return bool(_OPTION_RE.search(text or ""))


def _vocab(words: str, phrases: tuple[str, ...]) -> tuple[frozenset[str], frozenset[str]]:
    single, multi = set(), set()
    for w in [*words.split(), *phrases]:
        n = normalize_text(w)
        (multi if " " in n else single).add(n)
    return frozenset(single), frozenset(multi)


_CTX_WORDS_EN, _CTX_PHRASES_EN = _vocab(_CONTEXT_EN, _CONTEXT_PHRASES_EN)
_CTX_WORDS_HE, _CTX_PHRASES_HE = _vocab(_CONTEXT_HE, _CONTEXT_PHRASES_HE)
_CTX_WORDS = _CTX_WORDS_EN | _CTX_WORDS_HE
_CTX_PHRASES = _CTX_PHRASES_EN | _CTX_PHRASES_HE
_COMMON_HE_NORM = frozenset(normalize_text(w) for w in COMMON_WORD_NAMES_HE)
# Only these Hebrew prefixes for common-word names: מטבע is "coin", הטבע is "the nature" (a name takes no ה).
_HE_PREFIX_STRICT = "(?:ו?[בלש]?)"


def _tok(t: str) -> str:
    t = t.strip("'")
    return t[:-2] if t.endswith("'s") else t


def _is_ctx(tok: str) -> bool:
    if tok in _CTX_WORDS:
        return True
    if _HE_RE.search(tok):  # Hebrew prefix letters: במניות, והמניה, למשקיעים
        return any(tok[k:] in _CTX_WORDS for k in (1, 2) if len(tok) - k >= 2)
    return False


def _context_supported(before: list[str], name: list[str], after: list[str]) -> bool:
    """Is a common-word name occurrence (`name` tokens) a company mention given the surrounding tokens?"""
    b, a = [_tok(t) for t in before[-WINDOW:]], [_tok(t) for t in after[:WINDOW]]
    # the name is part of a finance phrase ("price target", "target price") -> not a company mention
    if (b and f"{b[-1]} {name[0]}" in _CTX_PHRASES) or (a and f"{name[-1]} {a[0]}" in _CTX_PHRASES):
        return False
    possessive = bool(after) and after[0] in ("'s", "s")
    if possessive:
        a = a[1:]
    if not possessive and a:
        nxt = a[1] if a[0] in _ADVERBS and len(a) > 1 else a[0]
        if nxt in CORPORATE_VERBS:
            return True
    for side in (b, a):
        if any(_is_ctx(t) for t in side):
            return True
        if any(f"{x} {y}" in _CTX_PHRASES for x, y in zip(side, side[1:], strict=False)):
            return True
    return False


@dataclass
class Alias:
    ticker: str
    alias: str
    weight: float = 1.0
    lang: str = "en"


@dataclass
class EntityMatcher:
    """Compiled alias/ticker matcher. Build once per poll via `EntityMatcher.load()`; pure `link()` after that."""

    aliases: list[Alias] = field(default_factory=list)
    tickers: set[str] = field(default_factory=set)
    _alias_re: re.Pattern | None = None
    _by_alias: dict[str, list[Alias]] = field(default_factory=dict)
    _common_re: dict[str, re.Pattern] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._compile()

    def _compile(self) -> None:
        self._by_alias = {}
        self._common_re = {}
        parts: list[str] = []
        for a in self.aliases:
            key = normalize_text(a.alias)
            if not key:
                continue
            self._by_alias.setdefault(key, []).append(a)
        for key in sorted(self._by_alias, key=len, reverse=True):
            esc = re.escape(key)
            he = bool(_HE_RE.search(key))
            if he:
                parts.append(rf"(?<!\w){_HE_PREFIX}{esc}(?!\w)")
            else:
                parts.append(rf"(?<![\w$]){esc}(?!\w)")
            if is_common_word_name(key):  # raw-text pattern (case kept) for the context check
                words = r"\s+".join(re.escape(w) for w in key.split())
                pat = rf"(?<!\w){_HE_PREFIX_STRICT}{words}(?!\w)" if he else rf"(?<![\w$]){words}(?!\w)"
                self._common_re[key] = re.compile(pat, re.I)
        self._alias_re = re.compile("|".join(parts)) if parts else None

    @classmethod
    async def load(cls) -> EntityMatcher:
        async with session_scope() as s:
            ents = (await s.execute(select(Entity))).scalars().all()
            rows = (await s.execute(select(EntityAlias))).scalars().all()
        aliases = [Alias(r.ticker.upper(), r.alias, r.weight, r.lang) for r in rows]
        have = {(a.ticker, normalize_text(a.alias)) for a in aliases}
        for e in ents:  # derived aliases added since the rows were seeded (e.g. 'apple inc')
            if e.name and e.name.upper() != e.ticker.upper():
                for alias, w in derived_aliases(e.ticker, e.name):
                    if (e.ticker.upper(), normalize_text(alias)) not in have:
                        aliases.append(Alias(e.ticker.upper(), alias, w, "en"))
                        have.add((e.ticker.upper(), normalize_text(alias)))
        return cls(aliases=aliases, tickers={e.ticker.upper() for e in ents})

    def _common_word_mentioned(self, key: str, raw: str) -> bool:
        pat = self._common_re.get(key)
        if pat is None:
            return False
        src = raw.translate(FINALS)
        he = bool(_HE_RE.search(key))
        for m in pat.finditer(src):
            word = m.group(0)
            if not he and not word[0].isupper():  # 'apple pie', 'michigan apple report'
                continue
            before = normalize_text(src[: m.start()]).split()
            after = normalize_text(src[m.end() :]).split()
            if _context_supported(before, key.split(), after):
                return True
        return False

    def link(
        self, text: str, provider_tickers: dict[str, float] | None = None
    ) -> dict[str, tuple[float, str]]:
        """Return {ticker: (relevance 0..1, method)} for tickers/aliases found in `text` (rules in the module
        docstring), unioned with `provider_tickers` (method 'provider'). Methods: provider | cashtag | alias |
        context (a common-word name confirmed by nearby finance context)."""
        out: dict[str, tuple[float, str]] = {}

        def put(t: str, rel: float, method: str) -> None:
            t = t.upper()
            cur = out.get(t)
            if cur is None or rel > cur[0]:
                out[t] = (round(min(1.0, rel), 3), method)

        for t, rel in (provider_tickers or {}).items():
            if t:
                put(t, rel if rel is not None else 0.8, "provider")
        raw = (text or "").replace("’", "'").replace("׳", "'")
        if is_quote_page(raw):
            return out
        # cashtags / exchange-prefixed / parenthesised tickers count for any known entity (incl. short ones)
        for m in _CASHTAG_RE.finditer(raw):
            t = m.group(1).upper()
            if t in self.tickers:
                put(t, 0.9, "cashtag")
        for m in _EXCH_RE.finditer(raw):
            t = m.group(1).upper()
            if t in self.tickers:
                put(t, 0.95, "cashtag")
        for m in _PAREN_RE.finditer(raw):
            t = m.group(1).upper()
            if t in self.tickers:
                put(t, 0.9, "cashtag")
        norm = normalize_text(raw)
        common_keys: set[str] = set()
        if self._alias_re:
            for m in self._alias_re.finditer(norm):
                key = m.group(0)
                hit = self._by_alias.get(key)
                if hit is None and _HE_RE.search(key):  # strip Hebrew prefix letters
                    for k in range(1, 3):
                        hit = self._by_alias.get(key[k:])
                        if hit:
                            key = key[k:]
                            break
                if hit and key in self._common_re:
                    common_keys.add(key)
                    continue
                for a in hit or []:
                    put(a.ticker, a.weight, "alias")
        for key in common_keys:
            if self._common_word_mentioned(key, raw):
                for a in self._by_alias[key]:
                    put(a.ticker, min(a.weight, 0.8), "context")
        # bare upper-case tickers: only >= 4 chars (short ones need a cashtag or a name co-mention), only in
        # mixed-case text (an all-caps headline carries no signal) and not for tickers that are words (NICE)
        if any(c.islower() for c in raw):
            for m in _WORD_RE.finditer(raw):
                w = m.group(0)
                t = w.upper()
                if (
                    t in self.tickers
                    and w.isupper()
                    and len(t.replace(".", "")) >= 4
                    and t.lower() not in COMMON_WORD_NAMES_EN
                ):
                    put(t, 0.6, "alias")
        return out


# --- seeding --------------------------------------------------------------------------------------------
def load_hebrew_aliases(path: Path = ALIASES_HE_PATH) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k).upper(): [str(a) for a in v] for k, v in (data.get("aliases") or {}).items()}


def _base(ticker: str) -> str:
    return ticker.upper().split(".", 1)[0]


async def seed_entities(tickers: list[str], ticker_map: dict[str, dict] | None = None) -> dict:
    """Upsert `entities` for `tickers` (SEC name/CIK when known, else name=ticker) and their aliases:
    derived from the name (source 'derived') + the Hebrew seed (source 'seed_he')."""
    tickers = sorted({t.strip().upper() for t in tickers if t.strip()})
    ticker_map = ticker_map or {}
    he = load_hebrew_aliases()
    n_ent = n_alias = 0
    async with session_scope() as s:
        existing = {e.ticker: e for e in (await s.execute(select(Entity))).scalars().all()}
        for e in existing.values():
            e.is_watchlist = e.ticker in tickers
        for t in tickers:
            info = ticker_map.get(t) or ticker_map.get(_base(t)) or {}
            ent = existing.get(t)
            if ent is None:
                ent = Entity(ticker=t, is_watchlist=True)
                s.add(ent)
                existing[t] = ent
                n_ent += 1
            ent.name = ent.name if ent.name and not info else (info.get("name") or ent.name or t)
            ent.cik = info.get("cik") or ent.cik
            ent.country = ent.country or ("IL" if t.endswith(".TA") else ("US" if info else None))
            ent.exchange = ent.exchange or ("TASE" if t.endswith(".TA") else None)
        have = {(a.ticker, a.alias) for a in (await s.execute(select(EntityAlias))).scalars().all()}

        def add(ticker: str, alias: str, weight: float, lang: str, source: str) -> None:
            nonlocal n_alias
            if (ticker, alias) in have or not alias:
                return
            s.add(EntityAlias(ticker=ticker, alias=alias, lang=lang, weight=weight, source=source))
            have.add((ticker, alias))
            n_alias += 1

        for t in tickers:
            name = existing[t].name
            if name and name.upper() != t:
                for alias, w in derived_aliases(t, name):
                    add(t, alias, w, "en", "derived")
            for alias in he.get(_base(t), []):
                add(t, alias, 1.0, "he", "seed_he")
    return {"entities": n_ent, "aliases": n_alias, "tickers": tickers}
