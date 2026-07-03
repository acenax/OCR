"""Per-customer learned layout ('เรียนรู้ตำแหน่ง').

New customers get a template auto-generated the first time a PO is read; old
customers reuse it. It can be re-learned any time from the UI if data looks wrong.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.strip()) or "customer"


def path_for(customer: str) -> Path:
    return config.TEMPLATE_DIR / f"{_safe(customer)}.json"


def load(customer: str) -> dict | None:
    p = path_for(customer)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def exists(customer: str) -> bool:
    return path_for(customer).exists()


def save_profile(customer: str, profile: dict):
    """Save a full customer profile (from the manual box-teaching tool)."""
    profile = dict(profile)
    profile["customer"] = customer
    config.TEMPLATE_DIR.mkdir(exist_ok=True)
    path_for(customer).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def save(customer: str, anchors_frac: dict, header_bottom: float, extra: dict | None = None):
    data = {
        "customer": customer,
        "anchors": anchors_frac,
        "header_bottom": header_bottom,
    }
    if extra:
        data.update(extra)
    config.TEMPLATE_DIR.mkdir(exist_ok=True)
    path_for(customer).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
