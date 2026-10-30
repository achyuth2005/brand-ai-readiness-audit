"""Marketplace-root discovery and shared-library path setup.

Why ``lib/`` sits at the marketplace root rather than inside each skill
----------------------------------------------------------------------
Six specialist skills need the same crawler, the same DOM extraction, and the same
severity model. Vendoring a copy into each skill folder guarantees that six copies drift
apart, and the first thing to drift would be the safety controls -- which is exactly
where drift is unacceptable.

The brief requires each *skill folder* to satisfy the agentskills.io spec. That is a
statement about ``SKILL.md`` validity, which shared code does not affect: every skill
folder here has valid frontmatter and is independently readable and runnable. The shared
library is an implementation detail of the marketplace, the same way ``references/`` is.

Every analyzer therefore starts with the same three lines::

    from auditkit.bootstrap import setup
    setup()
    from auditkit import ledger, finding, dom

:func:`setup` walks up from the calling file until it finds ``marketplace.json``, which
makes the whole package relocatable: the audit runs from a clone, an unzipped submission,
or a temp directory with no installation step and no environment variables.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

MARKER = "marketplace.json"
_root_cache: Optional[Path] = None


def find_root(start: Optional[Path] = None) -> Path:
    """Walk up from ``start`` to the directory containing ``marketplace.json``."""
    global _root_cache
    if start is None and _root_cache is not None:
        return _root_cache

    current = Path(start or __file__).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / MARKER).exists():
            if start is None:
                _root_cache = candidate
            return candidate
    raise RuntimeError(
        f"could not locate the marketplace root: no {MARKER} found in {current} or any "
        f"parent directory. Run scripts from inside an intact marketplace package."
    )


def setup(start: Optional[Path] = None) -> Path:
    """Put ``lib/`` on ``sys.path`` and return the marketplace root. Idempotent."""
    root = find_root(start)
    lib = root / "lib"
    lib_str = str(lib)
    if lib.is_dir() and lib_str not in sys.path:
        sys.path.insert(0, lib_str)
    return root


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``config/thresholds.json``.

    Every tunable default in the package lives there with a rationale, so that no
    detector carries a bare magic number and a reviewer can disagree with a threshold
    without reading any code.
    """
    root = find_root()
    config_path = Path(path) if path else root / "config" / "thresholds.json"
    if not config_path.exists():
        raise RuntimeError(f"threshold configuration not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def threshold(config: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """Read a threshold by dotted path, unwrapping the ``{value, rationale}`` shape.

    ``threshold(cfg, "render.thin_text_chars")`` returns the number, while the rationale
    stays available for the report and for a reader of the config file.
    """
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    if isinstance(node, dict):
        if "value" in node:
            return node["value"]
        return default
    return node


def rationale(config: Dict[str, Any], dotted: str) -> str:
    """Read a threshold's stated rationale, for inclusion in evidence and references."""
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return ""
        node = node[part]
    if isinstance(node, dict):
        return str(node.get("rationale", ""))
    return ""


def load_manifest() -> Dict[str, Any]:
    root = find_root()
    with (root / MARKER).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def schema_path(name: str) -> Path:
    return find_root() / "schema" / name


def load_schema(name: str) -> Dict[str, Any]:
    with schema_path(name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def skill_dir(skill_id: str) -> Path:
    return find_root() / "skills" / skill_id


def relative(path: Path) -> str:
    """Path relative to the marketplace root, for portable references in the report."""
    try:
        return str(Path(path).resolve().relative_to(find_root()))
    except (ValueError, RuntimeError):
        return str(path)
