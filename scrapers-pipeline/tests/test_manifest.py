"""Tests for manifest verification and published objects (T-30; RF-6, RF-8).

Azure is simulated in memory: a fake :class:`RemoteReader` serves objects from
a dict and materialises downloads into the local temporary directory. No
network, AzCopy or credentials are involved (plan section 8).

A Parquet payload is built with PyArrow (already justified in the plan,
section 6.3), mirroring ``test_completeness.py``.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import landing

BASE = "https://acct.blob.core.windows.net/landing"
SAS = "?sv=2024-01-01&sig=SUPERSECRET&se=2099-01-01"


class FakeRemote:
    """In-memory :class:`landing.RemoteReader` simulating the landing."""

    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        fail_list: str | None = None,
        fail_download: str | None = None,
    ) -> None:
        self.objects = dict(objects or {})
        self.fail_list = fail_list
        self.fail_download = fail_download
        self.closed = False
        self.downloads: list[str] = []

    def list_objects(self, prefix: str) -> list[landing.RemoteObject]:
        if self.fail_list is not None:
            raise landing.RemoteError(self.fail_list)
        return [
            landing.RemoteObject(path=key, size=len(data))
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def download(self, remote_path: str, local_path: Path) -> None:
        if self.fail_download is not None:
            raise landing.RemoteError(self.fail_download)
        self.downloads.append(remote_path)
        data = self.objects.get(remote_path)
        if data is None:
            raise landing.RemoteError("objeto no encontrado")
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    def close(self) -> None:
        self.closed = True


def _parquet_bytes(rows: int) -> bytes:
    table = pa.table({"job_key": [f"j{i}" for i in range(rows)]})
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(
    file: str,
    remote: str | None,
    *,
    status: str = "ok",
    rows: int | None = None,
    sha256: str | None = None,
    error: str | None = None,
) -> dict:
    return {
        "file": file,
        "status": status,
        "rows": rows,
        "bytes": 0,
        "sha256": sha256 or "",
        "converted": False,
        "error": error,
        "remote": remote,
    }


def _build(
    scraper: str,
    entries: list[dict],
    *,
    bad_files: int = 0,
    stamp: str = "20260926T010000",
) -> landing.Manifest:
    payload = {
        "schema_version": 1,
        "total_files": len(entries),
        "bad_files": bad_files,
        "files": entries,
    }
    manifest = landing.parse_manifest(payload, scraper=scraper, stamp=stamp)
    assert manifest is not None
    return manifest


# --------------------------------------------------------------------------
# parse_manifest (pure)
# --------------------------------------------------------------------------


def test_parse_manifest_full_entry():
    payload = {
        "schema_version": 1,
        "total_files": 1,
        "bad_files": 0,
        "files": [
            {
                "file": "jobs.parquet",
                "status": "ok",
                "rows": 12,
                "bytes": 345,
                "sha256": "ABC",
                "converted": True,
                "error": None,
                "remote": "dia=2026-09-26/jobs.parquet",
            }
        ],
        "fingerprint": {"sources": ["indeed"]},
    }

    manifest = landing.parse_manifest(payload, scraper="indeed", stamp="s1")

    assert manifest is not None
    assert manifest.scraper == "indeed"
    assert manifest.stamp == "s1"
    assert manifest.schema_version == 1
    assert manifest.total_files == 1
    assert manifest.bad_files == 0
    assert manifest.fingerprint == {"sources": ["indeed"]}
    assert manifest.files == (
        landing.ManifestFile(
            file="jobs.parquet",
            status="ok",
            rows=12,
            bytes=345,
            sha256="ABC",
            remote="dia=2026-09-26/jobs.parquet",
            error=None,
        ),
    )


def test_parse_manifest_is_tolerant_of_incomplete_entries():
    payload = {"files": [{"file": "a.parquet"}, "junk", 7, {"status": "bad"}]}

    manifest = landing.parse_manifest(payload)

    assert manifest is not None
    assert len(manifest.files) == 2
    assert manifest.files[0] == landing.ManifestFile(file="a.parquet")
    assert manifest.files[1].file == ""
    assert manifest.files[1].status == "bad"
    assert manifest.schema_version is None
    assert manifest.fingerprint is None


def test_parse_manifest_reads_fingerprint_if_present_without_requiring_it():
    payload = {"files": [], "fingerprint": {"runs": 5}}

    manifest = landing.parse_manifest(payload)

    assert manifest is not None
    assert manifest.fingerprint == {"runs": 5}
    assert manifest.files == ()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not json",
        [],
        5,
        {},
        {"files": "nope"},
        {"files": None},
        {"files": {"a": 1}},
    ],
)
def test_parse_manifest_returns_none_without_minimal_shape(payload):
    assert landing.parse_manifest(payload) is None


# --------------------------------------------------------------------------
# list_manifest_keys / load_manifest
# --------------------------------------------------------------------------


def test_list_manifest_keys_filters_by_scraper_prefix():
    reader = FakeRemote(
        {
            "_manifests/indeed/a.json": b"{}",
            "_manifests/indeed/b.json": b"{}",
            "_manifests/linkedin/c.json": b"{}",
        }
    )

    keys = landing.list_manifest_keys(reader, "indeed")

    assert keys == ["_manifests/indeed/a.json", "_manifests/indeed/b.json"]


def test_load_manifest_downloads_parses_and_infers_context(tmp_path):
    payload = {
        "schema_version": 1,
        "total_files": 1,
        "bad_files": 0,
        "files": [_entry("jobs.parquet", "dia=2026-09-26/jobs.parquet", rows=2)],
    }
    key = "_manifests/indeed/20260926T010000.json"
    reader = FakeRemote({key: json.dumps(payload).encode("utf-8")})

    manifest = landing.load_manifest(reader, key)

    assert manifest is not None
    assert manifest.scraper == "indeed"
    assert manifest.stamp == "20260926T010000"
    assert manifest.files[0].remote == "dia=2026-09-26/jobs.parquet"


def test_load_manifest_uses_reader_temp_path_when_available(tmp_path):
    payload = {"files": []}
    key = "_manifests/indeed/x.json"
    reader = landing.AzCopyReader(
        BASE, SAS, workdir=tmp_path, runner=_noop_runner
    )

    def fake_download(remote_path: str, local_path: Path) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(json.dumps(payload).encode())

    reader.download = fake_download  # type: ignore[method-assign]

    manifest = landing.load_manifest(reader, key)

    assert manifest is not None
    assert manifest.scraper == "indeed"
    reader.close()


def test_load_manifest_returns_none_on_invalid_json():
    key = "_manifests/indeed/x.json"
    reader = FakeRemote({key: b"{not valid json"})

    assert landing.load_manifest(reader, key) is None


def test_load_manifest_returns_none_on_non_mapping_json():
    key = "_manifests/indeed/x.json"
    reader = FakeRemote({key: b"[1, 2, 3]"})

    assert landing.load_manifest(reader, key) is None


def test_load_manifest_tolerates_utf8_bom():
    # PowerShell 5.1 ``Set-Content -Encoding UTF8`` writes a BOM into the
    # uploaded manifests; it must not turn a valid manifest into "not
    # checked" nor hide the comparable history (T-51; RF-7, RF-8).
    payload = {
        "schema_version": 1,
        "total_files": 1,
        "bad_files": 0,
        "files": [_entry("jobs.parquet", "dia=2026-09-26/jobs.parquet", rows=2)],
    }
    key = "_manifests/indeed/20260926T010000.json"
    reader = FakeRemote({key: ("\ufeff" + json.dumps(payload)).encode("utf-8")})

    manifest = landing.load_manifest(reader, key)

    assert manifest is not None
    assert manifest.scraper == "indeed"
    assert manifest.stamp == "20260926T010000"
    assert manifest.files[0].remote == "dia=2026-09-26/jobs.parquet"


def test_load_manifest_returns_none_on_invalid_json_with_bom():
    key = "_manifests/indeed/x.json"
    reader = FakeRemote({key: "\ufeff{not valid json".encode("utf-8")})

    assert landing.load_manifest(reader, key) is None


# --------------------------------------------------------------------------
# resolve_published_key (T-57; RF-6, RF-8)
# --------------------------------------------------------------------------


def test_resolve_published_key_prefers_exact_match():
    objects = [
        landing.RemoteObject("indeed/dia=2026-09-26/staging-a/offers.parquet"),
        landing.RemoteObject("indeed/dia=2026-09-26/offers.parquet"),
    ]

    resolved = landing.resolve_published_key(
        FakeRemote(), "indeed/dia=2026-09-26/offers.parquet", objects=objects
    )

    assert resolved == "indeed/dia=2026-09-26/offers.parquet"


def test_resolve_published_key_finds_a_unique_nested_name():
    nested = (
        "linkedin/dia=2026-09-30/"
        "scrapers-pipeline-linkedin-2026-09-30-639263290983170592/"
        "jobs_new_20260930_013818.parquet"
    )
    objects = [landing.RemoteObject(nested)]

    resolved = landing.resolve_published_key(
        FakeRemote(),
        "linkedin/dia=2026-09-30/jobs_new_20260930_013818.parquet",
        objects=objects,
    )

    assert resolved == nested


def test_resolve_published_key_does_not_guess_when_ambiguous():
    objects = [
        landing.RemoteObject("indeed/dia=2026-09-26/staging-a/offers.parquet"),
        landing.RemoteObject("indeed/dia=2026-09-26/staging-b/offers.parquet"),
    ]

    assert (
        landing.resolve_published_key(
            FakeRemote(), "indeed/dia=2026-09-26/offers.parquet", objects=objects
        )
        is None
    )


def test_resolve_published_key_returns_none_when_absent():
    objects = [landing.RemoteObject("indeed/dia=2026-09-26/other.parquet")]

    assert (
        landing.resolve_published_key(
            FakeRemote(), "indeed/dia=2026-09-26/offers.parquet", objects=objects
        )
        is None
    )


def test_resolve_published_key_lists_the_directory_prefix_when_objects_omitted():
    nested = "indeed/dia=2026-09-26/staging-a/offers.parquet"
    reader = FakeRemote({nested: _parquet_bytes(1)})

    resolved = landing.resolve_published_key(
        reader, "indeed/dia=2026-09-26/offers.parquet"
    )

    assert resolved == nested


# --------------------------------------------------------------------------
# verify_manifest
# --------------------------------------------------------------------------


def test_verify_manifest_ok_when_rows_and_checksum_match(tmp_path):
    data = _parquet_bytes(3)
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: data})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=3,
                sha256=_sha256(data),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.state == landing.STATE_OK
    assert check.rejected is False
    assert len(check.files) == 1
    assert check.files[0].state == landing.FILE_OK


def test_verify_manifest_downloads_the_exact_key_when_present(tmp_path):
    data = _parquet_bytes(3)
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: data})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=3,
                sha256=_sha256(data),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_OK
    assert reader.downloads == [key]


def test_verify_manifest_resolves_nested_staging_key(tmp_path):
    # AzCopy uploaded the file under an intermediate staging folder that the
    # manifest's ``remote`` does not carry: the object is still found by its
    # unique file name and the real key is what gets downloaded (T-57).
    data = _parquet_bytes(3)
    nested = (
        "linkedin/dia=2026-09-30/"
        "scrapers-pipeline-linkedin-2026-09-30-639263290983170592/"
        "jobs_new_20260930_013818.parquet"
    )
    reader = FakeRemote({nested: data})
    manifest = _build(
        "linkedin",
        [
            _entry(
                "jobs_new_20260930_013818.parquet",
                "dia=2026-09-30/jobs_new_20260930_013818.parquet",
                rows=3,
                sha256=_sha256(data),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.state == landing.STATE_OK
    assert check.files[0].state == landing.FILE_OK
    assert reader.downloads == [nested]


def test_verify_manifest_prefers_exact_key_over_nested_same_name(tmp_path):
    data = _parquet_bytes(3)
    exact = "indeed/dia=2026-09-26/offers.parquet"
    nested = "indeed/dia=2026-09-26/staging-a/offers.parquet"
    reader = FakeRemote({exact: data, nested: _parquet_bytes(9)})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=3,
                sha256=_sha256(data),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_OK
    assert reader.downloads == [exact]


def test_verify_manifest_ambiguous_same_name_is_missing_without_download(tmp_path):
    first = "indeed/dia=2026-09-26/staging-a/offers.parquet"
    second = "indeed/dia=2026-09-26/staging-b/offers.parquet"
    reader = FakeRemote({first: _parquet_bytes(1), second: _parquet_bytes(1)})
    manifest = _build(
        "indeed", [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")]
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_MISSING
    assert check.state == landing.STATE_PENDING
    assert reader.downloads == []


def test_verify_manifest_missing_object_is_pending(tmp_path):
    reader = FakeRemote({})
    manifest = _build(
        "indeed", [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")]
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.state == landing.STATE_PENDING
    assert check.files[0].state == landing.FILE_MISSING


def test_verify_manifest_corrupt_parquet_is_unreadable(tmp_path):
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: b"this is not a parquet file"})
    manifest = _build(
        "indeed", [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")]
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_UNREADABLE
    assert check.state != landing.STATE_OK


def test_verify_manifest_rows_mismatch(tmp_path):
    data = _parquet_bytes(3)
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: data})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=5,
                sha256=_sha256(data),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_ROWS_MISMATCH
    assert check.state == landing.STATE_MISMATCH


def test_verify_manifest_checksum_mismatch(tmp_path):
    data = _parquet_bytes(3)
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: data})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=3,
                sha256="deadbeef",
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_CHECKSUM_MISMATCH
    assert check.state == landing.STATE_MISMATCH


def test_verify_manifest_checksum_is_case_insensitive(tmp_path):
    data = _parquet_bytes(2)
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: data})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=2,
                sha256=_sha256(data).upper(),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_OK


def test_verify_manifest_rejects_manifest_with_bad_files(tmp_path):
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: _parquet_bytes(1)})
    manifest = _build(
        "indeed",
        [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")],
        bad_files=1,
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.rejected is True
    assert check.state == landing.STATE_REJECTED


def test_verify_manifest_rejects_entry_marked_bad(tmp_path):
    reader = FakeRemote({})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                status="bad",
                error="columnas obligatorias ausentes",
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.rejected is True
    assert check.state == landing.STATE_REJECTED
    assert check.files[0].state == landing.FILE_REJECTED
    assert check.files[0].detail == "columnas obligatorias ausentes"


def test_verify_manifest_entry_without_remote_is_missing(tmp_path):
    reader = FakeRemote({})
    manifest = _build("indeed", [_entry("offers.parquet", None)])

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_MISSING
    assert check.files[0].remote is None


def test_verify_manifest_without_rows_or_sha256_is_unverified(tmp_path):
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: _parquet_bytes(2)})
    manifest = _build(
        "indeed", [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")]
    )

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.files[0].state == landing.FILE_UNVERIFIED
    assert check.state == landing.STATE_UNVERIFIED


def test_verify_manifest_empty_manifest_is_unverified(tmp_path):
    reader = FakeRemote({})
    manifest = _build("indeed", [])

    check = landing.verify_manifest(manifest, reader, workdir=tmp_path)

    assert check.state == landing.STATE_UNVERIFIED
    assert check.files == ()


def test_verify_manifest_uses_own_temp_dir_when_workdir_is_none():
    data = _parquet_bytes(1)
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: data})
    manifest = _build(
        "indeed",
        [
            _entry(
                "offers.parquet",
                "dia=2026-09-26/offers.parquet",
                rows=1,
                sha256=_sha256(data),
            )
        ],
    )

    check = landing.verify_manifest(manifest, reader)

    assert check.state == landing.STATE_OK


def test_verify_manifest_propagates_remote_error_from_list(tmp_path):
    reader = FakeRemote(fail_list="sin credenciales")
    manifest = _build(
        "indeed", [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")]
    )

    with pytest.raises(landing.RemoteError):
        landing.verify_manifest(manifest, reader, workdir=tmp_path)


def test_verify_manifest_propagates_remote_error_from_download(tmp_path):
    key = "indeed/dia=2026-09-26/offers.parquet"
    reader = FakeRemote({key: _parquet_bytes(1)}, fail_download="sin conexion")
    manifest = _build(
        "indeed", [_entry("offers.parquet", "dia=2026-09-26/offers.parquet")]
    )

    with pytest.raises(landing.RemoteError):
        landing.verify_manifest(manifest, reader, workdir=tmp_path)


def test_load_manifest_propagates_remote_error(tmp_path):
    key = "_manifests/indeed/x.json"
    reader = FakeRemote({}, fail_download="sin credenciales")

    with pytest.raises(landing.RemoteError):
        landing.load_manifest(reader, key)


# --------------------------------------------------------------------------
# _safe_relative / temp_path hardening (regression for T-29 finding)
# --------------------------------------------------------------------------


def _noop_runner(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, 0, "", "")


def _make_reader(tmp_path: Path) -> landing.AzCopyReader:
    return landing.AzCopyReader(
        BASE, SAS, workdir=tmp_path, runner=_noop_runner
    )


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "..",
        "../escape.parquet",
        "..\\escape.parquet",
        "foo/../../bar.parquet",
        "C:\\Windows\\evil.txt",
        "C:/Windows/evil.txt",
        "a/C:/b.txt",
    ],
)
def test_temp_path_rejects_escaping_keys(tmp_path, bad_key):
    reader = _make_reader(tmp_path)
    try:
        with pytest.raises(ValueError):
            reader.temp_path(bad_key)
    finally:
        reader.close()


@pytest.mark.parametrize(
    "unc_key",
    [
        "//server/share/evil.txt",
        "\\\\server\\share\\evil.txt",
        "///server/share/evil.txt",
        "/etc/passwd",
    ],
)
def test_temp_path_unc_and_absolute_keys_stay_inside_temp(tmp_path, unc_key):
    reader = _make_reader(tmp_path)
    try:
        candidate = reader.temp_path(unc_key)

        # The resolved path never leaves the reader's private temporary
        # directory, whatever the leading separators were.
        candidate.resolve().relative_to(reader.temp_dir.resolve())
    finally:
        reader.close()


def test_verify_manifest_rejects_escaping_remote_key(tmp_path):
    # A corrupt manifest with a traversal key must not write outside the temp.
    full_key = "indeed/../../evil.parquet"
    reader = FakeRemote({full_key: _parquet_bytes(1)})
    manifest = _build(
        "indeed", [_entry("evil.parquet", "../../evil.parquet")]
    )

    with pytest.raises(ValueError):
        landing.verify_manifest(manifest, reader, workdir=tmp_path)