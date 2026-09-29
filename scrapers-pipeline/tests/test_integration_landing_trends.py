"""Integration tests of Azure landing, delta and trends (T-42; RF-6-RF-8, RF-13).

This is the end-to-end counterpart of the pure/unit tests of T-30-T-34: a
simulated landing is served by an in-memory :class:`verification.landing.RemoteReader`
and the whole scenario is driven through
:func:`verification.verify_run.run_diagnostic`, so the manifests, the published
objects, the snapshot/delta distinction and the comparable history are exercised
together.

Everything is offline: no real AzCopy, no network, no credentials and no Azure.
Published payloads are built with PyArrow (a dependency already justified in the
plan, section 6.3) and the local run fixture is written under ``tmp_path``.

One scenario is shared by several tests (a fake landing with a valid Indeed
object, a rejected Multi-site manifest, an empty LinkedIn delta and a pending
InfoJobs manifest) plus the five comparable Indeed runs. Reported to the
orchestrator (to be fixed in another spec, not here): through ``run_diagnostic``
an object that is not listed yet is currently degraded from ``pending`` to
``not_checked`` because the published delta is loaded after the manifest check
raises ``RemoteError``. The pending rule is proven at the manifest/publication
boundary and the reachable ``run_diagnostic`` outcome is asserted as the safety
property (never failed, never a false zero), never asserted as the correct
pending state.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import (
    fingerprint,
    landing,
    publication,
    report,
    status,
    verify_run,
)

# The analysed run window: the general log opens at 00:00:05 and closes at
# 07:05:39 local time, so every local artifact below is stamped inside it.
_RUN_MTIME = datetime(2026, 9, 26, 1, 0, 0)
_RUN_DATE = "2026-09-26"


# --------------------------------------------------------------------------
# In-memory landing reader
# --------------------------------------------------------------------------


class FakeReader:
    """In-memory :class:`landing.RemoteReader` simulating the landing.

    ``fail_list`` makes every listing raise :class:`landing.RemoteError`, which
    models a connectivity/credential failure without any network.
    """

    def __init__(
        self, objects: dict[str, bytes] | None = None, *, fail_list: str | None = None
    ) -> None:
        self.objects = dict(objects or {})
        self.fail_list = fail_list
        self.closed = False

    def list_objects(self, prefix: str) -> list[landing.RemoteObject]:
        if self.fail_list is not None:
            raise landing.RemoteError(self.fail_list)
        return [
            landing.RemoteObject(path=key, size=len(data))
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def download(self, remote_path: str, local_path: Path) -> None:
        data = self.objects.get(remote_path)
        if data is None:
            raise landing.RemoteError("objeto no encontrado")
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _set_mtime(path: Path, when: datetime | None = None) -> None:
    moment = when if when is not None else _RUN_MTIME
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _set_mtime(path)
    return path


def _write_json(root: Path, relative: str, payload: object) -> Path:
    return _write(root, relative, json.dumps(payload))


def _table(columns: tuple[str, ...], rows: list[dict]) -> pa.Table:
    """Build a table with every column present, even with no rows."""
    if rows:
        return pa.table(
            {column: [row.get(column) for row in rows] for column in columns}
        )
    return pa.table({column: pa.array([], type=pa.string()) for column in columns})


def _parquet_at(
    root: Path, relative: str, columns: tuple[str, ...], rows: list[dict]
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(_table(columns, rows), path)
    _set_mtime(path)
    return path


def _parquet_bytes(columns: tuple[str, ...], rows: list[dict]) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(_table(columns, rows), sink)
    return sink.getvalue().to_pybytes()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------
# Local nine-source run fixture (only Indeed and LinkedIn produce data)
# --------------------------------------------------------------------------


_INDEED_COLUMNS = (
    "job_key",
    "title",
    "company",
    "description_text",
    "salary_text",
    "workplace_type",
    "location",
    "posted_date",
    "viewjob_url",
    "scraped_at",
    "search_term",
    "city_query",
    "country",
)
_LINKEDIN_COLUMNS = (
    "job_id",
    "title",
    "company_name",
    "description_full",
    "salary_raw",
    "skills",
    "work_mode",
    "location_raw",
    "posted_datetime",
    "job_url",
    "scraped_at",
    "search_role",
    "search_city",
)


def _indeed_rows(count: int = 3) -> list[dict]:
    return [
        {
            "job_key": f"indeed-{index}",
            "title": f"Data Engineer {index}",
            "company": "ACME Corp",
            "description_text": "A real description.",
            "salary_text": "30000",
            "workplace_type": "Remote",
            "location": "Madrid",
            "posted_date": "2026-09-01",
            "viewjob_url": f"https://indeed.example/{index}",
            "scraped_at": _RUN_DATE,
            "search_term": "data engineer",
            "city_query": "Madrid",
            "country": "España",
        }
        for index in range(count)
    ]


def _linkedin_rows(count: int = 2) -> list[dict]:
    return [
        {
            "job_id": f"linkedin-{index}",
            "title": f"Data Engineer {index}",
            "company_name": "ACME Corp",
            "description_full": "A real description.",
            "salary_raw": "30000",
            "skills": ["Python"],
            "work_mode": "Remote",
            "location_raw": "Barcelona",
            "posted_datetime": "2026-09-01",
            "job_url": f"https://linkedin.example/{index}",
            "scraped_at": _RUN_DATE,
            "search_role": "data engineer",
            "search_city": "Barcelona",
        }
        for index in range(count)
    ]


def _write_general_log(root: Path) -> None:
    lines = [
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====",
        "00:00:08  [INFO]  [indeed] Lanzado PID 100 "
        "(wrapper: run_nightly_indeed.ps1)",
        "07:05:39  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 25534s ====",
    ]
    for key in ("indeed", "linkedin", "infojobs", "multi_site"):
        lines.append(f"07:05:39  [INFO]    [{key}] status=ok subidos=1 rechazados=0")
    _write(
        root,
        "scrapers-pipeline/logs/upload-2026-09-26.log",
        "\n".join(lines) + "\n",
    )


def _build_indeed_local(root: Path, rows: list[dict]) -> None:
    _write(
        root,
        "indeed_jobs_scraper/output/nightly.log",
        "2026-09-26 00:12:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:12:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        root,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": len(rows), "finished_at": "2026-09-26T05:04:55"},
    )
    _parquet_at(
        root,
        "indeed_jobs_scraper/output/indeed_jobs_20260926_0045.parquet",
        _INDEED_COLUMNS,
        rows,
    )


def _build_linkedin_local(root: Path, rows: list[dict]) -> None:
    _write(
        root,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:12:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:12:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        root,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        f"00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] {len(rows)} "
        "ofertas base anadidas (sin detalle aun).\n",
    )
    _write(
        root,
        "linkedin_jobs_scraper/data/last_nightly_run.txt",
        "last_run=20260926_013540 exit=0 attempts=1\n",
    )
    _parquet_at(
        root,
        "linkedin_jobs_scraper/data/output/jobs.parquet",
        _LINKEDIN_COLUMNS,
        rows,
    )


def _build_local_run(root: Path) -> Path:
    """Build a finished run where Indeed and LinkedIn produced offers."""
    _write_general_log(root)
    _build_indeed_local(root, _indeed_rows(3))
    _build_linkedin_local(root, _linkedin_rows(2))
    return root / "scrapers-pipeline" / "logs"


# --------------------------------------------------------------------------
# Manifest helpers and fingerprints
# --------------------------------------------------------------------------


def _ok_entry(file: str, remote: str | None, rows: int | None, sha: str) -> dict:
    return {
        "file": file,
        "status": "ok",
        "rows": rows,
        "bytes": 0,
        "sha256": sha,
        "converted": False,
        "error": None,
        "remote": remote,
    }


def _manifest_payload(
    entries: list[dict], *, fp: dict | None = None, bad_files: int = 0
) -> bytes:
    payload: dict = {
        "schema_version": 1,
        "total_files": len(entries),
        "bad_files": bad_files,
        "files": entries,
    }
    if fp is not None:
        payload["fingerprint"] = fp
    return _json_bytes(payload)


def _fp_a() -> dict:
    return fingerprint.build_fingerprint(
        ["indeed"], [fingerprint.SearchDimension(source="indeed", search="data engineer")]
    )


def _fp_b() -> dict:
    return fingerprint.build_fingerprint(
        ["indeed"], [fingerprint.SearchDimension(source="indeed", search="python")]
    )


# --------------------------------------------------------------------------
# The shared simulated landing
# --------------------------------------------------------------------------


def _add_indeed_manifest(
    objects: dict[str, bytes],
    *,
    stamp: str,
    day: str,
    fp: dict | None,
    rows: int,
) -> None:
    """Add one Indeed manifest and its published object for ``day``."""
    remote = f"dia=2026-09-{day}/jobs.parquet"
    data = _parquet_bytes(_INDEED_COLUMNS, _indeed_rows(rows))
    objects[f"indeed/{remote}"] = data
    objects[f"_manifests/indeed/{stamp}.json"] = _manifest_payload(
        [_ok_entry("jobs.parquet", remote, rows, _sha256(data))], fp=fp
    )


def _build_landing() -> dict[str, bytes]:
    """Build the landing shared by the integrated scenario.

    - Indeed: five comparable runs (fingerprint A) plus one run with a
      different fingerprint and one without a fingerprint, which are excluded.
    - LinkedIn: an empty ``OnlyNewOffers`` delta with a non-empty snapshot.
    - InfoJobs: a manifest whose published object is still pending.
    - Multi-site: a manifest that declared a bad file and was rejected.
    """
    objects: dict[str, bytes] = {}

    # Five comparable Indeed runs (the latest is the analysed one) plus two
    # candidates that must be excluded from the series.
    _add_indeed_manifest(
        objects, stamp="20260922T010000", day="22", fp=_fp_a(), rows=2
    )
    _add_indeed_manifest(
        objects, stamp="20260923T010000", day="23", fp=_fp_a(), rows=2
    )
    _add_indeed_manifest(
        objects, stamp="20260924T010000", day="24", fp=_fp_a(), rows=2
    )
    _add_indeed_manifest(
        objects, stamp="20260925T010000", day="25", fp=_fp_a(), rows=2
    )
    _add_indeed_manifest(
        objects, stamp="20260926T010000", day="26", fp=_fp_a(), rows=3
    )
    _add_indeed_manifest(
        objects, stamp="20260920T010000", day="20", fp=_fp_b(), rows=2
    )
    _add_indeed_manifest(
        objects, stamp="20260919T010000", day="19", fp=None, rows=2
    )

    # LinkedIn publishes only the new-offers delta; a zero-row delta is visible
    # but carries no offers. The local snapshot (built by _build_local_run) has
    # offers, so this must never be read as "zero offers captured".
    empty_delta = _parquet_bytes(_LINKEDIN_COLUMNS, [])
    objects["linkedin/dia=2026-09-26/jobs.parquet"] = empty_delta
    objects["_manifests/linkedin/20260926T010000.json"] = _manifest_payload(
        [_ok_entry("jobs.parquet", "dia=2026-09-26/jobs.parquet", 0, _sha256(empty_delta))]
    )

    # InfoJobs: the manifest resolves the published object as pending (the
    # remote path is not available yet).
    objects["_manifests/infojobs/20260926T010000.json"] = _manifest_payload(
        [
            {
                "file": "jobs.parquet",
                "status": "ok",
                "rows": None,
                "bytes": 0,
                "sha256": "",
                "converted": False,
                "error": "pendiente de publicar",
                "remote": None,
            }
        ]
    )

    # Multi-site: the manifest already rejected one file (bad_files > 0).
    objects["_manifests/multi_site/20260926T010000.json"] = _manifest_payload(
        [
            {
                "file": "jobs_unified.parquet",
                "status": "bad",
                "rows": None,
                "bytes": 0,
                "sha256": "",
                "converted": False,
                "error": "columnas obligatorias ausentes",
                "remote": "dia=2026-09-26/jobs_unified.parquet",
            }
        ],
        bad_files=1,
    )
    return objects


def _diagnose(root: Path, objects: dict[str, bytes]) -> report.DiagnosticReport:
    logs_dir = _build_local_run(root)
    return verify_run.run_diagnostic(root, logs_dir, reader=FakeReader(objects))


@pytest.fixture
def diagnostic(tmp_path: Path) -> report.DiagnosticReport:
    """Run the diagnostic once against the shared simulated landing."""
    return _diagnose(tmp_path, _build_landing())


def _source_report(
    diagnostic: report.DiagnosticReport, source_id: str
) -> report.SourceReport:
    return next(item for item in diagnostic.sources if item.source == source_id)


# --------------------------------------------------------------------------
# Valid published object (RF-6, RF-8)
# --------------------------------------------------------------------------


def test_valid_object_with_matching_rows_and_sha_is_published_ok(diagnostic):
    indeed = _source_report(diagnostic, "indeed")

    # The manifest declared 3 rows and the right checksum for a readable object.
    assert indeed.publication_state == publication.PUBLICATION_OK
    assert indeed.delta_offers == 3
    assert publication.is_zero_offers_captured(indeed.publication_state) is False


# --------------------------------------------------------------------------
# Rejected manifest (RF-8, RF-11)
# --------------------------------------------------------------------------


def test_rejected_manifest_is_not_declared_a_correct_publication(diagnostic):
    glassdoor = _source_report(diagnostic, "glassdoor")

    # Multi-site rejected a file (bad_files > 0 / status=bad), so its portals
    # are reported as rejected, never as a correct publication.
    assert glassdoor.publication_state == publication.PUBLICATION_REJECTED
    assert glassdoor.publication_state != publication.PUBLICATION_OK
    assert publication.is_zero_offers_captured(glassdoor.publication_state) is False


# --------------------------------------------------------------------------
# Empty delta with a non-empty snapshot (RF-3, RF-6)
# --------------------------------------------------------------------------


def test_empty_published_delta_never_proves_zero_offers(diagnostic):
    linkedin = _source_report(diagnostic, "linkedin")

    # The local snapshot has offers; the empty published delta must not be
    # turned into a false "zero offers captured" (RF-3).
    assert linkedin.obtained_offers == 2
    assert linkedin.publication_state != publication.PUBLICATION_EMPTY
    assert publication.is_zero_offers_captured(linkedin.publication_state) is False


def test_empty_delta_with_non_empty_snapshot_is_no_new_offers():
    # Pure rule of T-31: an explicitly measured empty delta with a non-empty
    # snapshot is `no_new_offers`, the only state that is *not* "zero offers
    # captured" while still being an empty delta.
    state = publication.classify_publication(
        obtained_offers=3, delta_offers=0, manifest_state=landing.STATE_OK
    )

    assert state == publication.PUBLICATION_NO_NEW_OFFERS
    assert publication.is_zero_offers_captured(state) is False


# --------------------------------------------------------------------------
# Pending publication (RF-8, RF-13)
# --------------------------------------------------------------------------


def test_pending_manifest_is_reported_as_pending_not_an_error(diagnostic):
    infojobs = _source_report(diagnostic, "infojobs")

    # The object is not published yet: pending, never an error and never zero.
    assert infojobs.publication_state == publication.PUBLICATION_PENDING
    assert publication.is_zero_offers_captured(infojobs.publication_state) is False


def test_missing_published_object_does_not_fail_the_source_or_claim_zero(tmp_path):
    # An Indeed manifest references an object that is not listed yet. Through
    # ``run_diagnostic`` the manifest check would be ``pending``, but today the
    # wiring loads the published delta afterwards and a ``RemoteError``
    # downgrades it to ``not_checked`` (reported; to be fixed in another spec,
    # not asserted as the correct state here). Whatever the wiring does, the
    # RF-8/RF-13 safety property must hold: the source is not failed and no
    # false zero is declared because a remote object is absent.
    key = "_manifests/indeed/20260926T010000.json"
    reader = FakeReader(
        {
            key: _manifest_payload(
                [_ok_entry("jobs.parquet", "dia=2026-09-26/jobs.parquet", 3, "")]
            )
        }
    )
    logs_dir = _build_local_run(tmp_path)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = _source_report(diagnostic, "indeed")

    assert indeed.state == status.SOURCE_CORRECT
    assert indeed.publication_state != publication.PUBLICATION_OK
    assert publication.is_zero_offers_captured(indeed.publication_state) is False


def test_unlisted_remote_object_is_pending_in_the_manifest_check(tmp_path):
    # Boundary proof for the specific RF-8 case: the manifest references an
    # object that is not listed yet, so the manifest check is pending and the
    # publication classifier maps that to pending (never to an error).
    key = "_manifests/infojobs/20260926T010000.json"
    remote = "dia=2026-09-26/jobs.parquet"
    reader = FakeReader(
        {
            key: _manifest_payload(
                [_ok_entry("jobs.parquet", remote, 3, "")],
            )
        }
    )
    manifest = landing.load_manifest(reader, key)
    assert manifest is not None

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)
    state = publication.classify_publication(
        obtained_offers=3,
        delta_offers=None,
        manifest_state=check.state,
        rejected=check.rejected,
    )

    assert check.state == landing.STATE_PENDING
    assert state == publication.PUBLICATION_PENDING
    assert publication.is_zero_offers_captured(state) is False


# --------------------------------------------------------------------------
# Comparable history (RF-7)
# --------------------------------------------------------------------------


def test_five_comparable_runs_are_used_and_incomparable_are_excluded(diagnostic):
    indeed = _source_report(diagnostic, "indeed")

    assert diagnostic.runs_used == 5
    assert indeed.trend is not None
    assert indeed.trend.runs_used == 5

    # A five-point series proves only the matching fingerprints entered it (the
    # two excluded candidates would have made it seven).
    title = indeed.trend.fields["title"]
    assert len(title.values) == 5
    assert len(title.counts) == 5

    # The exclusions are disclosed: a changed configuration and a manifest
    # without a fingerprint are never presented as comparable.
    note = diagnostic.trend_note or ""
    assert "configuraci" in note
    assert "huella" in note


# --------------------------------------------------------------------------
# Remote read unavailable (RF-8, RF-13)
# --------------------------------------------------------------------------


def test_remote_listing_failure_is_not_checked_without_failing_the_source(tmp_path):
    logs_dir = _build_local_run(tmp_path)
    reader = FakeReader(fail_list="sin credenciales")

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = _source_report(diagnostic, "indeed")

    # The publication could not be checked, but the source keeps its own state
    # (it is not failed because of a remote read failure).
    assert indeed.publication_state == publication.PUBLICATION_NOT_CHECKED
    assert indeed.state == status.SOURCE_CORRECT
    assert publication.is_zero_offers_captured(indeed.publication_state) is False
    assert diagnostic.runs_used == 0
