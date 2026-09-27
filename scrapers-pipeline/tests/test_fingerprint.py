"""Tests for the comparability fingerprint (T-32; RF-7).

The fingerprint is pure data, so most tests need no I/O. The integration tests
run ``ensure_compatible.py`` as a subprocess over a temporary staging directory
with a small PyArrow Parquet (PyArrow is already justified by the plan,
section 6.3); no network, AzCopy or credentials are involved.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import fingerprint, landing, sources

PIPELINE_DIR = Path(__file__).resolve().parents[1]
ENSURE_SCRIPT = PIPELINE_DIR / "ensure_compatible.py"

MULTI_SITE_IDS = (
    "irishjobs",
    "stepstone_nl",
    "devitjobs",
    "nvb",
    "jobs_ch",
    "glassdoor",
)


def _dim(source: str, search: str, region: str = "", city: str = ""):
    return fingerprint.SearchDimension(
        source=source, search=search, region=region, city=city
    )


# --------------------------------------------------------------------------
# build_fingerprint
# --------------------------------------------------------------------------


def test_build_fingerprint_has_expected_shape():
    result = fingerprint.build_fingerprint(
        ["indeed", "indeed"], [_dim("indeed", "data engineer", "ES", "Madrid")]
    )

    assert set(result) == {"schema_version", "sources", "searches", "hash"}
    assert result["schema_version"] == fingerprint.FINGERPRINT_SCHEMA_VERSION
    assert result["sources"] == ["indeed"]
    assert result["searches"] == [
        {"source": "indeed", "search": "data engineer", "region": "ES", "city": "Madrid"}
    ]
    assert result["hash"].startswith("sha256:")


def test_build_fingerprint_changes_when_sources_change():
    searches = [_dim("indeed", "data engineer")]
    one = fingerprint.build_fingerprint(["indeed"], searches)
    two = fingerprint.build_fingerprint(["indeed", "linkedin"], searches)

    assert one["hash"] != two["hash"]


def test_build_fingerprint_changes_when_searches_change():
    sources_arg = ["indeed"]
    first = fingerprint.build_fingerprint(sources_arg, [_dim("indeed", "data engineer")])
    second = fingerprint.build_fingerprint(sources_arg, [_dim("indeed", "python")])

    assert first["hash"] != second["hash"]


def test_build_fingerprint_changes_when_regions_change():
    first = fingerprint.build_fingerprint(
        ["infojobs"], [_dim("infojobs", "python", city="Madrid")]
    )
    second = fingerprint.build_fingerprint(
        ["infojobs"], [_dim("infojobs", "python", city="Barcelona")]
    )

    assert first["hash"] != second["hash"]


def test_build_fingerprint_is_stable_under_reordering():
    first = fingerprint.build_fingerprint(
        ["linkedin", "indeed"],
        [_dim("indeed", "python"), _dim("linkedin", "data analyst", city="Madrid")],
    )
    second = fingerprint.build_fingerprint(
        ["indeed", "linkedin"],
        [_dim("linkedin", "data analyst", city="Madrid"), _dim("indeed", "python")],
    )

    assert first == second


def test_build_fingerprint_is_deterministic_and_deduplicates():
    args = (["indeed"], [_dim("indeed", "python"), _dim("indeed", "python")])
    first = fingerprint.build_fingerprint(*args)
    second = fingerprint.build_fingerprint(*args)

    assert first == second
    assert first["searches"] == [
        {"source": "indeed", "search": "python", "region": "", "city": ""}
    ]


def test_build_fingerprint_trims_values():
    result = fingerprint.build_fingerprint(
        ["  indeed  "], [_dim(" indeed ", " python ", " ES ", " Madrid ")]
    )

    assert result["sources"] == ["indeed"]
    assert result["searches"][0]["search"] == "python"


def test_build_fingerprint_rejects_credentials():
    with pytest.raises(ValueError):
        fingerprint.build_fingerprint(
            ["indeed"], [_dim("indeed", "node?sv=2024&sig=SUPERSECRET")]
        )
    with pytest.raises(ValueError):
        fingerprint.build_fingerprint(["indeed"], [_dim("indeed", "SAS token")])


def test_build_fingerprint_accepts_normal_terms():
    result = fingerprint.build_fingerprint(
        ["indeed"],
        [_dim("indeed", "data engineer", "ES", "Madrid")],
    )

    assert result["sources"] == ["indeed"]


# --------------------------------------------------------------------------
# is_credential_free
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "data engineer",
        "Data Analyst",
        "Madrid",
        "python",
        "",
        ["python", "data engineer"],
        {"search": "data engineer", "city": "Dublin"},
    ],
)
def test_is_credential_free_accepts_normal_values(value):
    assert fingerprint.is_credential_free(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "sv=2024-01-01&sig=ABC123",
        "sig=ABC123",
        "bearer token",
        "my-password",
        "top secret",
        "AccountKey=abc==",
        "SharedAccessSignature",
        "SAS",
        {"search": "ok", "token": "abc"},
        ["fine", "password=123"],
    ],
)
def test_is_credential_free_rejects_secret_like_values(value):
    assert fingerprint.is_credential_free(value) is False


# --------------------------------------------------------------------------
# fingerprints_match
# --------------------------------------------------------------------------


def test_fingerprints_match_same_hash():
    one = fingerprint.build_fingerprint(["indeed"], [])
    two = fingerprint.build_fingerprint(["indeed"], [])

    assert fingerprint.fingerprints_match(one, two) is True


def test_fingerprints_match_different_hash():
    one = fingerprint.build_fingerprint(["indeed"], [])
    two = fingerprint.build_fingerprint(["linkedin"], [])

    assert fingerprint.fingerprints_match(one, two) is False


@pytest.mark.parametrize(
    "left,right",
    [
        (None, {"hash": "sha256:x"}),
        ({"hash": "sha256:x"}, None),
        (None, None),
        ({}, {"hash": "sha256:x"}),
        ({"hash": ""}, {"hash": ""}),
        ({"hash": "sha256:x"}, {"hash": "sha256:y"}),
    ],
)
def test_fingerprints_match_requires_comparable_hashes(left, right):
    assert fingerprint.fingerprints_match(left, right) is False


# --------------------------------------------------------------------------
# search_dimensions_from_table
# --------------------------------------------------------------------------


def test_search_dimensions_from_indeed_table():
    table = pa.table(
        {
            "search_term": ["data engineer", "data engineer", "python"],
            "city_query": ["Madrid", "Madrid", "Barcelona"],
            "country": ["ES", "ES", "ES"],
        }
    )

    dims = fingerprint.search_dimensions_from_table(table, "indeed")

    assert dims == (
        _dim("indeed", "data engineer", "ES", "Madrid"),
        _dim("indeed", "python", "ES", "Barcelona"),
    )


def test_search_dimensions_from_linkedin_table():
    table = pa.table(
        {
            "search_role": ["Data Analyst", "Data Analyst"],
            "search_city": ["Madrid", "Barcelona"],
        }
    )

    dims = fingerprint.search_dimensions_from_table(table, "linkedin")

    assert dims == (
        _dim("linkedin", "Data Analyst", city="Barcelona"),
        _dim("linkedin", "Data Analyst", city="Madrid"),
    )


def test_search_dimensions_from_infojobs_table():
    table = pa.table(
        {
            "keyword_buscada": ["python", "python"],
            "ciudad_buscada": ["Madrid", "Madrid"],
        }
    )

    dims = fingerprint.search_dimensions_from_table(table, "infojobs")

    assert dims == (_dim("infojobs", "python", city="Madrid"),)


def test_search_dimensions_from_multi_site_table_per_portal():
    table = pa.table(
        {
            "site": ["irishjobs", "irishjobs", "glassdoor"],
            "search_role": ["data engineer", "python", "data engineer"],
            "search_city": ["Dublin", "Dublin", ""],
        }
    )

    irish = fingerprint.search_dimensions_from_table(table, "irishjobs", site="irishjobs")

    assert irish == (
        _dim("irishjobs", "data engineer", city="Dublin"),
        _dim("irishjobs", "python", city="Dublin"),
    )
    assert all(dim.source == "irishjobs" for dim in irish)


def test_search_dimensions_from_multi_site_groups_by_site_when_no_filter():
    table = pa.table(
        {
            "site": ["irishjobs", "glassdoor"],
            "search_role": ["data engineer", "data engineer"],
            "search_city": ["Dublin", ""],
        }
    )

    dims = fingerprint.search_dimensions_from_table(table, "irishjobs")

    assert {dim.source for dim in dims} == {"irishjobs", "glassdoor"}


def test_search_dimensions_without_trace_columns_returns_empty():
    table = pa.table({"job_id": ["1", "2"]})

    assert fingerprint.search_dimensions_from_table(table, "indeed") == ()
    assert fingerprint.search_dimensions_from_table(table, "linkedin") == ()
    assert fingerprint.search_dimensions_from_table(table, "infojobs") == ()
    assert fingerprint.search_dimensions_from_table(table, "irishjobs") == ()


def test_search_dimensions_unknown_source_returns_empty():
    table = pa.table({"search_role": ["x"], "search_city": ["y"]})

    assert fingerprint.search_dimensions_from_table(table, "unknown") == ()


# --------------------------------------------------------------------------
# sources: trace columns and scope
# --------------------------------------------------------------------------


def test_search_trace_columns_known_sources():
    assert sources.search_trace_columns("indeed") == {
        "search": "search_term",
        "city": "city_query",
        "region": "country",
    }
    assert sources.search_trace_columns("linkedin") == {
        "search": "search_role",
        "city": "search_city",
    }
    assert sources.search_trace_columns("infojobs") == {
        "search": "keyword_buscada",
        "city": "ciudad_buscada",
    }
    assert sources.search_trace_columns("glassdoor") == {
        "search": "search_role",
        "city": "search_city",
        "site": "site",
    }


def test_search_trace_columns_unknown_source_is_empty():
    assert sources.search_trace_columns("does_not_exist") == {}


def test_fingerprint_scope_multi_site_covers_six_portals():
    assert sources.fingerprint_scope("multi_site") == MULTI_SITE_IDS


@pytest.mark.parametrize("name", ["indeed", "linkedin", "infojobs"])
def test_fingerprint_scope_direct_source_is_itself(name):
    assert sources.fingerprint_scope(name) == (name,)


def test_fingerprint_scope_unknown_is_empty():
    assert sources.fingerprint_scope("does_not_exist") == ()
    assert sources.fingerprint_scope("") == ()


# --------------------------------------------------------------------------
# ensure_compatible integration (subprocess over a temp staging dir)
# --------------------------------------------------------------------------


def _run_ensure(stage: Path, manifest: Path, *, fingerprint_source=None, required=""):
    argv = [
        sys.executable,
        str(ENSURE_SCRIPT),
        str(stage),
        "--manifest",
        str(manifest),
    ]
    if required:
        argv += ["--required-cols", required]
    if fingerprint_source:
        argv += ["--fingerprint-source", fingerprint_source]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _write_indeed_stage(stage: Path) -> None:
    table = pa.table(
        {
            "job_key": ["1", "2", "3"],
            "title": ["t", "t", "t"],
            "company": ["c", "c", "c"],
            "viewjob_url": ["u", "u", "u"],
            "scraped_at": ["2026-01-01", "2026-01-01", "2026-01-01"],
            "search_term": ["data engineer", "data engineer", "python"],
            "city_query": ["Madrid", "Madrid", "Barcelona"],
            "country": ["ES", "ES", "ES"],
        }
    )
    pq.write_table(table, stage / "offers.parquet")


INDEED_REQUIRED = "job_key,title,company,viewjob_url,scraped_at"


def test_ensure_compatible_adds_fingerprint_with_source_flag(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_indeed_stage(stage)
    manifest_path = tmp_path / "manifest.json"

    result = _run_ensure(
        stage, manifest_path, fingerprint_source="indeed", required=INDEED_REQUIRED
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "fingerprint" in payload
    fp = payload["fingerprint"]
    assert set(fp) == {"schema_version", "sources", "searches", "hash"}
    assert fp["sources"] == ["indeed"]
    assert {entry["search"] for entry in fp["searches"]} == {
        "data engineer",
        "python",
    }
    assert fp["hash"].startswith("sha256:")
    # No credentials anywhere in the published fingerprint.
    assert fingerprint.is_credential_free(fp) is True
    assert "sig=" not in json.dumps(fp)


def test_ensure_compatible_without_flag_has_no_fingerprint(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_indeed_stage(stage)
    manifest_path = tmp_path / "manifest.json"

    result = _run_ensure(stage, manifest_path, required=INDEED_REQUIRED)

    assert result.returncode == 0, result.stderr
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "fingerprint" not in payload


def test_ensure_compatible_multi_site_fingerprint_covers_portals(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    table = pa.table(
        {
            "job_id": ["1", "2", "3"],
            "job_url": ["u", "u", "u"],
            "title": ["t", "t", "t"],
            "company_name": ["c", "c", "c"],
            "scraped_at": ["2026-01-01", "2026-01-01", "2026-01-01"],
            "site": ["irishjobs", "irishjobs", "glassdoor"],
            "search_role": ["data engineer", "python", "data engineer"],
            "search_city": ["Dublin", "Dublin", ""],
        }
    )
    pq.write_table(table, stage / "jobs_unified.parquet")
    manifest_path = tmp_path / "manifest.json"
    required = "job_id,job_url,title,company_name,scraped_at"

    result = _run_ensure(
        stage,
        manifest_path,
        fingerprint_source="multi_site",
        required=required,
    )

    assert result.returncode == 0, result.stderr
    fp = json.loads(manifest_path.read_text(encoding="utf-8"))["fingerprint"]
    assert fp["sources"] == sorted(MULTI_SITE_IDS)
    portal_sources = {entry["source"] for entry in fp["searches"]}
    assert portal_sources == {"irishjobs", "glassdoor"}
    assert fingerprint.is_credential_free(fp) is True


def test_landing_parse_manifest_accepts_with_and_without_fingerprint(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    _write_indeed_stage(stage)

    with_fp_path = tmp_path / "with.json"
    without_fp_path = tmp_path / "without.json"
    assert (
        _run_ensure(
            stage, with_fp_path, fingerprint_source="indeed", required=INDEED_REQUIRED
        ).returncode
        == 0
    )
    assert _run_ensure(stage, without_fp_path, required=INDEED_REQUIRED).returncode == 0

    with_fp = landing.parse_manifest(
        json.loads(with_fp_path.read_text(encoding="utf-8")), scraper="indeed"
    )
    without_fp = landing.parse_manifest(
        json.loads(without_fp_path.read_text(encoding="utf-8")), scraper="indeed"
    )

    assert with_fp is not None and with_fp.fingerprint is not None
    assert with_fp.fingerprint["sources"] == ["indeed"]
    assert without_fp is not None and without_fp.fingerprint is None
