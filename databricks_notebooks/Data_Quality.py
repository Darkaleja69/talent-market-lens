"""Data-quality contract: rule registry, predicates and Delta DDL (spec 012 T-05).

This module is the single definition of the quality rules that the bronze
separation, the Delta constraints and the T-08 profile queries consume. It is
pure Python + PySpark: importing it creates nothing and executes no DDL.

Layout:

* :data:`COLUMN_MAP` fixes the **table-column map per layer** before any DDL:
  the names do not match between layers (``salary_min_annual`` in silver vs
  ``SalaryMinAnnual_EUR`` in gold), so they are never assumed.
* :data:`RULES` is the registry (frozen dataclasses, not a Delta table). Every
  rule has a stable ``rule_id``, its layers, its action and the evidence that
  supports it. Existing rules are *referenced*, not reimplemented:
  ``missing_key``/``missing_scraped_at``/``company_name_rating`` live in
  :mod:`Upsert_Job_Offers` (the bronze separation) and the posting-date flag
  lives in :mod:`Prepare_Gold`.
* :func:`sql_expression` is the one definition of each predicate. The PySpark
  predicates (:func:`predicate`, :func:`job_id_required`,
  :func:`salary_range_order`, :func:`posted_date_contract`) and the Delta DDL
  are built from that same string, so separation and constraint cannot drift.
* :func:`add_constraint_sql`, :func:`drop_constraint_sql`,
  :func:`ddl_statements`, :func:`ddl_script`, :func:`profile_sql` and
  :func:`profile_script` generate the versioned statements for T-07 and the
  single SQL session of T-08. Nothing here is executed by the pipeline.

Null semantics: a CHECK constraint is *true for each input row*, so a row whose
expression evaluates to NULL is rejected by Delta (measured locally with Delta
4.4: ``salary_min_annual <= salary_max_annual`` rejected rows with both amounts
NULL). Every expression therefore allows the missing values explicitly with
``IS NULL OR``. A violation is never a silent drop: it fails the write, which
is the last defence against a regression.
"""
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Sequence, Tuple

from pyspark.sql import Column
from pyspark.sql import functions as F

import Upsert_Job_Offers as upsert

#: Layers of the lakehouse, from the raw bronze to the analytical gold.
BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"

#: Action of a rule: where a non-conforming row stops or what happens to it.
QUARANTINE = "quarantine"   # the row never enters the layer's valid table
MARK = "mark"               # the row is kept and flagged (already implemented)
CONSTRAINT = "constraint"   # a Delta CHECK rejects the write

#: Rule lifecycle: already implemented, enforceable now, or waiting for a measurement.
EXISTING = "existing"
ACTIVE = "active"
DEFERRED = "deferred"

#: Silver tables that carry the offer contract (one per source, same columns).
SILVER_OFFER_TABLES: Tuple[str, ...] = (
    "offers_indeed",
    "offers_infojobs",
    "offers_linkedin",
    "offers_multi_site",
)

#: Gold fact table with one row per offer.
GOLD_FACT_TABLE = "fact_offers"

#: Posting-date bounds of the existing flag (``Prepare_Gold.add_posted_date_flags``).
POSTED_DATE_MIN_YEAR = 2026
POSTED_DATE_MAX_YEAR_EXCLUSIVE = 2090

#: Bronze keys per source, taken from the module that performs the separation.
BRONZE_KEYS: Mapping[str, Tuple[str, ...]] = {
    source: tuple(spec.keys) for source, spec in upsert.SOURCE_SPECS.items()
}

#: Explicit rule -> layer -> table -> columns map (fixed before generating DDL).
#: Bronze entries document the separation rule; only silver and gold are DDL
#: targets, because bronze already routes these rows to quarantine.
COLUMN_MAP: Mapping[str, Mapping[str, Mapping[str, Tuple[str, ...]]]] = {
    "job_id_required": {
        BRONZE: BRONZE_KEYS,
        SILVER: {table: ("job_id",) for table in SILVER_OFFER_TABLES},
        GOLD: {GOLD_FACT_TABLE: ("job_id",)},
    },
    "salary_range_order": {
        SILVER: {
            table: ("salary_min_annual", "salary_max_annual")
            for table in SILVER_OFFER_TABLES
        },
        GOLD: {
            GOLD_FACT_TABLE: ("SalaryMinAnnual_EUR", "SalaryMaxAnnual_EUR")
        },
    },
    "posted_date_contract": {
        GOLD: {GOLD_FACT_TABLE: ("posted_date",)},
    },
    "company_name_rating": {
        BRONZE: {
            source: ("company_name",) for source in upsert.SOURCE_SPECS
        },
    },
}


@dataclass(frozen=True)
class QualityRule:
    """One entry of the quality registry (stable id + action + evidence)."""

    rule_id: str
    layers: Tuple[str, ...]
    action: str
    description: str
    status: str = ACTIVE
    reason: str = ""
    evidence: str = ""
    note: str = ""


#: Rules that may become a Delta CHECK; the order fixes the DDL order.
ENFORCEABLE_RULE_IDS: Tuple[str, ...] = (
    "job_id_required",
    "salary_range_order",
)

#: Rules measured in T-08, including the deferred date rule.
PROFILE_RULE_IDS: Tuple[str, ...] = (
    "job_id_required",
    "salary_range_order",
    "posted_date_contract",
)

#: Registry. Existing rules point at their implementation; no logic is copied.
RULES: Tuple[QualityRule, ...] = (
    QualityRule(
        rule_id=upsert.MISSING_KEY,
        layers=(BRONZE,),
        action=QUARANTINE,
        description=(
            "Any source key is NULL or blank after trim(cast(... AS STRING)); "
            "the row never enters the bronze MERGE."
        ),
        status=EXISTING,
        reason=upsert.MISSING_KEY,
        evidence="databricks_notebooks/Upsert_Job_Offers.py::_missing_key_expr",
        note="Precedence: first reason of the bronze separation.",
    ),
    QualityRule(
        rule_id=upsert.MISSING_SCRAPED_AT,
        layers=(BRONZE,),
        action=QUARANTINE,
        description=(
            "The capture-time column is NULL, blank or unparseable "
            "(try_to_timestamp(...) IS NULL); the row never enters the MERGE."
        ),
        status=EXISTING,
        reason=upsert.MISSING_SCRAPED_AT,
        evidence="databricks_notebooks/Upsert_Job_Offers.py::split_quarantine",
        note="Second reason: loses against a missing key.",
    ),
    QualityRule(
        rule_id=upsert.COMPANY_NAME_RATING,
        layers=(BRONZE,),
        action=QUARANTINE,
        description=(
            "company_name is exactly a decimal rating (for example 4,0 or "
            "4.0), the unambiguous signature of a shifted parser column. A "
            "company name that merely contains digits (24/7 Retail) is valid."
        ),
        status=ACTIVE,
        reason=upsert.COMPANY_NAME_RATING,
        evidence="scrapers-pipeline/coherence.py::RATING_RE (local first defence)",
        note=(
            "Second defence in Delta; the column is checked before applying "
            "it, so a source without company_name is not quarantined by it. "
            "Third reason: loses against a missing key or capture time."
        ),
    ),
    QualityRule(
        rule_id="job_id_required",
        layers=(SILVER, GOLD),
        action=CONSTRAINT,
        description=(
            "job_id is not NULL and, as a STRING, not blank after trim. The "
            "bronze separation already quarantines the same condition per "
            "source key; silver and gold are the enforceable layers."
        ),
        status=ACTIVE,
        evidence="docs/governance/quality-inventory.md sections 2 and 7.2",
    ),
    QualityRule(
        rule_id="salary_range_order",
        layers=(SILVER, GOLD),
        action=CONSTRAINT,
        description=(
            "When both annualised amounts exist, the minimum is not greater "
            "than the maximum. NULL (one or both missing) is valid. Never "
            "applied to the original salary range, which the ELT neutralises."
        ),
        status=ACTIVE,
        evidence="Enrich_Job_Offers_Dataframes.enrich (salary_quality = invalid)",
    ),
    QualityRule(
        rule_id="posted_date_contract",
        layers=(GOLD,),
        action=MARK,
        description=(
            "The posting date is NULL or its year is within 2026..2089, the "
            "same contract as IsValidPostingDate. Today it is a flag, not a "
            "filter, and gold holds dates before 2026."
        ),
        status=DEFERRED,
        evidence="databricks_notebooks/Prepare_Gold.py::add_posted_date_flags",
        note=(
            "Constraint postponed: the T-01 statistic shows posted_date "
            "min 2018-10-13 in gold.fact_offers. T-08 profiles it and decides; "
            "no DDL is generated while it is deferred."
        ),
    ),
)

#: Registry indexed by rule id (stable lookup for code and tests).
RULES_BY_ID: Mapping[str, QualityRule] = {rule.rule_id: rule for rule in RULES}

#: Property prefix where Delta stores CHECK constraints.
CONSTRAINT_PROPERTY_PREFIX = "delta.constraints."
CONSTRAINT_NAME_PREFIX = "chk"


# ---------------------------------------------------------------------------
# Predicates: one SQL definition, consumed by both Spark and Delta
# ---------------------------------------------------------------------------

def _columns(rule_id: str, layer: str, table: str) -> Tuple[str, ...]:
    """Columns of ``rule_id`` for a table, from the explicit map."""
    try:
        return COLUMN_MAP[rule_id][layer][table]
    except KeyError:
        raise ValueError(
            f"La regla {rule_id!r} no está mapeada para {layer}.{table}. "
            "Revisa COLUMN_MAP antes de generar SQL."
        ) from None


def _quote(column: str) -> str:
    """Backtick a column so CamelCase and reserved words are safe."""
    return f"`{column}`"


def _not_blank_sql(column: str) -> str:
    """SQL: the column is neither NULL nor blank after trim."""
    quoted = _quote(column)
    return (
        f"{quoted} IS NOT NULL "
        f"AND trim(cast({quoted} AS STRING)) <> ''"
    )


def sql_expression(rule_id: str, layer: str, table: str) -> str:
    """SQL boolean expression of ``rule_id`` for one table of a layer.

    This is the single definition used by the PySpark predicates, the Delta
    CHECK generation and the profile queries. The expression is explicit about
    NULL: a CHECK must be true for every row, so missing values are allowed
    with ``IS NULL OR`` instead of relying on SQL's three-valued logic.
    """
    columns = _columns(rule_id, layer, table)
    if rule_id == "job_id_required":
        return " AND ".join(_not_blank_sql(column) for column in columns)
    if rule_id == "salary_range_order":
        low, high = (_quote(column) for column in columns)
        return f"{low} IS NULL OR {high} IS NULL OR {low} <= {high}"
    if rule_id == "posted_date_contract":
        date = _quote(columns[0])
        return (
            f"{date} IS NULL OR (year({date}) >= {POSTED_DATE_MIN_YEAR} "
            f"AND year({date}) < {POSTED_DATE_MAX_YEAR_EXCLUSIVE})"
        )
    if rule_id == upsert.COMPANY_NAME_RATING:
        name = _quote(columns[0])
        # coalesce: a NULL company_name is not a shifted rating (the profile
        # query must count rows, not SQL unknowns).
        return (
            f"coalesce(trim(cast({name} AS STRING)) "
            f"RLIKE '{upsert.RATING_PATTERN}', false)"
        )
    raise ValueError(f"Expresión desconocida para la regla {rule_id!r}.")


def predicate(rule_id: str, layer: str, table: str) -> Column:
    """PySpark column of the rule, built from the same SQL as the DDL."""
    return F.expr(sql_expression(rule_id, layer, table))


def job_id_required(column: str = "job_id") -> Column:
    """PySpark predicate: ``column`` is not NULL and not blank."""
    return F.expr(_not_blank_sql(column))


def salary_range_order(
    min_column: str = "salary_min_annual",
    max_column: str = "salary_max_annual",
) -> Column:
    """PySpark predicate: missing amounts are valid, both present must be ordered."""
    low, high = _quote(min_column), _quote(max_column)
    return F.expr(f"{low} IS NULL OR {high} IS NULL OR {low} <= {high}")


def posted_date_contract(column: str = "posted_date") -> Column:
    """PySpark predicate: NULL or year inside the 2026..2089 window."""
    date = _quote(column)
    return F.expr(
        f"{date} IS NULL OR (year({date}) >= {POSTED_DATE_MIN_YEAR} "
        f"AND year({date}) < {POSTED_DATE_MAX_YEAR_EXCLUSIVE})"
    )


# ---------------------------------------------------------------------------
# Delta DDL generation (never executed from here)
# ---------------------------------------------------------------------------

def constraint_name(table: str, rule_id: str) -> str:
    """Stable constraint name: ``chk_<table>_<rule_id>``."""
    return f"{CONSTRAINT_NAME_PREFIX}_{table}_{rule_id}"


def constraint_property(table: str, rule_id: str) -> str:
    """Table property that marks the constraint as present."""
    return CONSTRAINT_PROPERTY_PREFIX + constraint_name(table, rule_id)


def ddl_targets() -> Tuple[Tuple[str, str, str], ...]:
    """``(layer, table, rule_id)`` of the enforceable constraints, in order."""
    return tuple(
        (layer, table, rule_id)
        for rule_id in ENFORCEABLE_RULE_IDS
        for layer in RULES_BY_ID[rule_id].layers
        if layer in (SILVER, GOLD)
        for table in COLUMN_MAP[rule_id][layer]
    )


def profile_targets() -> Tuple[Tuple[str, str, str], ...]:
    """``(layer, table, rule_id)`` measured in T-08, deferred rules included."""
    return tuple(
        (layer, table, rule_id)
        for rule_id in PROFILE_RULE_IDS
        for layer in RULES_BY_ID[rule_id].layers
        for table in COLUMN_MAP[rule_id][layer]
        if layer in (SILVER, GOLD)
    )


def add_constraint_sql(
    catalog: str, layer: str, table: str, rule_id: str
) -> str:
    """``ALTER TABLE ... ADD CONSTRAINT`` for one rule and table."""
    expression = sql_expression(rule_id, layer, table)
    return (
        f"ALTER TABLE {catalog}.{layer}.{table}\n"
        f"  ADD CONSTRAINT {constraint_name(table, rule_id)}\n"
        f"  CHECK ({expression})"
    )


def drop_constraint_sql(
    catalog: str, layer: str, table: str, rule_id: str
) -> str:
    """Inverse of :func:`add_constraint_sql`."""
    return (
        f"ALTER TABLE {catalog}.{layer}.{table}\n"
        f"  DROP CONSTRAINT {constraint_name(table, rule_id)}"
    )


def ddl_statements(
    catalog: str, existing_properties: Iterable[str] = ()
) -> List[str]:
    """ADD statements for the constraints still absent from the table.

    ``existing_properties`` are the table properties read beforehand (for
    example the keys returned by ``SHOW TBLPROPERTIES t``); feeding them back
    makes the logical deployment idempotent: nothing is added twice.
    """
    existing = set(existing_properties)
    statements = []
    for layer, table, rule_id in ddl_targets():
        if constraint_property(table, rule_id) in existing:
            continue
        statements.append(add_constraint_sql(catalog, layer, table, rule_id))
    return statements


def rollback_statements(catalog: str) -> List[str]:
    """DROP statements that remove every generated constraint, in order."""
    return [
        drop_constraint_sql(catalog, layer, table, rule_id)
        for layer, table, rule_id in ddl_targets()
    ]


# ---------------------------------------------------------------------------
# Versioned scripts for T-07 (DDL) and T-08 (single SQL session)
# ---------------------------------------------------------------------------

#: Environments covered by the versioned scripts: development first, then production.
DEFAULT_CATALOGS: Tuple[str, ...] = ("job_offers_dev", "job_offers")


def _header(title: str, intro: Sequence[str]) -> str:
    lines = [f"-- {title}", "--"]
    lines.extend(f"-- {line}" for line in intro)
    return "\n".join(lines)


def ddl_script(catalogs: Sequence[str] = DEFAULT_CATALOGS) -> str:
    """Full forward DDL for ``catalogs`` plus the commented rollback block.

    The forward block is safe to run top to bottom only after the preflight:
    it adds the constraints that the plan emits, and the caller must have
    verified that the table properties do not list them yet. The rollback is
    commented out on purpose so re-running the file never removes a constraint.
    """
    parts = [
        _header(
            "Delta constraints of the data-quality contract (spec 012 T-07).",
            (
                "Generated by databricks_notebooks/Data_Quality.py; do not "
                "edit by hand (the tests compare this file with the module).",
                "Run the preflight first and skip any ADD whose property is "
                "already present. ADD CONSTRAINT validates the rows already "
                "in the table: do not run it over violations that T-08 has "
                "not resolved.",
                "Nothing here is executed by the pipeline.",
            ),
        ),
    ]
    for catalog in catalogs:
        parts.append(f"\n-- === {catalog} ===\n")
        parts.append("-- Preflight: existing constraints (one per table)")
        for layer, table in dict.fromkeys(
            (layer, table) for layer, table, _ in ddl_targets()
        ):
            parts.append(
                f"SHOW TBLPROPERTIES {catalog}.{layer}.{table};"
            )
        parts.append("\n-- Forward: add the missing constraints")
        parts.append("\n\n".join(
            statement + ";" for statement in ddl_statements(catalog)
        ))
        parts.append(f"\n-- Rollback of {catalog} (remove the -- to undo)")
        parts.extend(
            "\n".join(f"-- {line}" for line in statement.splitlines()) + ";"
            for statement in rollback_statements(catalog)
        )
    return "\n".join(parts) + "\n"


def profile_sql(catalog: str, layer: str, table: str, rule_id: str) -> str:
    """Count of rows of one table that violate ``rule_id`` and its NULLs."""
    columns = _columns(rule_id, layer, table)
    null_counts = ", \n       ".join(
        f"sum(CASE WHEN {_quote(column)} IS NULL THEN 1 ELSE 0 END) "
        f"AS {column}_nulls"
        for column in columns
    )
    extra = ""
    if rule_id == "posted_date_contract":
        date = _quote(columns[0])
        extra = (
            f",\n       min({date}) AS min_date,\n"
            f"       max({date}) AS max_date"
        )
    expression = sql_expression(rule_id, layer, table)
    return (
        f"SELECT '{catalog}.{layer}.{table}' AS target,\n"
        f"       '{rule_id}' AS rule_id,\n"
        f"       count(*) AS rows_total,\n"
        f"       sum(CASE WHEN {expression} THEN 0 ELSE 1 END) AS violating_rows,\n"
        f"       {null_counts}{extra}\n"
        f"FROM {catalog}.{layer}.{table}"
    )


def quarantine_inventory_sql(catalog: str, source: str) -> str:
    """Rows per quarantine reason and their time range, without payloads."""
    table = f"{catalog}.{BRONZE}.{source}_quarantine"
    return (
        f"SELECT '{table}' AS source_table,\n"
        f"       {_quote(upsert.QUARANTINE_REASON_COLUMN)} AS reason,\n"
        "       count(*) AS quarantine_rows,\n"
        f"       min({_quote(upsert.QUARANTINE_AT_COLUMN)}) AS first_seen,\n"
        f"       max({_quote(upsert.QUARANTINE_AT_COLUMN)}) AS last_seen\n"
        f"FROM {table}\n"
        f"GROUP BY {_quote(upsert.QUARANTINE_REASON_COLUMN)}"
    )


def profile_script(catalogs: Sequence[str] = DEFAULT_CATALOGS) -> str:
    """Statements of the single SQL session (T-08), ready to paste.

    Each constraint profile is one statement so a failing table can be skipped
    without losing the others; the quarantine inventory is one statement per
    source table because T-01 measured that none exists yet.
    """
    parts = [
        _header(
            "Single SQL session of spec 012 T-08 (profiles and quarantine).",
            (
                "Generated by databricks_notebooks/Data_Quality.py; do not "
                "edit by hand (the tests compare this file with the module).",
                "Warehouse STOPPED before and after. Sentencias preparadas "
                "para una sola ventana de menos de 10 minutos (~0,43 EUR).",
                "Run each block on its own: a missing quarantine table (T-01 "
                "measured none) must not abort the rest of the session.",
            ),
        ),
    ]
    for catalog in catalogs:
        parts.append(
            f"\n-- === {catalog}: constraints and rule violations ===\n"
        )
        parts.append("\n\n".join(
            statement + ";" for statement in (
                profile_sql(catalog, layer, table, rule_id)
                for layer, table, rule_id in profile_targets()
            )
        ))
        parts.append(
            f"\n-- === {catalog}: existing constraints (free metadata) ===\n"
        )
        parts.extend(
            f"SHOW TBLPROPERTIES {catalog}.{layer}.{table};"
            for layer in (SILVER, GOLD)
            for table in COLUMN_MAP["job_id_required"][layer]
        )
        parts.append(
            f"\n-- === {catalog}: quarantine inventory (RF-6) ===\n"
        )
        parts.append(
            "-- Run only the blocks whose table exists (inventory below).\n"
            f"SELECT table_schema, table_name\n"
            f"FROM {catalog}.information_schema.tables\n"
            "WHERE table_schema = 'bronze' AND table_name LIKE '%_quarantine';"
        )
        parts.append("\n\n".join(
            quarantine_inventory_sql(catalog, source) + ";"
            for source in upsert.SOURCE_SPECS
        ))
    return "\n".join(parts) + "\n"
