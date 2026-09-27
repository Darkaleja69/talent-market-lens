"""Data-contract validity rules for the measured job-offer fields.

The rules classify a field value into exactly one of three statuses:

- ``VALID``   -> the value is present and complies with the contract;
- ``ABSENT``  -> the offer genuinely does not publish the data (``None``,
  blank text or an empty collection); it is not a contract violation;
- ``INVALID`` -> the value is present but does not comply (RF-5).

Structural validation (URLs, dates, numeric ids, text) is reused from
``coherence.py`` instead of being duplicated (plan section 4.1). The rules are
documented in ``docs/DATA_SOURCES.md`` and deliberately do **not** impose a
minimum text length.
"""
from __future__ import annotations

import enum
import math
import re
import unicodedata
from dataclasses import dataclass

from coherence import _v_date, _v_id, _v_num, _v_rating, _v_text


class FieldStatus(str, enum.Enum):
    """Result of classifying a field value against the contract."""

    VALID = "valid"
    ABSENT = "absent"
    INVALID = "invalid"


VALID = FieldStatus.VALID
ABSENT = FieldStatus.ABSENT
INVALID = FieldStatus.INVALID

# Canonical work modes accepted by the contract (case-insensitive).
WORK_MODES: tuple[str, ...] = ("Remote", "Hybrid", "On-site")

# Real origin values (Spanish from LinkedIn/InfoJobs, plus English) mapped to
# the canonical work mode. Keys are already accent-folded and lowercased.
_WORK_MODE_SYNONYMS: dict[str, str] = {
    "remote": "Remote",
    "remoto": "Remote",
    "en remoto": "Remote",
    "a distancia": "Remote",
    "teletrabajo": "Remote",
    "hybrid": "Hybrid",
    "hibrido": "Hybrid",
    "on-site": "On-site",
    "onsite": "On-site",
    "presencial": "On-site",
}

_SKILL_SEPARATORS = re.compile(r"[|,;\n]")


def _fold(value: str) -> str:
    """Lowercase and strip accents, collapsing internal whitespace."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(without_accents.lower().split())


def normalize_work_mode(value: object) -> str | None:
    """Return the canonical work mode (`Remote`/`Hybrid`/`On-site`) or None.

    Tolerant to case and accents; accepts the real Spanish and English
    variants emitted by the scrapers.
    """
    if not isinstance(value, str):
        return None
    return _WORK_MODE_SYNONYMS.get(_fold(value))


def _as_text(value: object) -> str:
    """Flatten a scalar or a collection of scalars into a single string."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _v_text_field(value: object) -> bool:
    return _v_text(_as_text(value))


def _v_company(value: object) -> bool:
    """Company name: text (not a URL) and not a rating like '4,5'."""
    text = _as_text(value)
    return _v_text(text) and not _v_rating(text)


def _v_location(value: object) -> bool:
    return _v_text(_as_text(value))


def _v_id_field(value: object) -> bool:
    return _v_id(_as_text(value))


def _v_date_field(value: object) -> bool:
    return _v_date(_as_text(value))


def _v_salary(value: object) -> bool:
    """Valid when there is a numeric range or salary text with an amount."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not (isinstance(value, float) and math.isnan(value))
    text = _as_text(value)
    if not text:
        return False
    if _v_num(text):
        return True
    # Salary text must carry an amount (a digit or a currency symbol).
    return any(ch.isdigit() for ch in text) or any(sym in text for sym in "€$£")


def _v_work_mode(value: object) -> bool:
    return normalize_work_mode(value) is not None


def _v_skills(value: object) -> bool:
    """Valid when at least one real skill is present (list or string)."""
    if isinstance(value, str):
        tokens = _SKILL_SEPARATORS.split(value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        tokens = [str(item) for item in value]
    else:
        return False
    return any(token.strip() for token in tokens)


@dataclass(frozen=True)
class FieldRule:
    """Contract rule for one canonical field."""

    field: str
    required: bool
    validator: object
    description: str


_RULES: dict[str, FieldRule] = {
    "id": FieldRule(
        "id", False, _v_id_field, "Unique offer identifier in its source format."
    ),
    "title": FieldRule(
        "title", True, _v_text_field, "Job title: non-empty text, not a URL."
    ),
    "company": FieldRule(
        "company",
        True,
        _v_company,
        "Company name: non-empty text, not a URL and not a rating.",
    ),
    "description": FieldRule(
        "description", True, _v_text_field, "Description: non-empty text, not a URL."
    ),
    "salary": FieldRule(
        "salary",
        False,
        _v_salary,
        "Numeric salary or salary text carrying an amount.",
    ),
    "skills": FieldRule(
        "skills",
        False,
        _v_skills,
        "At least one real skill (list, or pipe/comma separated text).",
    ),
    "work_mode": FieldRule(
        "work_mode",
        False,
        _v_work_mode,
        "One of Remote / Hybrid / On-site (case-insensitive).",
    ),
    "location": FieldRule(
        "location",
        False,
        _v_location,
        "Location text (city/region/country), not a URL.",
    ),
    "posted_date": FieldRule(
        "posted_date", False, _v_date_field, "Parseable posting date."
    ),
}

MEASURED_FIELDS: tuple[str, ...] = tuple(_RULES)
REQUIRED_FIELDS: frozenset[str] = frozenset(
    field for field, rule in _RULES.items() if rule.required
)
OPTIONAL_FIELDS: frozenset[str] = frozenset(_RULES) - REQUIRED_FIELDS


def rules() -> dict[str, FieldRule]:
    """Return the field -> rule table."""
    return _RULES


def field_rule(field: str) -> FieldRule:
    """Return the rule of a canonical field; raises KeyError if unknown."""
    return _RULES[field]


def is_required(field: str) -> bool:
    """Return whether the field is mandatory in the data contract."""
    return field_rule(field).required


def _is_absent(value: object) -> bool:
    """Return True for genuinely missing values (not contract violations)."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, frozenset)):
        return len(value) == 0
    try:
        import pandas as pd

        if value is pd.NA or value is pd.NaT:
            return True
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def classify(field: str, value: object) -> FieldStatus:
    """Classify a value as VALID, ABSENT or INVALID for a given field."""
    rule = field_rule(field)
    if _is_absent(value):
        return ABSENT
    try:
        ok = bool(rule.validator(value))
    except Exception:
        ok = False
    return VALID if ok else INVALID


def coherence_validators() -> dict[str, object]:
    """Expose the reused coherence validators (for traceability/tests)."""
    return {
        "text": _v_text,
        "date": _v_date,
        "num": _v_num,
        "id": _v_id,
    }
