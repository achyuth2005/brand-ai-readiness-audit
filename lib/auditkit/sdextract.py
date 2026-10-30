"""Structured-data extraction and graph normalisation.

Extracts JSON-LD, Microdata and RDFa-lite, then normalises everything to one flat list
of typed nodes so property checks have a single shape to work against.

Two details that decide whether this check suite works at all:

**Normalising the JSON-LD container.** A block may be a single object, an array of
objects, or ``{"@graph": [...]}``, and nodes nest arbitrarily inside properties. Code
that only handles the single-object case silently passes on sites whose markup it never
actually looked at -- a coverage check that inspects nothing reports no problems. Every
container shape is flattened here, once.

**Recovering raw blocks ourselves.** ``extruct`` raises on malformed JSON-LD, and a
syntax error is a *finding* with a line and column an author can fix, not a crash and not
a silent discard. So raw blocks are always recovered and parsed per-block, with the
parser's own error message and position captured as evidence.

``extruct`` is used when installed (it handles RDFa far more thoroughly than the built-in
path) and is otherwise not required: JSON-LD is the dominant carrier and is pure
``json.loads``, so the built-in path is load-bearing rather than a stub.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import textnorm

SCHEMA_CONTEXT_PATTERN = re.compile(r"schema\.org", re.IGNORECASE)

# Microdata / RDFa property containers where the value lives in an attribute rather than
# in the element's text.
_VALUE_ATTRIBUTES = {
    "meta": "content", "audio": "src", "embed": "src", "iframe": "src", "img": "src",
    "source": "src", "track": "src", "video": "src", "a": "href", "area": "href",
    "link": "href", "object": "data", "data": "value", "meter": "value",
    "time": "datetime",
}


@dataclass
class SDNode:
    """One structured-data entity, flattened out of whatever container held it."""

    types: Tuple[str, ...]
    props: Dict[str, Any]
    syntax: str                 # json-ld | microdata | rdfa
    node_id: str = ""
    block_index: int = 0
    json_path: str = "$"
    selector: str = ""
    parent_prop: str = ""

    @property
    def primary_type(self) -> str:
        return self.types[0] if self.types else ""

    def has_type(self, *names: str) -> bool:
        wanted = {n.lower() for n in names}
        return any(t.lower() in wanted for t in self.types)

    def get(self, prop: str, default: Any = None) -> Any:
        """Case-insensitive property lookup."""
        if prop in self.props:
            return self.props[prop]
        lowered = prop.lower()
        for key, value in self.props.items():
            if key.lower() == lowered:
                return value
        return default

    def get_path(self, dotted: str, default: Any = None) -> Any:
        """Dotted lookup that walks into nested nodes and single-element lists.

        ``offers.price`` has to work whether ``offers`` is an object, a list of objects,
        or a nested :class:`SDNode`, because all three occur in the wild.
        """
        current: Any = self
        for part in dotted.split("."):
            if isinstance(current, SDNode):
                current = current.get(part)
            elif isinstance(current, dict):
                current = _dict_get_ci(current, part)
            elif isinstance(current, list):
                found = None
                for item in current:
                    candidate = (item.get(part) if isinstance(item, SDNode)
                                 else _dict_get_ci(item, part)
                                 if isinstance(item, dict) else None)
                    if candidate is not None:
                        found = candidate
                        break
                current = found
            else:
                return default
            if current is None:
                return default
        return current

    def text_values(self, prop: str) -> List[str]:
        """All scalar string values of a property, flattened."""
        return [str(v) for v in _flatten_scalars(self.get(prop))]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "types": list(self.types),
            "syntax": self.syntax,
            "node_id": self.node_id,
            "json_path": self.json_path,
            "props": _jsonable(self.props),
        }


@dataclass
class SDBlock:
    """One markup block, valid or not. An invalid block is still reportable evidence."""

    index: int
    syntax: str
    raw: str = ""
    line: int = 0
    selector: str = ""
    parsed: Any = None
    error: str = ""
    error_position: str = ""
    nodes: List[SDNode] = field(default_factory=list)
    context: Any = None

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def has_schema_context(self) -> bool:
        if self.context is None:
            return False
        return bool(SCHEMA_CONTEXT_PATTERN.search(json.dumps(self.context)))


@dataclass
class SDExtraction:
    """Everything the structured-data checks need from one page."""

    blocks: List[SDBlock] = field(default_factory=list)
    nodes: List[SDNode] = field(default_factory=list)
    backend: str = "builtin"

    @property
    def syntaxes(self) -> Tuple[str, ...]:
        return tuple(sorted({b.syntax for b in self.blocks if b.nodes}))

    @property
    def has_any(self) -> bool:
        return bool(self.nodes)

    @property
    def broken_blocks(self) -> List[SDBlock]:
        return [b for b in self.blocks if b.error]

    def types_present(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for node in self.nodes:
            for type_name in node.types:
                if type_name not in seen:
                    seen.append(type_name)
        return tuple(sorted(seen))

    def of_type(self, *names: str) -> List[SDNode]:
        return [n for n in self.nodes if n.has_type(*names)]

    def first_of_type(self, *names: str) -> Optional[SDNode]:
        matches = self.of_type(*names)
        return matches[0] if matches else None

    def jsonld_nodes(self) -> List[SDNode]:
        return [n for n in self.nodes if n.syntax == "json-ld"]


def extract(document: Any, base_url: str = "") -> SDExtraction:
    """Extract every syntax from a parsed document."""
    result = SDExtraction()

    # --- JSON-LD: always parsed here, per block, so errors carry a position ----------
    for block_info in document.jsonld_blocks():
        block = SDBlock(
            index=block_info["index"], syntax="json-ld", raw=block_info["raw"],
            line=block_info["line"], selector=block_info["selector"],
        )
        text = (block_info["raw"] or "").strip()
        if not text:
            block.error = "the script block is empty"
            result.blocks.append(block)
            continue
        try:
            block.parsed = json.loads(text)
        except ValueError as exc:
            block.error = str(exc)
            block.error_position = _describe_json_error(text, exc)
            result.blocks.append(block)
            continue

        block.context = _extract_context(block.parsed)
        for node in _flatten_jsonld(block.parsed, block.index, block.selector):
            block.nodes.append(node)
            result.nodes.append(node)
        result.blocks.append(block)

    # --- Microdata --------------------------------------------------------------------
    micro_nodes = _extract_microdata(document)
    if micro_nodes:
        block = SDBlock(index=1000, syntax="microdata", nodes=micro_nodes)
        result.blocks.append(block)
        result.nodes.extend(micro_nodes)

    # --- RDFa-lite --------------------------------------------------------------------
    rdfa_nodes = _extract_rdfa(document)
    if rdfa_nodes:
        block = SDBlock(index=2000, syntax="rdfa", nodes=rdfa_nodes)
        result.blocks.append(block)
        result.nodes.extend(rdfa_nodes)

    # --- extruct cross-check ----------------------------------------------------------
    # When available, extruct's RDFa and Microdata handling is more complete than the
    # built-in path. Anything it finds that we did not is merged in; we never discard our
    # own JSON-LD parse, because ours carries the line numbers.
    merged = _augment_with_extruct(document, base_url, result)
    if merged:
        result.backend = "extruct+builtin"
    return result


def _extract_context(parsed: Any) -> Any:
    if isinstance(parsed, dict):
        return parsed.get("@context")
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and "@context" in item:
                return item["@context"]
    return None


def _flatten_jsonld(parsed: Any, block_index: int, selector: str,
                    path: str = "$", parent_prop: str = "",
                    depth: int = 0) -> List[SDNode]:
    """Flatten any JSON-LD container shape into a list of typed nodes.

    Handles the single object, the root array, ``@graph``, and nodes nested inside
    properties (``Product.offers``, ``Article.author``, ``BreadcrumbList.itemListElement``)
    at any depth. Nested nodes are kept *as nodes* so property checks can validate an
    ``Offer`` directly, and are also reachable through their parent via
    :meth:`SDNode.get_path`.
    """
    out: List[SDNode] = []
    if depth > 10:
        return out

    if isinstance(parsed, list):
        for index, item in enumerate(parsed):
            out.extend(_flatten_jsonld(item, block_index, selector,
                                       f"{path}[{index}]", parent_prop, depth + 1))
        return out

    if not isinstance(parsed, dict):
        return out

    if "@graph" in parsed:
        graph = parsed["@graph"]
        out.extend(_flatten_jsonld(graph, block_index, selector,
                                   f"{path}.@graph", parent_prop, depth + 1))
        # A container carrying both @graph and its own @type is legal; keep both.
        if "@type" not in parsed:
            return out

    types = _normalise_types(parsed.get("@type"))
    props = {k: v for k, v in parsed.items() if not k.startswith("@")}
    for key in ("@id", "@context"):
        if key in parsed:
            props[key] = parsed[key]

    if types or props:
        out.append(SDNode(
            types=types,
            props=props,
            syntax="json-ld",
            node_id=str(parsed.get("@id") or ""),
            block_index=block_index,
            json_path=path,
            selector=selector,
            parent_prop=parent_prop,
        ))

    for key in sorted(parsed):
        if key.startswith("@"):
            continue
        value = parsed[key]
        if isinstance(value, (dict, list)):
            out.extend(_flatten_jsonld(value, block_index, selector,
                                       f"{path}.{key}", key, depth + 1))
    return out


def _normalise_types(raw: Any) -> Tuple[str, ...]:
    """Normalise ``@type`` to a tuple of bare type names.

    Accepts a string, a list, and fully-qualified forms (``https://schema.org/Product``,
    ``schema:Product``), because all three appear in real markup.
    """
    if raw is None:
        return ()
    values = raw if isinstance(raw, list) else [raw]
    out: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name:
            continue
        if "/" in name:
            name = name.rstrip("/").rsplit("/", 1)[-1]
        if ":" in name and not name.startswith(("http:", "https:")):
            name = name.rsplit(":", 1)[-1]
        name = name.split("#")[-1].strip()
        if name and name not in out:
            out.append(name)
    return tuple(out)


def _extract_microdata(document: Any) -> List[SDNode]:
    """Extract Microdata ``itemscope``/``itemtype``/``itemprop`` trees."""
    out: List[SDNode] = []
    scopes = document.select("[itemscope]")
    for index, scope in enumerate(scopes):
        try:
            itemtype = (scope.get("itemtype") or "")
        except (AttributeError, TypeError):
            continue
        types = _normalise_types(itemtype if isinstance(itemtype, str)
                                 else " ".join(itemtype))
        if not types:
            continue
        props: Dict[str, Any] = {}
        for prop_node in scope.select("[itemprop]"):
            # Properties belonging to a nested itemscope are that node's, not ours.
            owner = _nearest_scope(prop_node)
            if owner is not scope:
                continue
            names = prop_node.get("itemprop") or ""
            names = names if isinstance(names, list) else str(names).split()
            value = _microdata_value(prop_node, document)
            for name in names:
                if name in props:
                    existing = props[name]
                    props[name] = (existing if isinstance(existing, list)
                                   else [existing]) + [value]
                else:
                    props[name] = value
        from .dom import selector_for
        out.append(SDNode(
            types=types, props=props, syntax="microdata",
            node_id=str(scope.get("itemid") or ""),
            block_index=1000 + index, json_path=f"$[{index}]",
            selector=selector_for(scope),
        ))
    return out


def _nearest_scope(node: Any) -> Any:
    current = getattr(node, "parent", None)
    depth = 0
    while current is not None and depth < 30:
        try:
            if current.has_attr("itemscope"):
                return current
        except (AttributeError, TypeError):
            pass
        current = getattr(current, "parent", None)
        depth += 1
    return None


def _microdata_value(node: Any, document: Any) -> Any:
    """The value of an ``itemprop`` node: nested scope, attribute, or text."""
    try:
        if node.has_attr("itemscope"):
            nested = _extract_microdata_single(node, document)
            if nested is not None:
                return nested
        attribute = _VALUE_ATTRIBUTES.get(node.name)
        if attribute and node.get(attribute) is not None:
            return str(node.get(attribute)).strip()
        if node.get("content") is not None:
            return str(node.get("content")).strip()
    except (AttributeError, TypeError):
        pass
    return textnorm.normalise_text(document.visible_text(node))


def _extract_microdata_single(scope: Any, document: Any) -> Optional[Dict[str, Any]]:
    try:
        itemtype = scope.get("itemtype") or ""
    except (AttributeError, TypeError):
        return None
    types = _normalise_types(itemtype if isinstance(itemtype, str) else " ".join(itemtype))
    props: Dict[str, Any] = {}
    for prop_node in scope.select("[itemprop]"):
        if _nearest_scope(prop_node) is not scope:
            continue
        names = prop_node.get("itemprop") or ""
        names = names if isinstance(names, list) else str(names).split()
        value = _microdata_value(prop_node, document)
        for name in names:
            props[name] = value
    if not types and not props:
        return None
    result: Dict[str, Any] = dict(props)
    if types:
        result["@type"] = list(types)
    return result


def _extract_rdfa(document: Any) -> List[SDNode]:
    """Extract RDFa-lite ``typeof``/``property`` pairs.

    Deliberately the lite subset -- full RDFa needs a real graph engine, and when
    ``rdflib``/``extruct`` are installed that path is used instead. The lite subset covers
    what commercial pages actually publish.
    """
    out: List[SDNode] = []
    from .dom import selector_for
    for index, scope in enumerate(document.select("[typeof]")):
        try:
            types = _normalise_types(scope.get("typeof") or "")
        except (AttributeError, TypeError):
            continue
        if not types:
            continue
        props: Dict[str, Any] = {}
        for prop_node in scope.select("[property]"):
            nearest = _nearest_typeof(prop_node)
            if nearest is not scope:
                continue
            names = str(prop_node.get("property") or "").split()
            attribute = _VALUE_ATTRIBUTES.get(prop_node.name)
            if prop_node.get("content") is not None:
                value: Any = str(prop_node.get("content")).strip()
            elif attribute and prop_node.get(attribute) is not None:
                value = str(prop_node.get(attribute)).strip()
            else:
                value = textnorm.normalise_text(document.visible_text(prop_node))
            for name in names:
                props[name.rsplit(":", 1)[-1]] = value
        out.append(SDNode(
            types=types, props=props, syntax="rdfa",
            node_id=str(scope.get("resource") or scope.get("about") or ""),
            block_index=2000 + index, json_path=f"$[{index}]",
            selector=selector_for(scope),
        ))
    return out


def _nearest_typeof(node: Any) -> Any:
    current = getattr(node, "parent", None)
    depth = 0
    while current is not None and depth < 30:
        try:
            if current.has_attr("typeof"):
                return current
        except (AttributeError, TypeError):
            pass
        current = getattr(current, "parent", None)
        depth += 1
    return None


_VOCAB_PREFIX_RE = re.compile(r"^(?:https?://schema\.org/|schema:)", re.IGNORECASE)


def _compact_property(name: str) -> str:
    """``https://schema.org/itemListElement`` -> ``itemListElement``, as checks name it."""
    return _VOCAB_PREFIX_RE.sub("", name)


def _unwrap_expanded(value: Any) -> Any:
    """Undo JSON-LD expansion: ``[{"@value": "1"}]`` -> ``"1"``, ``[{"@id": x}]`` -> ``x``."""
    if isinstance(value, list):
        items = [_unwrap_expanded(v) for v in value]
        return items[0] if len(items) == 1 else items
    if isinstance(value, dict):
        if "@value" in value:
            return value["@value"]
        if set(value) == {"@id"}:
            return value["@id"]
        return {_compact_property(k): _unwrap_expanded(v) for k, v in value.items()}
    return value


def _augment_with_extruct(document: Any, base_url: str,
                          result: SDExtraction) -> bool:
    """Merge in anything ``extruct`` found that the built-in path missed.

    Wrapped whole: an optional dependency failing must never fail an audit. If extruct
    raises on this page's markup, the built-in extraction still stands.
    """
    try:
        import extruct  # type: ignore
    except Exception:
        return False
    try:
        data = extruct.extract(
            document.html, base_url=base_url or None,
            syntaxes=["microdata", "rdfa", "opengraph"], uniform=True,
        )
    except Exception:
        return False

    known = {(n.syntax, tuple(n.types), json.dumps(_jsonable(n.props), sort_keys=True))
             for n in result.nodes}
    # extruct augments; it does not overrule. Where the built-in pass already typed an
    # entity of this syntax, its node stands -- extruct's expanded form of the same entity
    # would otherwise duplicate it with differently shaped properties.
    covered = {(n.syntax, tuple(n.types)) for n in result.nodes}
    added = False
    for syntax in ("microdata", "rdfa"):
        for index, item in enumerate(data.get(syntax) or []):
            if not isinstance(item, dict):
                continue
            types = _normalise_types(item.get("@type"))
            if not types or (syntax, types) in covered:
                continue
            props = {_compact_property(k): _unwrap_expanded(v)
                     for k, v in item.items() if not k.startswith("@")}
            key = (syntax, types, json.dumps(_jsonable(props), sort_keys=True))
            if key in known:
                continue
            node = SDNode(types=types, props=props, syntax=syntax,
                          node_id=str(item.get("@id") or ""),
                          block_index=3000 + index, json_path=f"$[{index}]")
            result.nodes.append(node)
            known.add(key)
            added = True
    return added


def _describe_json_error(text: str, exc: ValueError) -> str:
    """Turn a JSON error into a line/column plus the offending fragment.

    "Invalid JSON-LD" is not actionable. "line 4 column 28, near ``,}``" is. The most
    common real causes -- a trailing comma, an unescaped quote from CMS templating, a raw
    newline inside a string -- are all identified by position.
    """
    line = getattr(exc, "lineno", None)
    column = getattr(exc, "colno", None)
    position = getattr(exc, "pos", None)
    parts: List[str] = []
    if line and column:
        parts.append(f"line {line} column {column}")
    if position is not None and 0 <= position <= len(text):
        start = max(0, position - 30)
        end = min(len(text), position + 30)
        fragment = textnorm.collapse_space(text[start:end])
        parts.append(f"near {fragment!r}")
    return ", ".join(parts)


def _dict_get_ci(data: Dict[str, Any], key: str) -> Any:
    if key in data:
        return data[key]
    lowered = key.lower()
    for existing, value in data.items():
        if str(existing).lower() == lowered:
            return value
    return None


def _flatten_scalars(value: Any, depth: int = 0) -> List[Any]:
    """Flatten a property value to scalar leaves, unwrapping schema.org value objects."""
    if depth > 6 or value is None:
        return []
    if isinstance(value, (str, int, float, bool)):
        return [value]
    if isinstance(value, list):
        out: List[Any] = []
        for item in value:
            out.extend(_flatten_scalars(item, depth + 1))
        return out
    if isinstance(value, SDNode):
        return _flatten_scalars(value.props, depth + 1)
    if isinstance(value, dict):
        # {"@value": "x"} and {"name": "x"} are the usual wrappers.
        for key in ("@value", "name", "value", "url", "@id"):
            if key in value:
                return _flatten_scalars(value[key], depth + 1)
        out = []
        for item in value.values():
            out.extend(_flatten_scalars(item, depth + 1))
        return out
    return []


def scalar(value: Any) -> str:
    """First scalar leaf of a property value, as a string. ``""`` when there is none."""
    leaves = _flatten_scalars(value)
    return str(leaves[0]).strip() if leaves else ""


def node_types(value: Any) -> Tuple[str, ...]:
    """Bare type names of an entity, whether flattened (:class:`SDNode`) or raw JSON."""
    if isinstance(value, SDNode):
        return value.types
    if isinstance(value, dict):
        return _normalise_types(value.get("@type"))
    return ()


def all_scalars(value: Any) -> List[str]:
    return [str(v).strip() for v in _flatten_scalars(value) if str(v).strip()]


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "..."
    if isinstance(value, SDNode):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def node_id_graph(nodes: Sequence[SDNode]) -> Dict[str, Any]:
    """Summarise ``@id`` usage and cross-references.

    Feeds ``G2.SD.ENTITY_GRAPH_UNLINKED``: a site publishing one ``Organization`` node
    with a stable ``@id``, referenced by ``publisher``/``brand``/``provider`` from every
    other entity, is one connected entity. A site publishing an unlinked island per page
    is many candidate entities that a consumer has to try to merge.
    """
    ids = {n.node_id for n in nodes if n.node_id}
    references: Dict[str, List[str]] = {}
    for node in nodes:
        for prop in ("publisher", "brand", "provider", "author", "parentOrganization",
                     "isPartOf", "mainEntityOfPage", "about", "seller", "manufacturer"):
            value = node.get(prop)
            if value is None:
                continue
            for target in _collect_ids(value):
                references.setdefault(target, []).append(
                    f"{node.primary_type}.{prop}")
    return {
        "declared_ids": sorted(ids),
        "referenced_ids": sorted(references),
        "resolved_references": sorted(set(references) & ids),
        "dangling_references": sorted(set(references) - ids),
        "reference_sources": {k: sorted(set(v)) for k, v in sorted(references.items())},
    }


def _collect_ids(value: Any, depth: int = 0) -> List[str]:
    if depth > 6:
        return []
    if isinstance(value, str):
        return [value] if value.startswith(("http", "#", "/")) else []
    if isinstance(value, SDNode):
        return [value.node_id] if value.node_id else _collect_ids(value.props, depth + 1)
    if isinstance(value, dict):
        if "@id" in value and isinstance(value["@id"], str):
            return [value["@id"]]
        out: List[str] = []
        for item in value.values():
            out.extend(_collect_ids(item, depth + 1))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_collect_ids(item, depth + 1))
        return out
    return []
