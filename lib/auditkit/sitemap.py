"""Sitemap parsing from the evidence ledger.

Shared by access-policy (is the sitemap valid and complete?), entity-trust (is lastmod
honest?) and engagement (which sitemap URLs are orphaned?). Pure: it reads stored bytes
and never fetches -- the orchestrator's discovery phase already fetched every sitemap
through the egress gateway, capped in count and size.
"""

from __future__ import annotations

import gzip
import zlib
from typing import Any, Dict, List, Tuple
from xml.etree import ElementTree


def _gunzip(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except (OSError, zlib.error):
            return data
    return data


def parse(data: bytes) -> Dict[str, Any]:
    """Parse a urlset or sitemapindex. Never raises: problems land in ``error``."""
    result: Dict[str, Any] = {"kind": "", "urls": [], "lastmods": [], "children": [],
                              "error": ""}
    data = _gunzip(data or b"")
    if not data.strip():
        result["error"] = "empty body"
        return result
    try:
        # ElementTree does not resolve external entities, so a hostile sitemap cannot
        # use XXE to read local files.
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        result["error"] = f"XML parse error: {exc}"
        return result

    kind = root.tag.rsplit("}", 1)[-1].lower()
    if kind not in ("urlset", "sitemapindex"):
        result["error"] = (f"root element is <{kind}>, not <urlset> or <sitemapindex>, "
                           f"so consumers will discard the file")
        return result
    result["kind"] = kind
    for child in root:
        name = child.tag.rsplit("}", 1)[-1].lower()
        if name not in ("url", "sitemap"):
            continue
        loc = lastmod = ""
        for leaf in child:
            leaf_name = leaf.tag.rsplit("}", 1)[-1].lower()
            if leaf_name == "loc":
                loc = (leaf.text or "").strip()
            elif leaf_name == "lastmod":
                lastmod = (leaf.text or "").strip()
        if not loc:
            continue
        if name == "sitemap":
            result["children"].append(loc)
        else:
            result["urls"].append(loc)
            if lastmod:
                result["lastmods"].append((loc, lastmod))
    return result


def collect(ledger: Any) -> Dict[str, Any]:
    """Every sitemap in the ledger, merged. Deterministically ordered."""
    urls: List[str] = []
    lastmods: List[Tuple[str, str]] = []
    errors: List[Tuple[Any, str]] = []
    entries: List[Any] = []
    for entry in ledger.entries(purpose="sitemap"):
        if not entry.ok:
            continue
        entries.append(entry)
        parsed = parse(ledger.body(entry))
        if parsed["error"]:
            errors.append((entry, parsed["error"]))
            continue
        urls.extend(parsed["urls"])
        lastmods.extend(parsed["lastmods"])
    return {
        "urls": sorted(set(urls)),
        "lastmods": sorted(set(lastmods)),
        "errors": errors,
        "entries": entries,
    }
