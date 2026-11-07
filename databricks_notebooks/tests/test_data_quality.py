"""Tests of the data-quality contract (spec 012 T-05).

Pure registry/DDL tests plus the Spark predicates. No Delta and no DDL is
executed here: the local Delta enforcement lives in
``test_constraints_delta.py`` (T-07) and the real one in the T-08 session.
"""
import datetime as dt
import os
import sys

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F841

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import types as T  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Data_Quality as q  # noqa: E402
import Upsert_Job_Offers as upsert  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    """Local session for the predicates (no Delta needed)."""
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    session = (
        SparkSession.builder.master("local[1]")
        .appName("data-quality-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.python.worker.timeout", "180")
        .config("spark.network.timeout", "300")
        .getOrCreate()
    )
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_rule_ids_are_stable_and_unique():
    assert [rule.rule_id for rule in q.RULES] == [
        "missing_key",
        "missing_scraped_at",
        "company_name_rating",
        "job_id_required",
        "salary_range_order",
        "posted_date_contract",
    ]
    assert len(q.RULES_BY_ID) == len(q.RULES)


def test_every_rule_declares_layer_action_and_evidence():
    for rule in q.RULES:
        assert rule.layers, rule.rule_id
        assert set(rule.layers) <= {q.BRONZE, q.SILVER, q.GOLD}, rule.rule_id
        assert rule.action in {q.QUARANTINE, q.MARK, q.CONSTRAINT}, rule.rule_id
        assert rule.description.strip(), rule.rule_id
        assert rule.evidence.strip(), rule.rule_id
        if rule.action == q.QUARANTINE:
            assert rule.reason, rule.rule_id
        if rule.status == q.DEFERRED:
            assert rule.note.strip(), rule.rule_id


def test_quarantine_rules_keep_the_three_existing_reasons():
    reasons = {
        rule.reason for rule in q.RULES if rule.action == q.QUARANTINE
    }
    assert reasons == {
        upsert.MISSING_KEY,
        upsert.MISSING_SCRAPED_AT,
        upsert.COMPANY_NAME_RATING,
    }
    # The contract consumes the operational constant, it does not restate it.
    assert q.RULES_BY_ID[upsert.COMPANY_NAME_RATING].reason == (
        upsert.COMPANY_NAME_RATING
    )


def test_existing_rules_point_at_their_implementation():
    # Rules already implemented are referenced, not reimplemented.
    assert "Upsert_Job_Offers" in q.RULES_BY_ID["missing_key"].evidence
    assert "Upsert_Job_Offers" in q.RULES_BY_ID["missing_scraped_at"].evidence
    assert "Prepare_Gold" in q.RULES_BY_ID["posted_date_contract"].evidence


# ---------------------------------------------------------------------------
# Table-column map per layer
# ---------------------------------------------------------------------------

def test_column_map_uses_the_real_names_of_each_layer():
    for table in q.SILVER_OFFER_TABLES:
        assert q.COLUMN_MAP["job_id_required"][q.SILVER][table] == ("job_id",)
        assert q.COLUMN_MAP["salary_range_order"][q.SILVER][table] == (
            "salary_min_annual", "salary_max_annual",
        )
    assert q.COLUMN_MAP["job_id_required"][q.GOLD]["fact_offers"] == ("job_id",)
    assert q.COLUMN_MAP["salary_range_order"][q.GOLD]["fact_offers"] == (
        "SalaryMinAnnual_EUR", "SalaryMaxAnnual_EUR",
    )
    assert q.COLUMN_MAP["posted_date_contract"][q.GOLD]["fact_offers"] == (
        "posted_date",
    )


def test_column_map_targets_only_known_tables():
    for rule_id, layers in q.COLUMN_MAP.items():
        for layer, tables in layers.items():
            assert layer in {q.BRONZE, q.SILVER, q.GOLD}, rule_id
            if layer == q.SILVER:
                assert set(tables) == set(q.SILVER_OFFER_TABLES), rule_id
            elif layer == q.GOLD:
                assert set(tables) == {q.GOLD_FACT_TABLE}, rule_id


def test_bronze_keys_come_from_the_separation_specs():
    assert q.COLUMN_MAP["job_id_required"][q.BRONZE] == q.BRONZE_KEYS
    for source, spec in upsert.SOURCE_SPECS.items():
        assert q.BRONZE_KEYS[source] == tuple(spec.keys)


def test_unknown_rule_or_table_fails_loudly():
    with pytest.raises(ValueError):
        q.sql_expression("no_such_rule", q.SILVER, "offers_indeed")
    with pytest.raises(ValueError):
        q.sql_expression("job_id_required", q.SILVER, "no_such_table")


# ---------------------------------------------------------------------------
# Predicates: valid, invalid, null and boundary
# ---------------------------------------------------------------------------

def test_job_id_required_predicate(spark):
    df = spark.createDataFrame(
        [("1",), (None,), ("",), ("   ",), (" 1 ",)],
        ["job_id"],
    )
    assert {row["job_id"] for row in df.filter(
        q.job_id_required()
    ).collect()} == {"1", " 1 "}


def test_salary_range_order_predicate(spark):
    df = spark.createDataFrame(
        [
            (10.0, 20.0),   # ordered
            (20.0, 20.0),   # equal is valid
            (20.0, 10.0),   # inverted: invalid
            (None, 10.0),   # missing minimum is valid
            (10.0, None),   # missing maximum is valid
            (None, None),   # no salary is valid
            (-5.0, -1.0),   # positivity is another rule, not this one
        ],
        ["salary_min_annual", "salary_max_annual"],
    )
    assert df.filter(q.salary_range_order()).count() == 6


def test_salary_range_order_uses_the_gold_names(spark):
    df = spark.createDataFrame(
        [(1.0, 2.0), (2.0, 1.0), (None, 1.0)],
        ["SalaryMinAnnual_EUR", "SalaryMaxAnnual_EUR"],
    )
    out = df.filter(q.predicate(
        "salary_range_order", q.GOLD, "fact_offers"
    )).collect()
    assert [row["SalaryMinAnnual_EUR"] for row in out] == [1.0, None]


def test_posted_date_contract_predicate(spark):
    df = spark.createDataFrame(
        [
            (None,),
            (dt.date(2026, 1, 1),),
            (dt.date(2025, 12, 31),),
            (dt.date(2089, 12, 31),),
            (dt.date(2090, 1, 1),),
        ],
        ["posted_date"],
    )
    out = df.filter(q.posted_date_contract()).collect()
    assert {row["posted_date"] for row in out} == {
        None, dt.date(2026, 1, 1), dt.date(2089, 12, 31),
    }


def test_company_name_rating_matches_the_bronze_separation(spark):
    names = ["4,0", "4.0", " 4,00 ", "4.0 Retail", "24/7 Retail", None, ""]
    df = spark.createDataFrame([(name,) for name in names], ["company_name"])
    contract = {
        row["company_name"]
        for row in df.filter(
            q.predicate("company_name_rating", q.BRONZE, "linkedin")
        ).collect()
    }
    operational = {
        row["company_name"]
        for row in df.filter(upsert.company_name_rating_expr(df)).collect()
    }
    assert contract == operational == {"4,0", "4.0", " 4,00 "}
    assert upsert.RATING_PATTERN in q.sql_expression(
        "company_name_rating", q.BRONZE, "linkedin"
    )


def test_predicate_and_ddl_share_the_same_expression():
    for layer, table, rule_id in q.ddl_targets():
        expression = q.sql_expression(rule_id, layer, table)
        statement = q.add_constraint_sql("job_offers", layer, table, rule_id)
        assert f"CHECK ({expression})" in statement


# ---------------------------------------------------------------------------
# DDL generation (nothing is executed)
# ---------------------------------------------------------------------------

def test_constraint_names_and_properties_are_stable():
    assert q.constraint_name("offers_indeed", "job_id_required") == (
        "chk_offers_indeed_job_id_required"
    )
    assert q.constraint_property("fact_offers", "salary_range_order") == (
        "delta.constraints.chk_fact_offers_salary_range_order"
    )


def test_ddl_covers_every_enforceable_table_once():
    targets = q.ddl_targets()
    assert len(targets) == 10
    assert len(set(targets)) == 10
    layers = {layer for layer, _, _ in targets}
    assert layers == {q.SILVER, q.GOLD}
    assert all(
        rule_id in q.ENFORCEABLE_RULE_IDS for _, _, rule_id in targets
    )


def test_add_and_drop_are_inverse():
    for layer, table, rule_id in q.ddl_targets():
        add = q.add_constraint_sql("job_offers", layer, table, rule_id)
        drop = q.drop_constraint_sql("job_offers", layer, table, rule_id)
        name = q.constraint_name(table, rule_id)
        assert f"ADD CONSTRAINT {name}" in add
        assert f"DROP CONSTRAINT {name}" in drop
        assert f"job_offers.{layer}.{table}" in add
        assert f"job_offers.{layer}.{table}" in drop


def test_ddl_is_idempotent_with_the_existing_properties():
    all_properties = [
        q.constraint_property(table, rule_id)
        for layer, table, rule_id in q.ddl_targets()
    ]
    assert q.ddl_statements("job_offers", all_properties) == []
    remaining = q.ddl_statements("job_offers", all_properties[:1])
    assert len(remaining) == len(all_properties) - 1
    assert all("offers_indeed_job_id_required" not in sql for sql in remaining)


def test_deferred_rule_generates_no_ddl():
    assert q.RULES_BY_ID["posted_date_contract"].status == q.DEFERRED
    assert "posted_date_contract" not in {
        rule_id for _, _, rule_id in q.ddl_targets()
    }
    assert "posted_date_contract" in {
        rule_id for _, _, rule_id in q.profile_targets()
    }


def test_rollback_removes_every_constraint():
    statements = q.rollback_statements("job_offers")
    assert len(statements) == len(q.ddl_targets())
    assert all("DROP CONSTRAINT chk_" in statement for statement in statements)


def test_ddl_script_covers_dev_and_prod_and_comments_the_rollback():
    script = q.ddl_script()
    assert script.count("\n  ADD CONSTRAINT ") == 20
    assert script.count("job_offers_dev.silver.offers_indeed") >= 1
    assert script.count("job_offers.gold.fact_offers") >= 1
    assert script.count("-- ALTER TABLE") == 20
    # A whole-file run never drops a constraint: every DROP is commented.
    assert "\nALTER TABLE" in script
    assert "\nDROP CONSTRAINT" not in script


# ---------------------------------------------------------------------------
# Profile statements for T-08
# ---------------------------------------------------------------------------

def test_profile_counts_violations_and_nulls():
    statement = q.profile_sql(
        "job_offers", q.SILVER, "offers_indeed", "job_id_required"
    )
    assert "count(*) AS rows_total" in statement
    assert "violating_rows" in statement
    assert "`job_id`_nulls" not in statement  # the alias has no backticks
    assert "job_id_nulls" in statement
    assert q.sql_expression(
        "job_id_required", q.SILVER, "offers_indeed"
    ) in statement
    assert "FROM job_offers.silver.offers_indeed" in statement


def test_profile_of_gold_salary_has_both_null_counts():
    statement = q.profile_sql(
        "job_offers", q.GOLD, "fact_offers", "salary_range_order"
    )
    assert "SalaryMinAnnual_EUR_nulls" in statement
    assert "SalaryMaxAnnual_EUR_nulls" in statement


def test_profile_of_dates_adds_the_range():
    statement = q.profile_sql(
        "job_offers", q.GOLD, "fact_offers", "posted_date_contract"
    )
    assert "min_date" in statement and "max_date" in statement


def test_profile_script_covers_both_catalogs_and_the_quarantine():
    script = q.profile_script()
    for catalog in q.DEFAULT_CATALOGS:
        assert f"{catalog}.silver.offers_indeed" in script
        assert f"{catalog}.gold.fact_offers" in script
        assert f"{catalog}.information_schema.tables" in script
        for source in upsert.SOURCE_SPECS:
            assert f"{catalog}.bronze.{source}_quarantine" in script
    # Deferred date rule is measured even though it generates no DDL.
    assert script.count("'posted_date_contract' AS rule_id") == 2
    assert script.count("violating_rows") == 22
