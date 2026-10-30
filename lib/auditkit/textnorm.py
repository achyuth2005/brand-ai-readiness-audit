"""Text, number, currency and date normalisation.

Most false positives in this problem space are normalisation bugs wearing a detector's
clothes. Two examples that this module exists to prevent:

* Comparing raw static text against rendered text without NFKC folding and whitespace
  collapse invents a render delta out of ``&nbsp;``, smart quotes and soft hyphens. The
  page is fine; the comparison is broken.
* Comparing ``"49.00"`` in JSON-LD against ``"£49"`` on the page reports a
  markup-versus-text contradiction that does not exist. Currency symbols, thousands
  separators and comma decimals all have to be resolved before any claim is made.

Everything here is a pure function, locale-independent, and deterministic.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

# Characters that carry no meaning for comparison but break naive string equality.
_ZERO_WIDTH = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)

_QUOTE_MAP = {
    ord("‘"): "'", ord("’"): "'", ord("‚"): "'", ord("‛"): "'",
    ord("“"): '"', ord("”"): '"', ord("„"): '"', ord("‟"): '"',
    ord("′"): "'", ord("″"): '"', ord("«"): '"', ord("»"): '"',
}

_DASH_MAP = {
    ord("‐"): "-", ord("‑"): "-", ord("‒"): "-", ord("–"): "-",
    ord("—"): "-", ord("―"): "-", ord("−"): "-", ord("⁃"): "-",
}

_SPACE_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+")

# Abbreviations that end in a period without ending a sentence. Without this the
# segmenter splits "Acme Inc. builds tools" into two fragments and then reports the
# second one as a sentence with no subject -- a false positive in the answerability
# checks that would be invisible in the evidence string.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "rev", "hon", "gen",
    "col", "lt", "sgt", "capt", "inc", "ltd", "llc", "llp", "plc", "co", "corp",
    "gmbh", "ag", "sa", "bv", "nv", "pty", "pvt", "kk",
    "e.g", "i.e", "etc", "vs", "viz", "cf", "al", "ca", "approx", "est",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "no", "vol", "pp", "ed", "fig", "dept", "univ", "govt", "min", "max", "avg",
    "u.s", "u.k", "e.u", "a.m", "p.m", "ph.d", "b.a", "m.a", "m.d", "b.sc", "m.sc",
})

# ISO 4217 for the symbols that actually appear on commercial pages. "$" is deliberately
# left ambiguous: guessing USD for a Canadian or Australian store is exactly the kind of
# confident error that produces a bogus contradiction finding.
CURRENCY_SYMBOLS: Dict[str, Optional[str]] = {
    "£": "GBP", "€": "EUR", "¥": None, "₹": "INR", "₽": "RUB", "₩": "KRW",
    "₪": "ILS", "₺": "TRY", "₴": "UAH", "₦": "NGN", "R$": "BRL", "CHF": "CHF",
    "kr": None, "zł": "PLN", "Kč": "CZK", "$": None, "¢": None,
}

AMBIGUOUS_CURRENCY_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "$": ("USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN", "ARS", "CLP", "COP", "TWD"),
    "¥": ("JPY", "CNY"),
    "kr": ("SEK", "NOK", "DKK", "ISK"),
    "¢": ("USD", "CAD"),
}

_PRICE_RE = re.compile(
    r"""(?P<pre>US\$|CA\$|AU\$|R\$|[£€¥₹₽₩₪₺₴₦$]|
                USD|EUR|GBP|JPY|CNY|INR|AUD|CAD|NZD|CHF|SEK|NOK|DKK|PLN|CZK|BRL|
                MXN|ZAR|SGD|HKD|KRW|TRY|RUB|ILS|AED|SAR|kr|zł|Kč)?\s*
        (?P<num>\d{1,3}(?:[ ,. ']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*
        (?P<post>[£€¥₹₽₩₪₺₴₦$]|USD|EUR|GBP|JPY|CNY|INR|AUD|CAD|NZD|CHF|SEK|NOK|DKK|
                 PLN|CZK|BRL|MXN|ZAR|SGD|HKD|KRW|TRY|RUB|ILS|AED|SAR|kr|zł|Kč)?""",
    re.VERBOSE | re.IGNORECASE,
)

# The grouped alternative requires at least one thousands group. With an optional group
# it would win the alternation on "1234.56" by matching only "123", silently misreading
# every ungrouped price of 1000 or more -- and manufacturing contradiction findings.
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_TEXT_DATE_RES = (
    # 15 March 2024 / 15 Mar 2024
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"),
    # March 15, 2024 / Mar 15 2024
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"),
    # March 2024
    re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b"),
)

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|llp|plc|gmbh|ag|s\.a|sa|sarl|bv|b\.v|"
    r"nv|n\.v|oy|ab|as|aps|kk|k\.k|pty|pvt|private limited|co|company|corp|corporation|"
    r"holdings|group|s\.r\.l|srl|spa|s\.p\.a)\b\.?",
    re.IGNORECASE,
)


def nfkc(text: str) -> str:
    """Unicode NFKC fold. Collapses full-width forms, ligatures and compatibility chars."""
    return unicodedata.normalize("NFKC", text)


def strip_invisibles(text: str) -> str:
    """Remove soft hyphens, zero-width joiners and BOMs."""
    return text.translate(_ZERO_WIDTH)


def unify_punctuation(text: str) -> str:
    """Fold smart quotes and every dash variant to ASCII equivalents."""
    return text.translate(_QUOTE_MAP).translate(_DASH_MAP)


def collapse_space(text: str) -> str:
    """Collapse all whitespace runs -- including ``&nbsp;`` -- to single spaces."""
    return _SPACE_RE.sub(" ", text.replace(" ", " ")).strip()


def normalise_text(text: str) -> str:
    """The standard comparison form: NFKC, invisibles stripped, punctuation unified,
    whitespace collapsed. Case is preserved -- callers lowercase when they need to."""
    if not text:
        return ""
    return collapse_space(unify_punctuation(strip_invisibles(nfkc(text))))


def comparison_key(text: str) -> str:
    """Normalised **and** case-folded. The form used for all text-delta comparisons."""
    return normalise_text(text).casefold()


def words(text: str) -> List[str]:
    """Word tokens. Keeps intra-word apostrophes and hyphens, drops punctuation."""
    return re.findall(r"[0-9A-Za-zÀ-ɏ]+(?:['\-][0-9A-Za-zÀ-ɏ]+)*",
                      normalise_text(text))


def word_count(text: str) -> int:
    return len(words(text))


def sentences(text: str) -> List[str]:
    """Abbreviation-aware sentence segmentation.

    Deliberately conservative: a period followed by whitespace only ends a sentence when
    the preceding token is not a known abbreviation and not a single initial. Getting
    this wrong manufactures subject-less fragments, which would inflate
    ``G3.ANSW.LOW_SELF_CONTAINMENT`` on perfectly well-written pages.
    """
    clean = normalise_text(text)
    if not clean:
        return []

    out: List[str] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(clean):
        candidate = clean[start:match.start()].strip()
        if not candidate:
            start = match.end()
            continue
        if _ends_with_abbreviation(candidate):
            continue
        out.append(candidate)
        start = match.end()
    tail = clean[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def _ends_with_abbreviation(fragment: str) -> bool:
    stripped = fragment.rstrip("\"')]")
    if not stripped.endswith("."):
        return False
    last = stripped[:-1].split()[-1] if stripped[:-1].split() else ""
    last = last.strip("(\"'[").lower()
    if not last:
        return False
    if last in _ABBREVIATIONS:
        return True
    # A single initial: "J. Smith", "A. B. Curie".
    if len(last) == 1 and last.isalpha():
        return True
    # Decimal numbers already contain their period, so "3.5" never reaches here, but a
    # trailing numeral like "Version 2." is a real sentence end. Leave it alone.
    return False


def paragraph_word_counts(paragraphs: Sequence[str]) -> List[int]:
    return [word_count(p) for p in paragraphs]


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile. Deterministic and dependency-free."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
    return float(ordered[rank])


# --------------------------------------------------------------------------------------
# Prices and currency
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Money:
    """A parsed monetary value. ``currency`` is ``None`` when the symbol is ambiguous."""

    amount: float
    currency: Optional[str]
    symbol: str
    raw: str

    def matches(self, other: "Money", tolerance: float = 0.005) -> bool:
        """True when two values agree.

        Currency comparison is deliberately permissive in one direction only: an unknown
        currency is compatible with any currency, because refusing to decide beats
        deciding wrongly. Two *known* and different currencies never match.
        """
        if abs(self.amount - other.amount) > tolerance:
            return False
        if self.currency and other.currency:
            return self.currency == other.currency
        return True


def _parse_number(raw: str) -> Optional[float]:
    """Parse a numeric string with unknown thousands/decimal convention.

    ``1.234,56`` (European) and ``1,234.56`` (Anglo) must both work, and ``1.234``
    is genuinely ambiguous -- it is resolved as a thousands separator only when the
    group is exactly three digits and no other separator is present.
    """
    text = raw.strip().replace(" ", "").replace(" ", "").replace("'", "")
    if not text:
        return None
    has_comma, has_dot = "," in text, "." in text

    if has_comma and has_dot:
        # The rightmost separator is the decimal point.
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        tail = text.rsplit(",", 1)[1]
        text = text.replace(",", "") if len(tail) == 3 else text.replace(",", ".")
    elif has_dot:
        tail = text.rsplit(".", 1)[1]
        if len(tail) == 3 and text.count(".") == 1 and len(text.split(".")[0]) <= 3:
            # "1.234" -- ambiguous. Treat as thousands, which is the more common
            # rendering of a four-digit price, and record nothing else about it.
            text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_money(raw: str, default_currency: Optional[str] = None) -> Optional[Money]:
    """Parse the first monetary value in ``raw``. Returns ``None`` when there is none."""
    if raw is None:
        return None
    text = normalise_text(str(raw))
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    amount = _parse_number(match.group("num"))
    if amount is None:
        return None

    symbol = (match.group("pre") or match.group("post") or "").strip()
    currency = default_currency
    if symbol:
        upper = symbol.upper()
        if len(upper) == 3 and upper.isalpha():
            currency = upper
        else:
            key = symbol if symbol in CURRENCY_SYMBOLS else symbol.lower()
            mapped = CURRENCY_SYMBOLS.get(key, CURRENCY_SYMBOLS.get(symbol))
            currency = mapped if mapped else None
            if symbol.upper() in ("US$",):
                currency = "USD"
            elif symbol.upper() in ("CA$",):
                currency = "CAD"
            elif symbol.upper() in ("AU$",):
                currency = "AUD"
    return Money(amount=amount, currency=currency, symbol=symbol, raw=match.group(0).strip())


def find_all_money(text: str, default_currency: Optional[str] = None) -> List[Money]:
    """Every monetary value in ``text``, in document order."""
    out: List[Money] = []
    clean = normalise_text(text)
    for match in _PRICE_RE.finditer(clean):
        if not (match.group("pre") or match.group("post")):
            continue  # a bare number is not a price; requiring a symbol avoids noise
        amount = _parse_number(match.group("num"))
        if amount is None:
            continue
        parsed = parse_money(match.group(0), default_currency)
        if parsed is not None:
            out.append(parsed)
    return out


def currency_for_symbol(symbol: str) -> Optional[str]:
    return CURRENCY_SYMBOLS.get(symbol) or CURRENCY_SYMBOLS.get(symbol.lower())


def is_ambiguous_currency(symbol: str) -> bool:
    return symbol in AMBIGUOUS_CURRENCY_CANDIDATES


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------

def parse_date(raw: str) -> Optional[date]:
    """Parse a date from markup or prose, returning a UTC calendar date.

    Comparison happens at day granularity everywhere in this package, because timezone
    handling differences between a CMS and a rendered byline routinely produce one-day
    offsets that are not freshness defects.
    """
    if not raw:
        return None
    text = normalise_text(str(raw))
    if not text:
        return None

    iso = _ISO_DATE_RE.search(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            pass

    # Full ISO-8601 timestamps, including trailing Z and offsets.
    candidate = text.replace("Z", "+00:00")
    for attempt in (candidate, candidate[:19], candidate[:10]):
        try:
            parsed = datetime.fromisoformat(attempt)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.date()
        except ValueError:
            continue

    for pattern in _TEXT_DATE_RES:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groups()
        try:
            if len(groups) == 3 and groups[0].isdigit():
                day, month_name, year = groups
                month = _MONTHS.get(month_name.lower().rstrip("."))
                if month:
                    return date(int(year), month, int(day))
            elif len(groups) == 3:
                month_name, day, year = groups
                month = _MONTHS.get(month_name.lower().rstrip("."))
                if month:
                    return date(int(year), month, int(day))
            elif len(groups) == 2:
                month_name, year = groups
                month = _MONTHS.get(month_name.lower().rstrip("."))
                if month:
                    return date(int(year), month, 1)
        except (ValueError, TypeError):
            continue

    # Numeric forms. d/m/Y and m/d/Y are genuinely ambiguous, so only accept one when
    # the day component exceeds 12 and therefore settles the order.
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", text)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            if a > 12 >= b:
                return date(year, b, a)
            if b > 12 >= a:
                return date(year, a, b)
        except ValueError:
            return None
    try:
        from dateutil import parser as _dateutil_parser  # optional dependency
        parsed = _dateutil_parser.parse(text, fuzzy=False, default=datetime(2000, 1, 1))
        return parsed.date()
    except Exception:
        return None


def is_iso8601(raw: str) -> bool:
    """True when ``raw`` is an ISO-8601 date or datetime, as schema.org requires."""
    if not raw or not isinstance(raw, str):
        return False
    text = raw.strip()
    # Reduced-precision forms (YYYY, YYYY-MM) are valid ISO 8601 and valid schema.org
    # Dates -- a founding year of "2011" is correct markup, not a malformed value.
    if re.match(r"^\d{4}$", text):
        return True
    if re.match(r"^\d{4}-(0[1-9]|1[0-2])$", text):
        return True
    if not re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
                    r"(Z|[+-]\d{2}:?\d{2})?)?$", text):
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def years_in(text: str, floor: int = 1990, ceiling: int = 2100) -> List[int]:
    """Four-digit years appearing in ``text``, filtered to a plausible range."""
    out = []
    for m in re.finditer(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text):
        year = int(m.group(1))
        if floor <= year <= ceiling:
            out.append(year)
    return out


def days_between(a: date, b: date) -> int:
    return abs((a - b).days)


# --------------------------------------------------------------------------------------
# Names, phones, addresses -- comparison helpers for markup-versus-text checks
# --------------------------------------------------------------------------------------

def normalise_brand_name(name: str) -> str:
    """Fold a brand string for comparison: case, punctuation, legal suffix, ``&``.

    ``Acme, Inc.``, ``ACME Inc``, ``Acme and Co`` and ``Acme & Co.`` all reduce to the
    same key, which is what stops ``G3.ENTITY.NAME_INCONSISTENCY`` from firing on the
    ordinary long-form/short-form pairs every company has.
    """
    text = normalise_text(name).lower()
    text = text.replace("&", " and ")
    text = _LEGAL_SUFFIX_RE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return collapse_space(text)


def name_variants_reduce(a: str, b: str) -> bool:
    """True when one normalised name is a prefix/subset of the other.

    ``Acme`` and ``Acme Corporation`` reduce to one another and are not an
    inconsistency. ``Acme`` and ``Globex`` do not.
    """
    na, nb = normalise_brand_name(a), normalise_brand_name(b)
    if not na or not nb:
        return True
    if na == nb or na.startswith(nb) or nb.startswith(na):
        return True
    ta, tb = set(na.split()), set(nb.split())
    return ta.issubset(tb) or tb.issubset(ta)


def normalise_phone(raw: str) -> str:
    """Reduce a phone number to significant digits for comparison."""
    digits = re.sub(r"\D", "", normalise_text(raw))
    # Compare on the last 10 significant digits so +44 20 1234 5678 and 020 1234 5678
    # agree without a country-code table.
    return digits[-10:] if len(digits) >= 10 else digits


def phones_match(a: str, b: str) -> bool:
    na, nb = normalise_phone(a), normalise_phone(b)
    if len(na) < 7 or len(nb) < 7:
        return False
    return na[-7:] == nb[-7:]


_ADDRESS_ABBREV = {
    "street": "st", "road": "rd", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
    "lane": "ln", "court": "ct", "place": "pl", "square": "sq", "terrace": "ter",
    "suite": "ste", "apartment": "apt", "floor": "fl", "building": "bldg",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "saint": "st", "mount": "mt", "fort": "ft", "highway": "hwy", "parkway": "pkwy",
}


def normalise_address_part(raw: str) -> str:
    """Fold an address component using an abbreviation table."""
    text = normalise_brand_name(raw)
    tokens = [_ADDRESS_ABBREV.get(t, t) for t in text.split()]
    return " ".join(tokens)


def addresses_match(a: str, b: str) -> bool:
    na, nb = normalise_address_part(a), normalise_address_part(b)
    if not na or not nb:
        return False
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= 0.7


def contains_normalised(haystack: str, needle: str) -> bool:
    """Substring test in the standard comparison form."""
    if not needle:
        return False
    return comparison_key(needle) in comparison_key(haystack)


def truncate(text: str, limit: int = 300) -> str:
    """Cap an evidence snippet, marking the cut so it is never mistaken for the whole."""
    clean = normalise_text(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"
