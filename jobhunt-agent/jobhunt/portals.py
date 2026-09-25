"""
Portal registry loader.

Reads portals.yaml — the catalogue of every European/remote AI job portal the
tool knows about and how each one connects (auto / rss / browser / browser_login
/ email / manual). Used to list coverage and to know how a given URL should be
handled at apply time.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_PATH = Path(__file__).with_name("portals.yaml")


def load() -> list[dict]:
    if not _PATH.exists():
        return []
    return (yaml.safe_load(_PATH.read_text()) or {}).get("portals", [])


def by_path() -> dict[str, list[str]]:
    """Group portal names by apply_path, for a quick coverage summary."""
    groups: dict[str, list[str]] = {}
    for p in load():
        groups.setdefault(p.get("apply_path", "manual"), []).append(p["name"])
    return groups


def summary() -> str:
    groups = by_path()
    order = ["auto", "rss", "browser", "browser_login", "email", "manual"]
    lines = [f"{len(load())} portals known:"]
    for k in order:
        names = groups.get(k, [])
        if names:
            lines.append(f"  {k:14} ({len(names):2}): {', '.join(names)}")
    return "\n".join(lines)
