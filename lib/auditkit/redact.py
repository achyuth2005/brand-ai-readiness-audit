"""Redaction of personal data and secrets from evidence snippets (threats T11, T12).

Evidence strings quote the audited page verbatim, which is what makes them checkable. It
also means an evidence snippet can copy a customer's email out of a review, or a token
out of a URL, into a report that then gets shared.

So every snippet passes through here before it reaches a finding, with one deliberate
exception: **business contact details that are themselves the subject of the finding are
preserved.** Redacting the phone number in a "your phone number disagrees with your
markup" finding would make the finding unusable. The exception is narrow, explicit, and
opt-in per call rather than a global setting.

The report and the run log are also scanned before emit, so a secret that reached them by
any route is caught by a second, independent pass.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Tuple

DEFAULT_SNIPPET_LIMIT = 300

# --- personal data ---------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?"
    r"\d{3,4}[\s.-]?\d{3,4}(?:[\s.-]?\d{2,4})?(?![\w.])"
)
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# --- secrets ---------------------------------------------------------------------------
# Patterns for credentials that must never appear in output. Prefix-anchored where the
# provider publishes a stable prefix, because that is far more precise than entropy alone.
_SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("aws-access-key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|"
                                  r"ANVA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?"
                                     r"PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer-header", re.compile(r"(?i)\b(?:authorization|bearer)\s*[:=]\s*[A-Za-z0-9._\-]{20,}")),
    ("basic-auth-url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("generic-secret-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|access[_-]?key|"
        r"client[_-]?secret|private[_-]?key)\s*[:=]\s*[\"']?[A-Za-z0-9._\-/+]{16,}")),
)

# Placeholders. Distinctive so a reader can see redaction happened and grep for it.
EMAIL_MASK = "[email redacted]"
PHONE_MASK = "[phone redacted]"
CARD_MASK = "[payment number redacted]"
IBAN_MASK = "[bank identifier redacted]"
SSN_MASK = "[national id redacted]"
IP_MASK = "[ip redacted]"


def _mask_secrets(text: str) -> Tuple[str, List[str]]:
    found: List[str] = []
    out = text
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(out):
            found.append(label)
            out = pattern.sub(f"[{label} redacted]", out)
    return out, found


def redact(text: str, limit: int = DEFAULT_SNIPPET_LIMIT,
           keep_business_contact: bool = False) -> str:
    """Redact and cap an evidence snippet.

    ``keep_business_contact`` preserves phone numbers and email addresses, and is set
    only by checks where the contact detail *is* the finding -- a markup/text phone
    contradiction, or a local business with no address in its markup. Everywhere else
    contact details are masked, because on a content page they usually belong to a
    person rather than the business.
    """
    if not text:
        return ""
    out, _secrets = _mask_secrets(str(text))

    out = _CARD_RE.sub(lambda m: CARD_MASK if _looks_like_card(m.group(0)) else m.group(0), out)
    out = _IBAN_RE.sub(IBAN_MASK, out)
    out = _SSN_RE.sub(SSN_MASK, out)
    out = _IPV4_RE.sub(lambda m: IP_MASK if _looks_like_ip(m.group(0)) else m.group(0), out)

    if not keep_business_contact:
        out = _EMAIL_RE.sub(EMAIL_MASK, out)
        out = _PHONE_RE.sub(lambda m: PHONE_MASK if _looks_like_phone(m.group(0))
                            else m.group(0), out)

    from . import textnorm
    return textnorm.truncate(out, limit)


def _looks_like_card(value: str) -> bool:
    """Luhn check, so ordinary long digit strings are not masked as payment numbers."""
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _looks_like_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _looks_like_phone(value: str) -> bool:
    """Distinguish a phone number from a price, a year range, or a spec.

    Without this, ``1920 x 1080``, ``2019 - 2024`` and ``49.99`` all get masked as phone
    numbers, which mangles exactly the evidence the commercial checks depend on.
    """
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        return False
    # Bare 4-digit-looking years and simple decimals are not phone numbers.
    if len(digits) <= 8 and not re.search(r"[\s.\-()]", value.strip()):
        return False
    if re.match(r"^\d{4}\s*[-x]\s*\d{4}$", value.strip()):
        return False
    return True


def scan_for_secrets(text: str) -> List[str]:
    """Return the secret pattern labels present in ``text``. Empty when clean.

    Used as an independent pre-emit gate over the whole report and run log, so a secret
    that reached the output by a path other than an evidence snippet is still caught.
    """
    _out, found = _mask_secrets(str(text or ""))
    return sorted(set(found))


def scan_payload(payload) -> List[str]:
    """Recursively scan a JSON-serialisable structure for secrets."""
    import json
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return scan_for_secrets(text)


def redact_url(url: str) -> str:
    """Strip credentials and token-shaped parameters from a URL before printing it."""
    if not url:
        return ""
    out = re.sub(r"://[^/\s:@]+:[^/\s@]+@", "://[credentials redacted]@", url)
    out = re.sub(
        r"(?i)([?&](?:token|auth|key|api[_-]?key|access[_-]?token|sig|signature|"
        r"nonce|otp|magic|session|sid)=)[^&#\s]+",
        r"\1[redacted]", out,
    )
    return out


def redaction_policy() -> Dict[str, object]:
    """Machine-readable policy, embedded in the report's methodology block."""
    return {
        "snippet_limit_chars": DEFAULT_SNIPPET_LIMIT,
        "personal_data_masked": [
            "email addresses", "phone numbers", "payment card numbers (Luhn-verified)",
            "IBANs", "national identifiers", "IPv4 addresses",
        ],
        "secret_patterns_scanned": [label for label, _ in _SECRET_PATTERNS],
        "exception": ("business contact details are preserved only in findings where "
                      "the contact detail is itself the subject of the finding"),
        "pre_emit_gate": ("the rendered report and run log are scanned independently "
                          "for secret patterns before they are written"),
    }
