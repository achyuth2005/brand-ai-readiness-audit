"""Minimal static CSS analysis, for overlay geometry and declared contrast.

Scope, stated up front: this resolves **statically declared** style only. There is no
cascade engine, no specificity resolution beyond source order, and no computed style.
That is a real limitation, and it is handled the way rule R6 requires -- by capping
confidence and recording what could not be determined, never by guessing.

Why bother without a browser at all? Because the two engagement checks that matter most
turn on declarations that are almost always literal in the stylesheet:

* a viewport-covering overlay is ``position:fixed`` with full inset and a high
  ``z-index``, and a scroll lock is ``overflow:hidden`` on ``body``/``html``
* a contrast failure needs both colours declared as literals to be computable at all

``tinycss2`` is used when installed; the fallback tokeniser handles the declaration
subset these checks read. Either way the check states its evidence basis in the finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_AT_RULE_RE = re.compile(r"@[\w-]+[^{;]*(\{(?:[^{}]|\{[^{}]*\})*\}|;)", re.DOTALL)
_DECL_RE = re.compile(r"([-\w]+)\s*:\s*([^;]+)")

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3,8})$")
_RGB_RE = re.compile(r"^rgba?\(([^)]+)\)$", re.IGNORECASE)
_HSL_RE = re.compile(r"^hsla?\(([^)]+)\)$", re.IGNORECASE)

NAMED_COLORS: Dict[str, Tuple[int, int, int]] = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "silver": (192, 192, 192), "maroon": (128, 0, 0),
    "olive": (128, 128, 0), "lime": (0, 255, 0), "aqua": (0, 255, 255),
    "teal": (0, 128, 128), "navy": (0, 0, 128), "fuchsia": (255, 0, 255),
    "purple": (128, 0, 128), "orange": (255, 165, 0), "pink": (255, 192, 203),
    "brown": (165, 42, 42), "beige": (245, 245, 220), "ivory": (255, 255, 240),
    "khaki": (240, 230, 140), "gold": (255, 215, 0), "coral": (255, 127, 80),
    "salmon": (250, 128, 114), "tan": (210, 180, 140), "violet": (238, 130, 238),
    "indigo": (75, 0, 130), "turquoise": (64, 224, 208), "crimson": (220, 20, 60),
    "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
    "dimgray": (105, 105, 105), "dimgrey": (105, 105, 105),
    "whitesmoke": (245, 245, 245), "gainsboro": (220, 220, 220),
    "transparent": (0, 0, 0),
}


@dataclass
class Rule:
    """One CSS rule: its selectors and the declarations it sets."""

    selectors: Tuple[str, ...]
    declarations: Dict[str, str] = field(default_factory=dict)
    order: int = 0
    in_media: str = ""

    def get(self, prop: str) -> str:
        return self.declarations.get(prop.lower(), "")


class Stylesheet:
    """A parsed set of CSS rules with the narrow queries the checks need."""

    def __init__(self, rules: Sequence[Rule], sources: Sequence[str] = (),
                 backend: str = "builtin", unavailable: Sequence[str] = ()):
        self.rules = list(rules)
        self.sources = list(sources)
        self.backend = backend
        self.unavailable = list(unavailable)

    @property
    def complete(self) -> bool:
        """False when a referenced stylesheet could not be read.

        The overlay check consults this and caps its confidence when style information
        was incomplete, rather than treating "we did not see a hiding rule" as "there is
        no hiding rule".
        """
        return not self.unavailable

    def __len__(self) -> int:
        return len(self.rules)

    def declarations_for(self, *, tag: str = "", ident: str = "",
                        classes: Sequence[str] = (), node: Any = None) -> Dict[str, str]:
        """Merge declarations from every rule that matches this node on arrival.

        The rule's *key* simple selector is compared against the node's tag, id and
        classes first. When the parsed ``node`` is supplied, a candidate rule must then
        match as a whole selector against the static DOM, so a state rule such as
        ``.menu--open .backdrop { visibility: visible }`` does not reveal a backdrop whose
        ancestor carries no ``menu--open`` class. Dynamic pseudo-classes (``:hover``,
        ``:focus``, ``:target``) never hold on arrival, and pseudo-element rules style a
        generated box rather than the node. A selector that cannot be evaluated statically
        contributes only hiding declarations: it may hide a node, never reveal one.
        """
        class_set = {c.lower() for c in classes if c}
        merged: Dict[str, str] = {}
        for rule in sorted(self.rules, key=lambda r: r.order):
            for selector in rule.selectors:
                if not self._matches(selector, tag.lower(), ident.lower(), class_set):
                    continue
                holds = True if node is None else selector_holds(selector, node)
                if holds is False:
                    continue
                if holds is None:
                    hiding = {k: v for k, v in rule.declarations.items()
                              if k in HIDING_PROPERTIES}
                    if is_hidden_by_css(hiding)[0]:
                        merged.update(hiding)
                    break
                merged.update(rule.declarations)
                break
        return merged

    @staticmethod
    def _matches(selector: str, tag: str, ident: str, classes: set) -> bool:
        selector = selector.strip()
        if not selector or selector.startswith("@"):
            return False
        # Use the rightmost compound selector -- the one that identifies the subject.
        key = re.split(r"[\s>+~]+", selector)[-1]
        key = key.split("::")[0]
        key = re.sub(r":(hover|focus|active|visited|not\([^)]*\)|nth-[^)]*\)|"
                     r"first-child|last-child|before|after|root)", "", key)
        if not key or key == "*":
            return False
        parts = re.findall(r"[#.]?[\w-]+", key)
        if not parts:
            return False
        for part in parts:
            if part.startswith("#"):
                if part[1:].lower() != ident:
                    return False
            elif part.startswith("."):
                if part[1:].lower() not in classes:
                    return False
            else:
                if tag and part.lower() != tag:
                    return False
        return True

    def body_scroll_locked(self, soup: Any = None) -> Tuple[bool, str]:
        """True when a rule that applies on arrival sets ``overflow:hidden`` on the page.

        The third signal in overlay detection: a scroll lock is what turns a visible
        overlay into a genuine content block rather than something the reader scrolls
        past. A bare ``body``/``html`` rule always applies. A conditional one
        (``body.modal-open``, ``body:has(.search-dialog)``, ``.no-js body``) counts only
        when its condition holds in the static DOM ``soup``; without the DOM it cannot be
        shown to apply and does not count.
        """
        for rule in self.rules:
            overflow = (rule.get("overflow") or rule.get("overflow-y")).lower()
            if "hidden" not in overflow and "clip" not in overflow:
                continue
            for selector in rule.selectors:
                bare = selector.strip().lower()
                subject = re.split(r"[\s>+~]+", bare)[-1]
                base = re.split(r"[.#:\[]", subject, maxsplit=1)[0]
                if base not in ("body", "html"):
                    continue
                if bare != base:
                    root = soup.find(base) if soup is not None else None
                    if root is None or selector_holds(selector.strip(), root) is not True:
                        continue
                return True, f"{selector.strip()} {{ overflow: {overflow} }}"
        return False, ""

    def font_display_missing(self) -> List[str]:
        """``@font-face`` blocks with no ``font-display``, which cause text reflow."""
        out: List[str] = []
        for rule in self.rules:
            if any(s.strip().lower().startswith("@font-face") for s in rule.selectors):
                if not rule.get("font-display"):
                    family = rule.get("font-family") or "(unnamed)"
                    out.append(family.strip().strip('"\''))
        return out


HIDING_PROPERTIES = frozenset({"display", "visibility", "opacity", "transform"})

_PSEUDO_ELEMENT_RE = re.compile(
    r"::|:(?:before|after|first-line|first-letter)\b", re.IGNORECASE)


def selector_holds(selector: str, node: Any) -> Optional[bool]:
    """Whether a full selector matches ``node`` in the static DOM, on arrival.

    ``True`` or ``False`` when that can be decided; ``None`` when the selector uses syntax
    the matcher cannot evaluate. ``soupsieve`` ships with BeautifulSoup and implements
    combinators, ``:has()``, ``:is()`` and ``:not()``; it treats user-action
    pseudo-classes as never matching, which is exactly the arrival state.
    """
    if _PSEUDO_ELEMENT_RE.search(selector):
        return False
    try:
        import soupsieve
        return bool(soupsieve.match(selector, node))
    except Exception:
        return None


def parse(css_sources: Iterable[str], unavailable: Sequence[str] = ()) -> Stylesheet:
    """Parse CSS text into a :class:`Stylesheet`, preferring ``tinycss2``."""
    sources = [s for s in css_sources if s and s.strip()]
    try:
        return _parse_tinycss2(sources, unavailable)
    except Exception:
        return _parse_fallback(sources, unavailable)


def _parse_tinycss2(sources: Sequence[str], unavailable: Sequence[str]) -> Stylesheet:
    import tinycss2  # optional dependency

    rules: List[Rule] = []
    order = 0
    for text in sources:
        for node in tinycss2.parse_stylesheet(text, skip_comments=True,
                                              skip_whitespace=True):
            order += 1
            if node.type == "qualified-rule":
                selectors = tinycss2.serialize(node.prelude).strip()
                declarations = _declarations_from_tinycss2(node.content)
                rules.append(Rule(
                    selectors=tuple(s.strip() for s in selectors.split(",") if s.strip()),
                    declarations=declarations, order=order,
                ))
            elif node.type == "at-rule":
                keyword = (node.lower_at_keyword or "")
                if keyword == "font-face" and node.content is not None:
                    rules.append(Rule(
                        selectors=("@font-face",),
                        declarations=_declarations_from_tinycss2(node.content),
                        order=order,
                    ))
                elif keyword in ("media", "supports", "layer") and node.content is not None:
                    condition = tinycss2.serialize(node.prelude).strip()
                    inner = tinycss2.parse_stylesheet(
                        tinycss2.serialize(node.content), skip_comments=True,
                        skip_whitespace=True)
                    for sub in inner:
                        order += 1
                        if sub.type != "qualified-rule":
                            continue
                        selectors = tinycss2.serialize(sub.prelude).strip()
                        rules.append(Rule(
                            selectors=tuple(s.strip() for s in selectors.split(",")
                                            if s.strip()),
                            declarations=_declarations_from_tinycss2(sub.content),
                            order=order, in_media=condition,
                        ))
    return Stylesheet(rules, sources, backend="tinycss2", unavailable=unavailable)


def _declarations_from_tinycss2(content) -> Dict[str, str]:
    import tinycss2
    out: Dict[str, str] = {}
    if content is None:
        return out
    for decl in tinycss2.parse_declaration_list(content, skip_comments=True,
                                                skip_whitespace=True):
        if decl.type != "declaration":
            continue
        out[decl.lower_name] = tinycss2.serialize(decl.value).strip()
    return out


def _parse_fallback(sources: Sequence[str], unavailable: Sequence[str]) -> Stylesheet:
    """Regex tokeniser for the declaration subset these checks read."""
    rules: List[Rule] = []
    order = 0
    for text in sources:
        cleaned = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
        # Hoist @media/@supports bodies so their rules are visible, then drop the
        # at-rules that carry no declarations of interest.
        media_bodies: List[Tuple[str, str]] = []
        for match in re.finditer(r"@(media|supports|layer)([^{]*)\{", cleaned):
            start = match.end() - 1
            body = _balanced(cleaned, start)
            if body:
                media_bodies.append((match.group(2).strip(), body))
        for match in re.finditer(r"@font-face\s*\{([^{}]*)\}", cleaned, re.IGNORECASE):
            order += 1
            rules.append(Rule(selectors=("@font-face",),
                              declarations=_parse_declarations(match.group(1)),
                              order=order))
        flat = _AT_RULE_RE.sub(" ", cleaned)
        for selectors, body in _RULE_RE.findall(flat):
            order += 1
            rules.append(Rule(
                selectors=tuple(s.strip() for s in selectors.split(",") if s.strip()),
                declarations=_parse_declarations(body), order=order,
            ))
        for condition, body in media_bodies:
            for selectors, decls in _RULE_RE.findall(body):
                order += 1
                rules.append(Rule(
                    selectors=tuple(s.strip() for s in selectors.split(",") if s.strip()),
                    declarations=_parse_declarations(decls), order=order,
                    in_media=condition,
                ))
    return Stylesheet(rules, sources, backend="builtin", unavailable=unavailable)


def _balanced(text: str, start: int) -> str:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    return ""


def _parse_declarations(body: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in _DECL_RE.findall(body):
        out[name.strip().lower()] = value.strip()
    return out


def parse_inline(style: str) -> Dict[str, str]:
    """Parse an inline ``style`` attribute."""
    return _parse_declarations(style or "")


# --------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------

def is_viewport_covering(declarations: Dict[str, str]) -> Tuple[bool, List[str]]:
    """True when declarations describe an element covering the viewport.

    Returns the matched reasons as well, so the finding's evidence can quote the actual
    declarations rather than asserting a conclusion.
    """
    reasons: List[str] = []
    position = (declarations.get("position") or "").lower()
    if position not in ("fixed", "sticky", "absolute"):
        return False, reasons
    reasons.append(f"position: {position}")

    inset = (declarations.get("inset") or "").lower()
    edges = [(declarations.get(edge) or "").lower()
             for edge in ("top", "right", "bottom", "left")]
    zeros = sum(1 for value in edges if value in ("0", "0px", "0%", "0rem", "0em"))
    full_inset = bool(inset and inset.split()[0] in ("0", "0px", "0%")) or zeros >= 3
    if full_inset:
        reasons.append(f"inset: {inset or ' '.join(e or 'auto' for e in edges)}")

    width = (declarations.get("width") or "").lower()
    height = (declarations.get("height") or "").lower()
    full_size = any(v in ("100%", "100vw", "100vh", "100dvh", "100svh")
                    for v in (width, height))
    if full_size:
        reasons.append(f"size: width {width or 'auto'} / height {height or 'auto'}")

    z_index = declarations.get("z-index") or ""
    high_z = False
    try:
        high_z = int(z_index) >= 100
    except (TypeError, ValueError):
        high_z = False
    if high_z:
        reasons.append(f"z-index: {z_index}")

    covering = (full_inset or full_size) and (high_z or position == "fixed")
    return covering, reasons


def is_hidden_by_css(declarations: Dict[str, str]) -> Tuple[bool, str]:
    """True when declarations hide an element. The dominant correct pattern for modals."""
    display = (declarations.get("display") or "").lower()
    if display in ("none",):
        return True, "display: none"
    visibility = (declarations.get("visibility") or "").lower()
    if visibility in ("hidden", "collapse"):
        return True, f"visibility: {visibility}"
    opacity = (declarations.get("opacity") or "").strip()
    try:
        if opacity and float(opacity) == 0.0:
            return True, "opacity: 0"
    except ValueError:
        pass
    if "scale(0)" in (declarations.get("transform") or "").lower():
        return True, "transform: scale(0)"
    return False, ""


def fixed_height_px(declarations: Dict[str, str]) -> float:
    """Declared height in px, for cumulative sticky-chrome measurement. 0 when unknown."""
    for prop in ("height", "min-height"):
        value = (declarations.get(prop) or "").strip().lower()
        match = re.match(r"^([\d.]+)(px|rem|em)$", value)
        if match:
            size = float(match.group(1))
            unit = match.group(2)
            return size * (16.0 if unit in ("rem", "em") else 1.0)
        match = re.match(r"^([\d.]+)vh$", value)
        if match:
            return float(match.group(1)) / 100.0 * 800.0   # assume an 800px viewport
    return 0.0


# --------------------------------------------------------------------------------------
# Colour and contrast
# --------------------------------------------------------------------------------------

def parse_color(value: str) -> Optional[Tuple[int, int, int]]:
    """Parse a literal colour. ``None`` for anything not statically resolvable.

    Returning ``None`` for ``var(--fg)``, ``currentColor`` and gradients is the point:
    the contrast check must stay silent where the value depends on the cascade, rather
    than assume a default and report a failure that may not exist.
    """
    if not value:
        return None
    text = value.strip().lower().rstrip(";")
    if not text or text in ("inherit", "initial", "unset", "currentcolor", "revert",
                            "auto", "none"):
        return None
    if text.startswith(("var(", "calc(", "linear-gradient", "radial-gradient",
                        "color-mix", "light-dark")):
        return None
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]

    hex_match = _HEX_RE.match(text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits[:3])
        elif len(digits) in (6, 8):
            digits = digits[:6]
        else:
            return None
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))

    rgb_match = _RGB_RE.match(text)
    if rgb_match:
        parts = re.split(r"[,\s/]+", rgb_match.group(1).strip())
        try:
            channels = []
            for part in parts[:3]:
                if part.endswith("%"):
                    channels.append(int(round(float(part[:-1]) * 255 / 100)))
                else:
                    channels.append(int(round(float(part))))
            if len(channels) == 3:
                return tuple(max(0, min(255, c)) for c in channels)  # type: ignore
        except ValueError:
            return None

    hsl_match = _HSL_RE.match(text)
    if hsl_match:
        parts = re.split(r"[,\s/]+", hsl_match.group(1).strip())
        try:
            hue = float(parts[0].replace("deg", "")) % 360 / 360.0
            sat = float(parts[1].rstrip("%")) / 100.0
            light = float(parts[2].rstrip("%")) / 100.0
        except (ValueError, IndexError):
            return None
        return _hsl_to_rgb(hue, sat, light)
    return None


def _hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    def channel(p: float, q: float, t: float) -> float:
        if t < 0:
            t += 1
        if t > 1:
            t -= 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    if s == 0:
        value = int(round(l * 255))
        return (value, value, value)
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return (
        int(round(channel(p, q, h + 1 / 3) * 255)),
        int(round(channel(p, q, h) * 255)),
        int(round(channel(p, q, h - 1 / 3) * 255)),
    )


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """WCAG relative luminance."""
    def linear(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: Tuple[int, int, int],
                   background: Tuple[int, int, int]) -> float:
    """WCAG 2.x contrast ratio, 1.0 to 21.0."""
    l1 = relative_luminance(foreground)
    l2 = relative_luminance(background)
    lighter, darker = max(l1, l2), min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def meets_aa(ratio: float, large_text: bool = False) -> bool:
    return ratio >= (3.0 if large_text else 4.5)
