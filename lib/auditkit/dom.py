"""DOM parsing, visible-text extraction, and evidence anchors.

Every analyzer reads pages through this module, so the definition of "the text a machine
sees" is made once, in one place, and is the same definition every check argues from.

Three details here are load-bearing:

**Visible text excludes what a reader cannot see.** ``script``, ``style``, ``noscript``,
``template``, ``head`` and ``aria-hidden`` subtrees are skipped. BeautifulSoup's
``get_text()`` happily returns the contents of ``<script>``, which would make every
text-ratio metric meaningless on a JavaScript-heavy page -- the exact page class the
render checks are about.

**Main content is separated from boilerplate.** A "thin page" judgement compares the
main region against the template's own median, not against nav and footer text. Without
the separation, a page that is 90% chrome looks content-rich.

**Every node can produce a stable selector.** Findings cite ``dom`` evidence refs
carrying a CSS selector; ``verify_report.py`` re-resolves each one against the stored
bytes and fails the report if a selector no longer matches. Anchors therefore have to be
generated deterministically and be re-resolvable, which is what :func:`selector_for`
guarantees.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from . import textnorm
from .lexicons import HIDDEN_CLASS_CONVENTIONS

try:
    from bs4 import BeautifulSoup, Comment, NavigableString, Tag
    HAVE_BS4 = True
except ImportError:  # pragma: no cover - handled by the analyzers' skip path
    HAVE_BS4 = False
    BeautifulSoup = None  # type: ignore
    Comment = NavigableString = Tag = object  # type: ignore

# Tags whose text content is not page content.
NON_CONTENT_TAGS = frozenset({
    "script", "style", "noscript", "template", "head", "title", "meta", "link",
    "svg", "canvas", "iframe", "object", "embed", "param", "source", "track",
    "map", "area", "base", "br", "wbr", "col", "colgroup",
})

# Tags that mark page chrome rather than the content a visitor came for.
BOILERPLATE_TAGS = frozenset({"header", "nav", "footer", "aside"})

BOILERPLATE_NAME_PATTERNS = (
    "header", "navbar", "nav-", "-nav", "navigation", "menu", "footer", "sidebar",
    "side-bar", "aside", "breadcrumb", "cookie", "consent", "banner", "topbar",
    "masthead", "site-head", "site-foot", "social", "share", "newsletter", "subscribe",
    "widget", "promo", "advert", "ad-", "-ads", "skip-link", "pagination", "pager",
)

MAIN_SELECTORS = (
    "main", "[role=main]", "article", "#main", "#content", "#main-content",
    "#primary", ".main-content", ".content-area", ".post-content", ".entry-content",
    ".article-body", ".page-content", "#page-content", "[itemprop=articleBody]",
)

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

BLOCK_TEXT_TAGS = ("p", "li", "dd", "dt", "td", "th", "blockquote", "figcaption",
                   "summary", "caption", "address", "pre")

# Framework mount points. Rule R5: their presence is never itself a defect. They are
# only a *corroborating* signal alongside a measured text delta.
MOUNT_SELECTORS = (
    "#root", "#app", "#__next", "#__nuxt", "#___gatsby", "#gatsby-focus-wrapper",
    "#svelte", "#q-app", "#application", "[data-reactroot]", "[ng-version]",
    "[data-server-rendered]", "[data-svelte]", "[data-vue-app]", "[id=app-root]",
    "[data-turbo-body]",
)

# Hydration payload carriers. These are a ground-truth oracle for "what the page will
# say once JavaScript runs", which is what makes browser-free confirmation possible.
STATE_BLOB_PATTERNS = (
    ("__NEXT_DATA__", re.compile(r"__NEXT_DATA__\s*=\s*", re.IGNORECASE)),
    ("window.__NUXT__", re.compile(r"window\.__NUXT__\s*=\s*", re.IGNORECASE)),
    ("__remixContext", re.compile(r"__remixContext\s*=\s*", re.IGNORECASE)),
    ("__APOLLO_STATE__", re.compile(r"__APOLLO_STATE__\s*=\s*", re.IGNORECASE)),
    ("window.__INITIAL_STATE__", re.compile(r"__INITIAL_STATE__\s*=\s*", re.IGNORECASE)),
    ("window.__PRELOADED_STATE__", re.compile(r"__PRELOADED_STATE__\s*=\s*", re.IGNORECASE)),
    ("window.__data", re.compile(r"window\.__data\s*=\s*", re.IGNORECASE)),
    ("__sveltekit", re.compile(r"__sveltekit_[a-z0-9]+\s*=\s*", re.IGNORECASE)),
    ("self.__next_f", re.compile(r"self\.__next_f\s*(?:\.push)?\s*", re.IGNORECASE)),
)

_JSON_SCRIPT_TYPES = frozenset({
    "application/json", "application/ld+json", "application/x-json",
})


def parse(html: str) -> Any:
    """Parse HTML with the most capable available backend.

    ``lxml`` is preferred for speed and leniency on malformed real-world markup, with
    Python's own ``html.parser`` as the fallback so the package works on a machine where
    only ``beautifulsoup4`` is installed.
    """
    if not HAVE_BS4:
        raise RuntimeError(
            "beautifulsoup4 is required for DOM analysis; install it with "
            "`pip install -r requirements.txt`"
        )
    for backend in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, backend)
        except Exception:
            continue
    raise RuntimeError("no usable HTML parser backend is available")


@dataclass
class Section:
    """A heading and the content that belongs to it, for per-section analysis."""

    level: int
    heading: str
    heading_id: str
    selector: str
    text: str
    node: Any = field(repr=False, default=None)

    @property
    def word_count(self) -> int:
        return textnorm.word_count(self.text)


@dataclass
class LinkRef:
    href: str
    text: str
    rel: Tuple[str, ...]
    in_main: bool
    in_nav: bool
    selector: str


@dataclass
class ImageRef:
    src: str
    alt: Optional[str]          # None means the attribute is absent entirely
    width: Optional[int]
    height: Optional[int]
    has_dimensions: bool
    is_decorative: bool
    in_main: bool
    selector: str
    loading: str = ""
    srcset: str = ""


class Document:
    """One parsed page, with the derived views the detectors need.

    Constructed from the raw response bytes recorded in the ledger. When a rendered DOM
    is also present, a second :class:`Document` is built for it so the two can be
    compared -- the raw response always stays the ground truth a finding is argued from.
    """

    def __init__(self, html: str, url: str = "", entry: Any = None):
        self.html = html or ""
        self.url = url
        self.entry = entry
        self.soup = parse(self.html) if self.html else parse("<html></html>")
        self._cache: Dict[str, Any] = {}

    # -- structural metrics computed from the full tree ---------------------------------

    @property
    def html_bytes(self) -> int:
        return len(self.html.encode("utf-8", errors="replace"))

    @property
    def script_count(self) -> int:
        return len(self.soup.find_all("script"))

    @property
    def script_bytes(self) -> int:
        total = 0
        for node in self.soup.find_all("script"):
            total += len(node.get_text() or "")
            total += len(node.get("src") or "")
        return total

    @property
    def blocking_head_scripts(self) -> List[Any]:
        """Synchronous scripts in ``head`` -- they block first paint."""
        head = self.soup.find("head")
        if head is None:
            return []
        out = []
        for node in head.find_all("script"):
            if node.get("src") is None:
                continue
            if node.has_attr("defer") or node.has_attr("async"):
                continue
            if (node.get("type") or "").lower() == "module":
                continue
            out.append(node)
        return out

    @property
    def lang(self) -> str:
        html_tag = self.soup.find("html")
        raw = (html_tag.get("lang") if html_tag else "") or ""
        return raw.strip().lower().split("-")[0]

    def title(self) -> str:
        node = self.soup.find("title")
        return textnorm.normalise_text(node.get_text()) if node else ""

    def meta(self, name: str) -> str:
        node = self.soup.find("meta", attrs={"name": re.compile(f"^{re.escape(name)}$", re.I)})
        return textnorm.normalise_text(node.get("content") or "") if node else ""

    def meta_http_equiv(self, name: str) -> str:
        node = self.soup.find("meta", attrs={"http-equiv": re.compile(
            f"^{re.escape(name)}$", re.I)})
        return textnorm.normalise_text(node.get("content") or "") if node else ""

    def og(self, prop: str) -> str:
        node = self.soup.find("meta", attrs={"property": re.compile(
            f"^(og:)?{re.escape(prop)}$", re.I)})
        if node is None:
            node = self.soup.find("meta", attrs={"property": f"og:{prop}"})
        return textnorm.normalise_text(node.get("content") or "") if node else ""

    def link_rel(self, rel: str) -> List[Any]:
        out = []
        for node in self.soup.find_all("link"):
            rels = [r.lower() for r in (node.get("rel") or [])]
            if rel.lower() in rels:
                out.append(node)
        return out

    def canonical(self) -> str:
        nodes = self.link_rel("canonical")
        return (nodes[0].get("href") or "").strip() if nodes else ""

    def robots_meta(self) -> List[Tuple[str, str]]:
        """All robots meta directives as ``(agent, content)``.

        ``name="robots"`` applies to every agent; ``name="googlebot"`` and friends are
        per-agent overrides, and reporting them without that distinction is how audits
        misattribute a directive.
        """
        out: List[Tuple[str, str]] = []
        for node in self.soup.find_all("meta"):
            name = (node.get("name") or "").strip().lower()
            if not name:
                continue
            if name == "robots" or name.endswith("bot") or name in (
                "googlebot-news", "google", "slurp", "msnbot", "ia_archiver",
            ):
                content = (node.get("content") or "").strip().lower()
                if content:
                    out.append((name, content))
        return out

    def charset_declared(self) -> str:
        node = self.soup.find("meta", attrs={"charset": True})
        if node:
            return (node.get("charset") or "").strip().lower()
        ct = self.meta_http_equiv("content-type")
        m = re.search(r"charset\s*=\s*([\w\-]+)", ct, re.I)
        return m.group(1).lower() if m else ""

    def has_viewport_meta(self) -> bool:
        return bool(self.meta("viewport"))

    # -- text extraction ----------------------------------------------------------------

    def visible_text(self, scope: Any = None) -> str:
        """Visible text of ``scope`` (default: the whole body).

        Skips non-content tags, HTML comments, and ``aria-hidden``/``hidden`` subtrees,
        then normalises. This is the single definition of "text a machine sees" that the
        whole package uses.
        """
        node = scope if scope is not None else (self.soup.find("body") or self.soup)
        return textnorm.normalise_text(" ".join(self._iter_text(node)))

    def _iter_text(self, node: Any) -> Iterator[str]:
        if node is None:
            return
        name = getattr(node, "name", None)
        if name is not None and name.lower() in NON_CONTENT_TAGS:
            return
        if name is not None and self._is_hidden(node):
            return
        for child in getattr(node, "children", ()):
            child_name = getattr(child, "name", None)
            if child_name is None:
                if isinstance(child, Comment):
                    continue
                text = str(child)
                if text.strip():
                    yield text
            else:
                yield from self._iter_text(child)

    @staticmethod
    def _is_hidden(node: Any) -> bool:
        """True when a node is hidden from assistive technology or display.

        Only *declared* hiding counts: the ``hidden`` attribute, ``aria-hidden="true"``,
        or an inline ``display:none``/``visibility:hidden``. Class-name conventions are
        handled separately in the overlay check, where they are one signal among several
        rather than grounds for excluding content.
        """
        try:
            if node.has_attr("hidden"):
                return True
            if (node.get("aria-hidden") or "").strip().lower() == "true":
                return True
            style = (node.get("style") or "").lower().replace(" ", "")
            if "display:none" in style or "visibility:hidden" in style:
                return True
        except (AttributeError, TypeError):
            return False
        return False

    def main_node(self) -> Any:
        """The main content region, or ``None`` when no landmark or heuristic finds one."""
        if "main_node" in self._cache:
            return self._cache["main_node"]
        node = None
        for selector in MAIN_SELECTORS:
            try:
                found = self.soup.select_one(selector)
            except Exception:
                continue
            if found is not None and textnorm.word_count(self.visible_text(found)) >= 5:
                node = found
                break
        if node is None:
            node = self._heuristic_main()
        self._cache["main_node"] = node
        return node

    def _heuristic_main(self) -> Any:
        """Pick the densest non-boilerplate block when no landmark exists.

        Stated as a heuristic in the reference file rather than implied to be a real
        content-extraction model: it chooses the body-level container with the most
        visible text that is not itself chrome.
        """
        body = self.soup.find("body")
        if body is None:
            return None
        best, best_words = None, 0
        for child in body.find_all(["div", "section", "article", "main"],
                                   recursive=True, limit=400):
            if self.is_boilerplate(child):
                continue
            words = textnorm.word_count(self.visible_text(child))
            if words > best_words:
                best, best_words = child, words
        return best

    @property
    def has_main_landmark(self) -> bool:
        if self.soup.find("main") is not None:
            return True
        if self.soup.find(attrs={"role": re.compile("^main$", re.I)}) is not None:
            return True
        return self.soup.find("article") is not None

    @property
    def main_selector(self) -> str:
        """A selector that matches the landmark :attr:`has_main_landmark` found.

        Evidence references must re-resolve against the stored response, so a page whose
        landmark is ``role="main"`` or an ``<article>`` must not be cited as ``main``.
        """
        if self.soup.find("main") is not None:
            return "main"
        if self.soup.find(attrs={"role": re.compile("^main$", re.I)}) is not None:
            return "[role=main]"
        if self.soup.find("article") is not None:
            return "article"
        return "body"

    def main_text(self) -> str:
        if "main_text" not in self._cache:
            node = self.main_node()
            self._cache["main_text"] = self.visible_text(node) if node is not None \
                else self.visible_text()
        return self._cache["main_text"]

    def body_text(self) -> str:
        if "body_text" not in self._cache:
            self._cache["body_text"] = self.visible_text()
        return self._cache["body_text"]

    def boilerplate_text(self) -> str:
        if "boilerplate_text" in self._cache:
            return self._cache["boilerplate_text"]
        parts: List[str] = []
        for tag in BOILERPLATE_TAGS:
            for node in self.soup.find_all(tag):
                parts.append(self.visible_text(node))
        self._cache["boilerplate_text"] = textnorm.normalise_text(" ".join(parts))
        return self._cache["boilerplate_text"]

    @classmethod
    def is_boilerplate(cls, node: Any) -> bool:
        """True when a node is page chrome, by tag or by class/id naming convention."""
        current = node
        depth = 0
        while current is not None and depth < 8:
            name = getattr(current, "name", None)
            if name in BOILERPLATE_TAGS:
                return True
            if name is not None:
                try:
                    role = (current.get("role") or "").lower()
                    if role in ("navigation", "banner", "contentinfo", "complementary"):
                        return True
                    ident = " ".join(
                        [current.get("id") or ""] + list(current.get("class") or [])
                    ).lower()
                except (AttributeError, TypeError):
                    ident = ""
                if any(p in ident for p in BOILERPLATE_NAME_PATTERNS):
                    return True
            current = getattr(current, "parent", None)
            depth += 1
        return False

    def text_ratio(self) -> float:
        """Visible-body-text bytes as a fraction of total HTML bytes."""
        if self.html_bytes == 0:
            return 0.0
        return len(self.body_text().encode("utf-8")) / self.html_bytes

    # -- structure ----------------------------------------------------------------------

    def headings(self, scope: Any = None) -> List[Tuple[int, str, Any]]:
        """``(level, text, node)`` for every heading, in document order."""
        root = scope if scope is not None else self.soup
        out: List[Tuple[int, str, Any]] = []
        for node in root.find_all(HEADING_TAGS):
            if self._is_hidden(node):
                continue
            text = self.visible_text(node)
            out.append((int(node.name[1]), text, node))
        return out

    def h1s(self) -> List[str]:
        return [text for level, text, _ in self.headings() if level == 1 and text]

    def paragraphs(self, scope: Any = None, min_words: int = 1) -> List[str]:
        root = scope if scope is not None else (self.main_node() or self.soup)
        out = []
        for node in root.find_all(["p", "li", "blockquote", "dd"]):
            if self._is_hidden(node) or self.is_boilerplate(node):
                continue
            text = self.visible_text(node)
            if textnorm.word_count(text) >= min_words:
                out.append(text)
        return out

    def sections(self, scope: Any = None, min_level: int = 2,
                 max_level: int = 3) -> List[Section]:
        """Split the main region into heading subtrees.

        Per-section evaluation matters: one badly-written section inside an otherwise
        good page is still a section an assistant may retrieve, and averaging it away
        across the whole page hides it.
        """
        root = scope if scope is not None else (self.main_node() or self.soup.find("body"))
        if root is None:
            return []
        out: List[Section] = []
        headings = [(lvl, text, node) for lvl, text, node in self.headings(root)
                    if min_level <= lvl <= max_level]
        for level, heading_text, node in headings:
            collected: List[str] = []
            for sibling in node.next_siblings:
                sib_name = getattr(sibling, "name", None)
                if sib_name in HEADING_TAGS and int(sib_name[1]) <= level:
                    break
                if sib_name is None:
                    if not isinstance(sibling, Comment) and str(sibling).strip():
                        collected.append(str(sibling))
                else:
                    collected.append(self.visible_text(sibling))
            out.append(Section(
                level=level,
                heading=heading_text,
                heading_id=(node.get("id") or "").strip(),
                selector=selector_for(node),
                text=textnorm.normalise_text(" ".join(collected)),
                node=node,
            ))
        return out

    def links(self) -> List[LinkRef]:
        if "links" in self._cache:
            return self._cache["links"]
        main = self.main_node()
        out: List[LinkRef] = []
        for node in self.soup.find_all("a"):
            href = (node.get("href") or "").strip()
            if not href:
                continue
            rels = tuple(r.lower() for r in (node.get("rel") or []))
            out.append(LinkRef(
                href=href,
                text=self.visible_text(node),
                rel=rels,
                in_main=bool(main is not None and _is_within(node, main)),
                in_nav=self.is_boilerplate(node),
                selector=selector_for(node),
            ))
        self._cache["links"] = out
        return out

    def images(self) -> List[ImageRef]:
        if "images" in self._cache:
            return self._cache["images"]
        main = self.main_node()
        out: List[ImageRef] = []
        for node in self.soup.find_all("img"):
            alt = node.get("alt")
            width = _int_attr(node.get("width"))
            height = _int_attr(node.get("height"))
            style = (node.get("style") or "").lower()
            has_dims = bool((width and height)
                            or "aspect-ratio" in style
                            or node.get("data-aspect-ratio"))
            # alt="" plus a presentation role is the *correct* pattern for decoration.
            # Treating it as a defect is the classic accessibility false positive.
            role = (node.get("role") or "").strip().lower()
            is_decorative = (alt is not None and alt.strip() == "") or \
                            role in ("presentation", "none")
            out.append(ImageRef(
                src=(node.get("src") or node.get("data-src") or "").strip(),
                alt=alt,
                width=width,
                height=height,
                has_dimensions=has_dims,
                is_decorative=is_decorative,
                in_main=bool(main is not None and _is_within(node, main)),
                selector=selector_for(node),
                loading=(node.get("loading") or "").strip().lower(),
                srcset=(node.get("srcset") or "").strip(),
            ))
        self._cache["images"] = out
        return out

    def forms(self) -> List[Dict[str, Any]]:
        """Form inventory. Read only -- nothing here is ever submitted."""
        out: List[Dict[str, Any]] = []
        for form in self.soup.find_all("form"):
            fields: List[Dict[str, Any]] = []
            for node in form.find_all(["input", "select", "textarea"]):
                itype = (node.get("type") or node.name).lower()
                if itype in ("hidden", "submit", "button", "reset", "image"):
                    continue
                fields.append({
                    "type": itype,
                    "name": (node.get("name") or "").strip(),
                    "required": node.has_attr("required")
                    or (node.get("aria-required") or "").lower() == "true",
                    "labelled": self._is_labelled(node, form),
                    "autocomplete": (node.get("autocomplete") or "").strip(),
                    "selector": selector_for(node),
                })
            out.append({
                "action": (form.get("action") or "").strip(),
                "method": (form.get("method") or "get").strip().lower(),
                "fields": fields,
                "field_count": len(fields),
                "required_count": sum(1 for f in fields if f["required"]),
                "unlabelled": [f for f in fields if not f["labelled"]],
                "has_password": any(f["type"] == "password" for f in fields),
                "has_email": any(f["type"] == "email" for f in fields),
                "in_main": bool(self.main_node() is not None
                                and _is_within(form, self.main_node())),
                "selector": selector_for(form),
                "text": self.visible_text(form),
            })
        return out

    @staticmethod
    def _is_labelled(node: Any, form: Any) -> bool:
        """True when a field has any programmatic label.

        Four mechanisms all count, because all four work for assistive technology:
        ``aria-label``, ``aria-labelledby``, a wrapping ``<label>``, and a ``<label for>``
        pointing at the field's id. Requiring only ``<label for>`` would flag a large
        amount of correct markup.
        """
        try:
            if (node.get("aria-label") or "").strip():
                return True
            if (node.get("aria-labelledby") or "").strip():
                return True
            if (node.get("title") or "").strip():
                return True
            parent = node.parent
            depth = 0
            while parent is not None and depth < 4:
                if getattr(parent, "name", None) == "label":
                    return True
                parent = parent.parent
                depth += 1
            node_id = (node.get("id") or "").strip()
            if node_id and form is not None:
                for label in form.find_all("label"):
                    if (label.get("for") or "").strip() == node_id:
                        return True
        except (AttributeError, TypeError):
            return False
        return False

    def tables(self, scope: Any = None) -> List[Any]:
        root = scope if scope is not None else (self.main_node() or self.soup)
        return [n for n in root.find_all(["table", "dl"]) if not self.is_boilerplate(n)]

    def lists(self, scope: Any = None) -> List[Any]:
        root = scope if scope is not None else (self.main_node() or self.soup)
        return [n for n in root.find_all(["ul", "ol"]) if not self.is_boilerplate(n)]

    def mount_nodes(self) -> List[Any]:
        """Framework mount points. Never a defect on their own (rule R5)."""
        found: List[Any] = []
        for selector in MOUNT_SELECTORS:
            try:
                found.extend(self.soup.select(selector))
            except Exception:
                continue
        unique: List[Any] = []
        seen = set()
        for node in found:
            marker = id(node)
            if marker not in seen:
                seen.add(marker)
                unique.append(node)
        return unique

    def noscript_text(self) -> str:
        return textnorm.normalise_text(
            " ".join(n.get_text() or "" for n in self.soup.find_all("noscript"))
        )

    def above_fold_nodes(self, limit: int = 120) -> List[Any]:
        """The first ``limit`` elements of the main region in document order.

        An explicit, stated proxy for a viewport. This package has no layout engine, so
        it does not pretend to measure a fold; the reference file says so directly rather
        than implying a real measurement.
        """
        root = self.main_node() or self.soup.find("body") or self.soup
        out: List[Any] = []
        for node in root.descendants:
            if getattr(node, "name", None) is None:
                continue
            if node.name in NON_CONTENT_TAGS:
                continue
            out.append(node)
            if len(out) >= limit:
                break
        return out

    def above_fold_text(self, limit: int = 120) -> str:
        nodes = self.above_fold_nodes(limit)
        parts = []
        for node in nodes:
            if node.name in BLOCK_TEXT_TAGS or node.name in HEADING_TAGS:
                parts.append(self.visible_text(node))
        return textnorm.normalise_text(" ".join(parts))

    # -- hydration payloads -------------------------------------------------------------

    def state_blobs(self) -> List[Dict[str, Any]]:
        """Hydration payloads found in the raw HTML, parsed where possible.

        Each entry is ``{"name", "raw", "data", "script_index"}``. ``data`` is ``None``
        when the payload did not parse as JSON, which is common for assignment-style
        blobs that contain JavaScript expressions -- in that case the *raw* text is still
        usable for span extraction.
        """
        if "state_blobs" in self._cache:
            return self._cache["state_blobs"]
        out: List[Dict[str, Any]] = []
        for index, node in enumerate(self.soup.find_all("script")):
            script_type = (node.get("type") or "").strip().lower()
            content = node.get_text() or ""
            if not content.strip():
                continue

            node_id = (node.get("id") or "").strip()
            if script_type in _JSON_SCRIPT_TYPES and script_type != "application/ld+json":
                data = _try_json(content)
                out.append({"name": node_id or f"script[type={script_type}]",
                            "raw": content, "data": data, "script_index": index})
                continue
            if node_id == "__NEXT_DATA__":
                out.append({"name": "__NEXT_DATA__", "raw": content,
                            "data": _try_json(content), "script_index": index})
                continue
            for name, pattern in STATE_BLOB_PATTERNS:
                match = pattern.search(content)
                if not match:
                    continue
                payload = content[match.end():].strip()
                out.append({"name": name, "raw": payload,
                            "data": _try_json(_trim_to_json(payload)),
                            "script_index": index})
                break
        self._cache["state_blobs"] = out
        return out

    def blob_text_spans(self, min_chars: int = 40, min_words: int = 6) -> List[Dict[str, Any]]:
        """Natural-language strings inside hydration payloads.

        These are what a page *will* say once JavaScript runs. A span present here and
        absent from the static text proves client-side rendering from a single fetch,
        with no browser -- the key idea behind tier 2 of the render check.

        Filtering is strict on purpose: identifiers, URLs, hashes, enum values and CSS
        fragments are not prose, and counting them would manufacture a delta.
        """
        out: List[Dict[str, Any]] = []
        for blob in self.state_blobs():
            if blob["data"] is not None:
                for path, value in _walk_json_strings(blob["data"]):
                    if _is_prose(value, min_chars, min_words):
                        out.append({"blob": blob["name"], "path": path, "text": value})
            else:
                for value in _extract_quoted_strings(blob["raw"]):
                    if _is_prose(value, min_chars, min_words):
                        out.append({"blob": blob["name"], "path": "(unparsed)",
                                    "text": value})
        # Deduplicate while keeping the first path seen for each span.
        seen = set()
        unique: List[Dict[str, Any]] = []
        for item in out:
            key = textnorm.comparison_key(item["text"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def jsonld_blocks(self) -> List[Dict[str, Any]]:
        """Raw JSON-LD script blocks with source line numbers.

        Line numbers are recovered here rather than delegated to an extraction library,
        because a syntax error is a *finding* that needs a location an author can act on
        -- "invalid JSON-LD" without a line number is not actionable.
        """
        out: List[Dict[str, Any]] = []
        for index, node in enumerate(self.soup.find_all("script")):
            if (node.get("type") or "").strip().lower() != "application/ld+json":
                continue
            content = node.get_text() or ""
            out.append({
                "index": index,
                "raw": content,
                "line": self._line_of(content),
                "selector": selector_for(node),
            })
        return out

    def _line_of(self, needle: str) -> int:
        """1-based source line where ``needle`` begins, or 0 when not locatable."""
        if not needle:
            return 0
        probe = needle.strip()[:120]
        if not probe:
            return 0
        position = self.html.find(probe)
        if position < 0:
            return 0
        return self.html.count("\n", 0, position) + 1

    def inline_styles(self) -> List[str]:
        """Contents of every ``<style>`` block, for static overlay/contrast analysis."""
        return [n.get_text() or "" for n in self.soup.find_all("style")]

    def stylesheet_hrefs(self) -> List[str]:
        return [(n.get("href") or "").strip() for n in self.link_rel("stylesheet")
                if (n.get("href") or "").strip()]

    def data_nosnippet_nodes(self) -> List[Any]:
        try:
            return list(self.soup.select("[data-nosnippet]"))
        except Exception:
            return []

    def iframes(self) -> List[Any]:
        return list(self.soup.find_all("iframe"))

    def media(self) -> List[Any]:
        return list(self.soup.find_all(["video", "audio"]))

    def svgs(self) -> List[Any]:
        return list(self.soup.find_all("svg"))

    def select(self, selector: str) -> List[Any]:
        try:
            return list(self.soup.select(selector))
        except Exception:
            return []

    def select_one(self, selector: str) -> Any:
        try:
            return self.soup.select_one(selector)
        except Exception:
            return None


# --------------------------------------------------------------------------------------
# Evidence anchors
# --------------------------------------------------------------------------------------

def selector_for(node: Any, max_depth: int = 6) -> str:
    """Build a deterministic, re-resolvable CSS selector for ``node``.

    Prefers an ``id`` when one exists (short and stable), otherwise walks up to
    ``max_depth`` ancestors emitting ``tag:nth-of-type(n)`` steps. The result is stored
    in ``dom`` evidence refs and re-resolved by the verification gate, so the selector
    has to match the same node when re-run against the stored bytes -- which is why
    positional steps are used rather than class names that a build can change.
    """
    if node is None or getattr(node, "name", None) is None:
        return ""
    try:
        node_id = (node.get("id") or "").strip()
        if node_id and re.match(r"^[A-Za-z][\w\-]*$", node_id):
            return f"#{node_id}"
    except (AttributeError, TypeError):
        pass

    steps: List[str] = []
    current = node
    depth = 0
    while current is not None and getattr(current, "name", None) not in (None, "[document]") \
            and depth < max_depth:
        name = current.name
        if name == "html":
            break
        parent = current.parent
        if parent is None or getattr(parent, "name", None) in (None, "[document]"):
            steps.append(name)
            break
        siblings = [c for c in parent.children if getattr(c, "name", None) == name]
        if len(siblings) > 1:
            try:
                position = siblings.index(current) + 1
            except ValueError:
                position = 1
            steps.append(f"{name}:nth-of-type({position})")
        else:
            steps.append(name)
        try:
            parent_id = (parent.get("id") or "").strip()
            if parent_id and re.match(r"^[A-Za-z][\w\-]*$", parent_id):
                steps.append(f"#{parent_id}")
                break
        except (AttributeError, TypeError):
            pass
        current = parent
        depth += 1
    return " > ".join(reversed(steps))


def resolve_selector(document: "Document", selector: str) -> Any:
    """Re-resolve a stored selector. Used by the verification gate."""
    if not selector:
        return None
    return document.select_one(selector)


def excerpt(node: Any, limit: int = 300) -> str:
    """A capped, normalised text excerpt of a node, for evidence."""
    if node is None:
        return ""
    if isinstance(node, str):
        return textnorm.truncate(node, limit)
    try:
        text = node.get_text(" ")
    except AttributeError:
        text = str(node)
    return textnorm.truncate(text, limit)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

def _is_within(node: Any, ancestor: Any) -> bool:
    current = getattr(node, "parent", None)
    depth = 0
    while current is not None and depth < 40:
        if current is ancestor:
            return True
        current = getattr(current, "parent", None)
        depth += 1
    return False


def _int_attr(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return None


def _try_json(text: str) -> Any:
    if not text:
        return None
    candidate = text.strip().rstrip(";").strip()
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


def _trim_to_json(text: str) -> str:
    """Trim an assignment payload to its outermost JSON literal.

    ``window.__NUXT__ = {...};`` and ``...= JSON.parse("{...}")`` both occur. This takes
    the first balanced ``{}`` or ``[]`` span, which is enough for the common cases and
    fails cleanly (returning the input) for the rest -- the raw-string path then handles
    span extraction.
    """
    stripped = text.strip()
    if not stripped:
        return stripped
    start = None
    for index, char in enumerate(stripped[:200]):
        if char in "{[":
            start = index
            break
    if start is None:
        return stripped
    opener = stripped[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return stripped[start:index + 1]
    return stripped[start:]


def _walk_json_strings(data: Any, path: str = "$",
                       depth: int = 0) -> Iterator[Tuple[str, str]]:
    """Yield ``(jsonpath, value)`` for every string leaf, depth-capped."""
    if depth > 12:
        return
    if isinstance(data, str):
        yield path, data
    elif isinstance(data, dict):
        for key in sorted(data):
            yield from _walk_json_strings(data[key], f"{path}.{key}", depth + 1)
    elif isinstance(data, list):
        for index, item in enumerate(data[:200]):
            yield from _walk_json_strings(item, f"{path}[{index}]", depth + 1)


_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.){20,})"')


def _extract_quoted_strings(text: str, limit: int = 400) -> List[str]:
    out: List[str] = []
    for match in _QUOTED_RE.finditer(text[:600_000]):
        try:
            out.append(json.loads(f'"{match.group(1)}"'))
        except ValueError:
            out.append(match.group(1))
        if len(out) >= limit:
            break
    return out


_URLISH_RE = re.compile(r"^(https?://|//|/[\w\-./]*$|data:|blob:|#|\$\{)")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_\-:.]+$")
_HASHISH_RE = re.compile(r"^[0-9a-fA-F]{16,}$|^[A-Za-z0-9+/=]{40,}$")
_CSSISH_RE = re.compile(r"[{};]\s*[\w-]+\s*:|^\.[\w-]+\s*\{|@media|!important")
_CODEISH_RE = re.compile(r"function\s*\(|=>|\breturn\b|\bvar\b|\bconst\b|</[a-z]+>")


def _is_prose(value: str, min_chars: int, min_words: int) -> bool:
    """True when a string is natural-language prose rather than machinery.

    Deliberately strict. Every false accept here becomes a phantom render delta, and a
    phantom delta becomes a critical finding on a site that is working correctly -- the
    worst outcome this package can produce.
    """
    if not value or len(value) < min_chars:
        return False
    text = value.strip()
    if len(text) < min_chars:
        return False
    if _URLISH_RE.match(text) or _HASHISH_RE.match(text):
        return False
    if _IDENTIFIER_RE.match(text):
        return False
    # A JSON document serialised into a string (rich-text models, CMS blocks) is data. Its
    # keys and brackets would be counted as missing prose and quoted as evidence.
    if text[:1] in "[{" and text[-1:] in "]}":
        try:
            json.loads(text)
            return False
        except ValueError:
            pass
    if _CSSISH_RE.search(text) or _CODEISH_RE.search(text):
        return False
    tokens = textnorm.words(text)
    if len(tokens) < min_words:
        return False
    # Prose has spaces between most words and a sane letter ratio.
    letters = sum(1 for c in text if c.isalpha())
    if letters / len(text) < 0.55:
        return False
    if text.count(" ") < min_words - 2:
        return False
    # Reject strings that are mostly one repeated token (placeholder padding).
    if len(set(t.lower() for t in tokens)) < max(3, len(tokens) // 4):
        return False
    return True


def looks_hidden_by_class(node: Any) -> bool:
    """True when class/id naming says "hidden until triggered".

    The dominant convention on the web, and the most important single guard in the
    overlay check: most modal markup in a page is inert until a script reveals it.
    """
    try:
        ident = " ".join([node.get("id") or ""] + list(node.get("class") or [])).lower()
    except (AttributeError, TypeError):
        return False
    return any(convention in ident for convention in HIDDEN_CLASS_CONVENTIONS)


def node_identity(node: Any) -> str:
    """``tag#id.class1.class2`` -- a compact, human-readable node label for evidence."""
    if node is None or getattr(node, "name", None) is None:
        return ""
    try:
        parts = [node.name]
        node_id = (node.get("id") or "").strip()
        if node_id:
            parts.append(f"#{node_id}")
        classes = [c for c in (node.get("class") or []) if c][:3]
        for cls in classes:
            parts.append(f".{cls}")
        return "".join(parts)
    except (AttributeError, TypeError):
        return str(getattr(node, "name", ""))


def text_of(nodes: Iterable[Any], document: "Document") -> str:
    return textnorm.normalise_text(" ".join(document.visible_text(n) for n in nodes))


def sequence_delta(static_text: str, rendered_text: str) -> Dict[str, Any]:
    """Token-level diff between static and rendered text.

    Both sides are normalised and case-folded first. Skipping that step is how encoding
    and punctuation noise fakes a render delta on a correctly server-rendered page.
    """
    import difflib
    static_tokens = textnorm.comparison_key(static_text).split()
    rendered_tokens = textnorm.comparison_key(rendered_text).split()
    matcher = difflib.SequenceMatcher(None, static_tokens, rendered_tokens, autojunk=False)
    added: List[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(rendered_tokens[j1:j2])
    added_chars = sum(len(t) + 1 for t in added)
    return {
        "static_tokens": len(static_tokens),
        "rendered_tokens": len(rendered_tokens),
        "added_tokens": len(added),
        "added_chars": added_chars,
        "delta_ratio": (len(added) / len(rendered_tokens)) if rendered_tokens else 0.0,
        "added_text": " ".join(added),
    }


def detect_language(document: "Document") -> str:
    """Page language from ``<html lang>``, falling back to ``og:locale``.

    No statistical language identification: declaring the source of the judgement and
    skipping when it is absent is more honest than guessing, and the answerability
    lexicons are English-only by design.
    """
    lang = document.lang
    if lang:
        return lang
    locale = document.og("locale")
    if locale:
        return locale.strip().lower().replace("_", "-").split("-")[0]
    meta_lang = document.meta("language") or document.meta_http_equiv("content-language")
    if meta_lang:
        return meta_lang.strip().lower().split("-")[0]
    return ""
