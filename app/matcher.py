"""Match a PO product name to a tmc_code using each customer's Product Details.xlsx.

The PO 'DESCRIPTION' column holds the real part number (e.g. DNMG150612-HN-GK1120).
Product Details.xlsx maps customer product names -> tmc_code. Because the names come
from noisy OCR we match with rapidfuzz rather than requiring an exact string.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd
from rapidfuzz import process, fuzz


def normalize(s: str) -> str:
    """Uppercase and drop spaces / punctuation so OCR spacing noise doesn't hurt.

    Keeps Latin A-Z, digits AND Thai characters (\\u0E00-\\u0E7F) — Thai product
    descriptions must survive normalization or they can never match.
    """
    return re.sub(r"[^A-Z0-9฀-๿]", "", str(s).upper())


@dataclass
class MatchResult:
    tmc_code: str
    score: float
    matched_name: str
    status: str  # matched | fuzzy | no_match


class ProductMatcher:
    def __init__(self, threshold: float = 72, strong: float = 90):
        self.threshold = threshold
        self.strong = strong
        # normalized_name -> (tmc_code, original_name)
        self._index: dict[str, tuple[str, str]] = {}
        self._choices: list[str] = []
        self.unit_prices: dict[str, float] = {}   # tmc_code -> price (reference)
        self.loaded_from: str = ""

    @classmethod
    def from_product_file(cls, path: str, threshold=72, strong=90) -> "ProductMatcher":
        m = cls(threshold, strong)
        m.load(path)
        return m

    def load(self, path: str):
        p = Path(path)
        self._index.clear()
        self._choices.clear()
        if not p.exists():
            self.loaded_from = ""
            return
        df = pd.read_excel(p)
        self.loaded_from = str(p)
        # locate the tmc_code column
        tmc_col = None
        for c in df.columns:
            if "tmc" in str(c).lower():
                tmc_col = c
                break
        if tmc_col is None:
            return
        tmc_idx = list(df.columns).index(tmc_col)
        # name columns = every text column before tmc_code (customer part names)
        name_cols = list(df.columns[:tmc_idx]) or list(df.columns[tmc_idx + 1:])
        price_col = None
        for c in df.columns:
            cl = str(c).lower()
            if "ราคา" in str(c) or "price" in cl:
                price_col = c
                break

        for _, row in df.iterrows():
            tmc = str(row[tmc_col]).strip()
            if not tmc or tmc.lower() == "nan":
                continue
            if price_col is not None:
                try:
                    self.unit_prices[tmc] = float(row[price_col])
                except (ValueError, TypeError):
                    pass
            for nc in name_cols:
                name = row[nc]
                if pd.isna(name):
                    continue
                key = normalize(name)
                if len(key) < 3:
                    continue
                # first name wins for a given key
                self._index.setdefault(key, (tmc, str(name).strip()))
        self._choices = list(self._index.keys())

    def match(self, description: str) -> MatchResult:
        key = normalize(description)
        if not key or not self._choices:
            return MatchResult("", 0.0, "", "no_match")
        # exact normalized hit
        if key in self._index:
            tmc, name = self._index[key]
            return MatchResult(tmc, 100.0, name, "matched")
        best = process.extractOne(key, self._choices, scorer=fuzz.WRatio)
        if not best:
            return MatchResult("", 0.0, "", "no_match")
        choice, score, _ = best
        tmc, name = self._index[choice]
        if score >= self.strong:
            return MatchResult(tmc, score, name, "matched")
        if score >= self.threshold:
            return MatchResult(tmc, score, name, "fuzzy")
        return MatchResult("", score, name, "no_match")
