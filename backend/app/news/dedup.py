"""Pure de-duplication primitives: canonical URLs, normalised titles, RapidFuzz title similarity and a
home-grown 64-bit SimHash (no extra deps). No DB access here; the pipeline applies the gates.
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "yclid",
    "msclkid",
    "igshid",
    "ref",
    "ref_src",
    "ref_url",
    "mc_cid",
    "mc_eid",
    "_ga",
    "_gl",
    "ocid",
    "cmpid",
    "ncid",
    "sref",
    "srnd",
    "oc",
    "output",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
}
TITLE_THRESHOLD = 90
SIMHASH_MAX_HAMMING = 3
FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
_PUNCT_RE = re.compile(r"[^\w\s$%]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_SUFFIX_RE = re.compile(r"\s+[-–—|:]\s+([^-–—|]{2,40})$")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "״": '"', "׳": "'"})
_TOKEN_RE = re.compile(r"[\w$%]+", re.UNICODE)


# --- URLs -----------------------------------------------------------------------------------------------
def decode_google_news_url(url: str) -> str | None:
    """Best-effort decode of news.google.com/rss/articles/<id> links (older base64 format embeds the URL).
    Returns None when the id uses the newer opaque format (needs a JS/batchexecute round-trip)."""
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_\-]+)", url)
    if not m:
        return None
    token = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except Exception:
        return None
    # protobuf-ish: 0x08 0x13 0x22 <len> <url> ...
    idx = raw.find(b"http")
    if idx < 0:
        return None
    end = idx
    while end < len(raw) and 0x20 < raw[end] < 0x7F:
        end += 1
    cand = raw[idx:end].decode("ascii", "ignore")
    return cand if cand.startswith(("http://", "https://")) and "." in cand else None


def canonical_url(url: str) -> str:
    """Lowercase scheme/host, strip tracking params + fragment, sort remaining params, drop trailing slash."""
    url = (url or "").strip()
    if not url:
        return ""
    decoded = decode_google_news_url(url)
    if decoded:
        url = decoded
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _is_tracking(k)]
    q.sort()
    path = re.sub(r"/+$", "", parts.path) or "/"
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme.lower()
    return urlunsplit((scheme, host, path, urlencode(q), ""))


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in TRACKING_PARAMS


def url_hash(url: str) -> str:
    c = canonical_url(url)
    return hashlib.sha256(c.encode("utf-8")).hexdigest()[:40] if c else ""


# --- titles ---------------------------------------------------------------------------------------------
def normalize_title(title: str, publisher: str | None = None) -> str:
    t = (title or "").translate(_QUOTES).translate(FINALS).strip()
    if publisher:
        pub = re.escape(publisher.strip())
        t = re.sub(rf"\s+[-–—|:]\s+{pub}\s*$", "", t, flags=re.I)
    m = _SUFFIX_RE.search(t)
    if m and len(t[: m.start()].strip()) >= 20 and len(m.group(1).split()) <= 5:
        t = t[: m.start()]
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def title_similarity(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(a, b))


def titles_similar(a: str, b: str, threshold: float = TITLE_THRESHOLD) -> bool:
    if not a or not b:
        return False
    return title_similarity(a, b) >= threshold


# --- SimHash --------------------------------------------------------------------------------------------
def _features(text: str) -> dict[str, int]:
    toks = [t for t in _TOKEN_RE.findall((text or "").lower().translate(FINALS)) if len(t) > 1]
    feats: dict[str, int] = {}
    for t in toks:
        feats[t] = feats.get(t, 0) + 1
    for a, b in zip(toks, toks[1:], strict=False):
        feats[f"{a} {b}"] = feats.get(f"{a} {b}", 0) + 2  # bigrams weigh more: word order matters
    return feats


def _h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def simhash64(text: str) -> int:
    """Charikar SimHash over word unigrams + bigrams. Returns an unsigned 64-bit int."""
    feats = _features(text)
    if not feats:
        return 0
    v = [0] * 64
    for f, w in feats.items():
        h = _h64(f)
        for i in range(64):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def simhash_near(a: int, b: int, max_dist: int = SIMHASH_MAX_HAMMING) -> bool:
    if not a or not b:
        return False
    return hamming(a, b) <= max_dist


def to_signed64(v: int) -> int:
    v &= 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >= 1 << 63 else v


def from_signed64(v: int) -> int:
    return v & 0xFFFFFFFFFFFFFFFF


# --- misc -----------------------------------------------------------------------------------------------
_HE_RE = re.compile(r"[֐-׿]")


def detect_lang(text: str) -> str:
    if not text:
        return "en"
    he = len(_HE_RE.findall(text))
    letters = sum(1 for c in text if c.isalpha())
    return "he" if letters and he / letters > 0.3 else "en"


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    return _WS_RE.sub(" ", s).strip()
