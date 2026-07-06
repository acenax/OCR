"""Validation helpers for PO OCR results.

This module is intentionally UI-independent so it can be reused by the
Process tab, future dashboard, export pipeline, or tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import PODocument, POLine


@dataclass
class ValidationIssue:
    level: str        # "critical" | "review"
    row: int          # 1-based row number, 0 for document-level issue
    field: str
    message: str

    def as_text(self) -> str:
        prefix = f"แถว {self.row}: " if self.row else ""
        return f"{prefix}{self.message}"


def _is_blank(value: object) -> bool:
    return str(value or "").strip() == ""


def _money(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip() or 0)
    except Exception:
        return 0.0


def validate_line(
    line: POLine,
    row_no: int,
    *,
    fuzzy_review_threshold: float = 90.0,
    require_stock_group: bool = True,
) -> list[ValidationIssue]:
    """Return issues found in a single PO line.

    Critical = should not export.
    Review = can export only after a user confirms/reviews.
    """
    issues: list[ValidationIssue] = []

    if _is_blank(line.tmc_code):
        issues.append(ValidationIssue("critical", row_no, "tmc_code", "ยังไม่มี tmc_code"))

    if require_stock_group and _is_blank(line.stock_group_code):
        issues.append(ValidationIssue("critical", row_no, "stock_group_code", "ยังไม่มี stock_group_code"))

    if _money(line.qty) <= 0:
        issues.append(ValidationIssue("critical", row_no, "qty", "จำนวนเป็น 0 หรือว่าง"))

    if _money(line.price) <= 0:
        issues.append(ValidationIssue("critical", row_no, "price", "ราคาเป็น 0 หรือว่าง"))

    status = str(getattr(line, "match_status", "") or "").strip().lower()
    score = float(getattr(line, "match_score", 0) or 0)

    # Fuzzy/noisy match is not always wrong, but users should review it.
    if status == "fuzzy" or (score and score < fuzzy_review_threshold and status not in {"manual", "matched"}):
        issues.append(
            ValidationIssue(
                "review",
                row_no,
                "match_score",
                f"จับคู่สินค้าแบบไม่มั่นใจ ({status or 'unknown'} {score:.0f})",
            )
        )

    # Compare OCR amount, when available, against qty * price.
    amount = _money(getattr(line, "amount", 0))
    calc = round(_money(line.qty) * _money(line.price), 2)
    if amount > 0 and calc > 0:
        tolerance = max(1.0, abs(amount) * 0.02)
        if abs(amount - calc) > tolerance:
            issues.append(
                ValidationIssue(
                    "review",
                    row_no,
                    "amount",
                    f"ยอดต่อแถวไม่ตรง: OCR {amount:,.2f} / คำนวณ {calc:,.2f}",
                )
            )

    return issues


def validate_document(
    doc: PODocument,
    *,
    fuzzy_review_threshold: float = 90.0,
    require_stock_group: bool = True,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if _is_blank(getattr(doc, "po_no", "")):
        issues.append(ValidationIssue("critical", 0, "po_no", "ยังไม่มีเลขที่ PO"))

    if not getattr(doc, "lines", None):
        issues.append(ValidationIssue("critical", 0, "lines", "ไม่พบรายการสินค้าในเอกสาร"))
        return issues

    for i, line in enumerate(doc.lines, 1):
        issues.extend(
            validate_line(
                line,
                i,
                fuzzy_review_threshold=fuzzy_review_threshold,
                require_stock_group=require_stock_group,
            )
        )

    total = _money(getattr(doc, "total", 0))
    vat = _money(getattr(doc, "vat", 0))
    grand = _money(getattr(doc, "grand_total", 0))
    calc_total = round(sum(_money(l.qty) * _money(l.price) for l in doc.lines), 2)

    if total > 0 and calc_total > 0:
        tolerance = max(1.0, abs(total) * 0.02)
        if abs(total - calc_total) > tolerance:
            issues.append(
                ValidationIssue(
                    "review",
                    0,
                    "total",
                    f"ยอดรวมไม่ตรง: Header {total:,.2f} / คำนวณจากรายการ {calc_total:,.2f}",
                )
            )

    if grand > 0 and (total > 0 or vat > 0):
        calc_grand = round(total + vat, 2)
        tolerance = max(1.0, abs(grand) * 0.02)
        if abs(grand - calc_grand) > tolerance:
            issues.append(
                ValidationIssue(
                    "review",
                    0,
                    "grand_total",
                    f"ยอดรวมทั้งสิ้นไม่ตรง: Header {grand:,.2f} / รวม+VAT {calc_grand:,.2f}",
                )
            )

    return issues


def split_issues(issues: Iterable[ValidationIssue]) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    critical: list[ValidationIssue] = []
    review: list[ValidationIssue] = []
    for issue in issues:
        if issue.level == "critical":
            critical.append(issue)
        else:
            review.append(issue)
    return critical, review


def summarize_issues(issues: Iterable[ValidationIssue], *, limit: int = 8) -> str:
    items = list(issues)
    if not items:
        return "ผ่านการตรวจสอบ พร้อม Export"
    lines = [issue.as_text() for issue in items[:limit]]
    if len(items) > limit:
        lines.append(f"...และอีก {len(items) - limit} รายการ")
    return "\n".join(lines)
