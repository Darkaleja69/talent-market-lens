"""Read-only remote landing access for the daily diagnostic.

This module is the Azure boundary of the diagnostic (plan section 4.2). It
uses the AzCopy binary already configured by the pipeline
(``scrapers-pipeline/config.ps1``) to **list** and **download** landing
objects into a private temporary directory. It never uploads, overwrites or
deletes anything in Azure: the diagnostic must not alter the publication
(RF-8, RF-12, constitution #5).

Design rules (RF-6, RF-8, RF-13):

- ``RemoteObject.path`` is the container-relative key (for example
  ``linkedin/dia=2026-09-26/jobs_20260926_010000.parquet``). It never carries
  the SAS token; neither does the SAS appear in ``repr``, error messages or
  logs.
- Listing and downloading are the only remote operations. A failure to reach
  Azure raises :class:`RemoteError`, so later tasks can distinguish "the
  publication could not be checked" from "the publication is wrong"
  (RF-8, RF-13).
- Downloads land under a lazily created temporary directory owned by the
  reader. ``close()`` removes *only* that directory and is idempotent, so no
  local copy survives the operation (constitution #5).

The pure parser (:func:`parse_azcopy_list`) and the environment helper
(:func:`base_url_from_env`) are separated from the adapter, and the command
runner is injectable, so every test runs offline with a fake runner and no
real AzCopy or credentials (plan section 8).
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from verification import completeness

DEFAULT_CONTAINER = "landing"
STORAGE_ACCOUNT_ENV = "LANDING_STORAGE_ACCOUNT"
SAS_TOKEN_ENV = "LANDING_SAS_TOKEN"

_AZURE_BLOB_SUFFIX = ".blob.core.windows.net"
_CONTENT_MARKER = "; Content Length:"
_LOG_PREFIX_RE = re.compile(r"^(?:INFO|WARN|WARNING|ERROR|DEBUG)\s*:\s*", re.IGNORECASE)
_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)$")
# Redacts the query string (where the SAS lives) when a piece of text such as
# azcopy's stderr has to be surfaced.
_QUERY_RE = re.compile(r"(?<=\?)[^\s\"']+")

# Binary/decimal size units azcopy prints in human-readable mode. A bare number
# (azcopy ``--machine-readable``) is treated as bytes.
_SIZE_UNITS: dict[str, int] = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}

# A runner takes the full argv and returns a subprocess-like result exposing
# ``returncode``/``stdout``/``stderr``. Tests inject a fake runner.
Runner = Callable[[list[str]], "subprocess.CompletedProcess"]

# --- File verification states (T-30; RF-6, RF-8) ----------------------------
#
# One state per published file, plus the aggregate manifest states. These are
# intentionally plain strings so the report layer can render them without an
# extra enum import.
FILE_OK = "ok"
FILE_MISSING = "not_found"  # the object is not visible in the landing
FILE_UNREADABLE = "unreadable"  # present but not a readable Parquet
FILE_ROWS_MISMATCH = "rows_mismatch"
FILE_CHECKSUM_MISMATCH = "checksum_mismatch"
FILE_REJECTED = "rejected"  # the manifest already marked the file as bad
FILE_UNVERIFIED = "unverified"  # nothing in the manifest to compare against

# Overall states of a manifest check.
STATE_OK = "ok"
STATE_PENDING = "pending"
STATE_MISMATCH = "mismatch"
STATE_REJECTED = "rejected"
STATE_UNVERIFIED = "unverified"

_MISMATCH_STATES = frozenset({FILE_ROWS_MISMATCH, FILE_CHECKSUM_MISMATCH})
# A drive-like fragment (``C:``), including Windows alternate-data-stream
# syntax, never belongs to a remote key mapped under a temporary directory.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class RemoteObject:
    """One object in the landing container.

    ``path`` is the container-relative key and never contains the SAS token;
    ``size`` is the object size in bytes when azcopy reported it.
    """

    path: str
    size: int | None = None


@dataclass(frozen=True)
class ManifestFile:
    """One file entry of a landing manifest (RF-6, RF-8).

    The manifest written by ``ensure_compatible.py`` uses the keys ``file``,
    ``status``, ``rows``, ``bytes``, ``sha256``, ``converted`` and ``error``;
    the upload wrapper adds ``remote`` (the container-relative key under the
    scraper, e.g. ``dia=2026-09-26/jobs.parquet``). Older manifests may lack
    ``remote`` or carry empty rows/sha256, so every optional field is read
    tolerantly.
    """

    file: str
    status: str = ""
    rows: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    remote: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Manifest:
    """Parsed landing manifest for one scraper and stamp (RF-6, RF-8).

    ``fingerprint`` is reserved for the comparability fingerprint of T-32: it
    is read when present and never required, so manifests written before that
    task remain usable (RF-7).
    """

    scraper: str
    stamp: str
    schema_version: int | None
    total_files: int | None
    bad_files: int | None
    files: tuple[ManifestFile, ...]
    fingerprint: dict | None = None


@dataclass(frozen=True)
class FileCheck:
    """Verification outcome of a single published file (RF-8)."""

    file: str
    remote: str | None
    state: str
    detail: str


@dataclass(frozen=True)
class ManifestCheck:
    """Verification outcome of one manifest and its published objects (RF-8).

    ``rejected`` is True when the manifest itself declared bad files (or any
    entry is marked ``bad``); ``state`` summarises the whole check.
    """

    scraper: str
    stamp: str
    rejected: bool
    files: tuple[FileCheck, ...]
    state: str


class RemoteError(RuntimeError):
    """Raised when a remote read (list or download) cannot be completed.

    Callers treat it as "the publication could not be checked" rather than as
    a publication failure (RF-8, RF-13). Its message never contains the SAS.
    """


@runtime_checkable
class RemoteReader(Protocol):
    """Stable read-only interface used by the publication checks.

    Later tasks (T-30, T-33) depend only on this contract, so tests can inject
    a fake reader and never touch Azure.
    """

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        """Return the remote objects whose key starts with ``prefix``."""
        ...

    def download(self, remote_path: str, local_path: Path) -> None:
        """Download ``remote_path`` to ``local_path``."""
        ...

    def close(self) -> None:
        """Release local resources (temporary downloads); idempotent."""
        ...


def _parse_size(raw: str) -> int | None:
    """Parse an azcopy ``Content Length`` value into bytes, or ``None``."""
    match = _SIZE_RE.match(raw.strip())
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    factor = _SIZE_UNITS.get(unit)
    if factor is None:
        return None
    return int(number * factor)


def parse_azcopy_list(output: str) -> list[RemoteObject]:
    """Parse the text output of ``azcopy list`` into :class:`RemoteObject`s.

    Assumed line format (azcopy's real text output)::

        INFO: <path>; Content Length: <number>[ <unit>]

    ``<path>`` is the container-relative key and ``<unit>`` is a human size
    unit (``B``, ``KiB``, ``MiB``, ...). A bare number, as produced by
    ``azcopy --machine-readable``, is read as bytes. A path may itself contain
    ``;``: the split is done on the ``; Content Length:`` marker, not on the
    first semicolon.

    Blank lines, azcopy banners (version/upgrade notices, ``Scanning...``),
    summary lines such as ``N objects listed`` and any other line without the
    marker are ignored. A malformed size still yields the object, with
    ``size=None``. The SAS token never appears in the listing output, so
    ``RemoteObject.path`` is always clean.
    """
    objects: list[RemoteObject] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or _CONTENT_MARKER not in line:
            continue
        path_part, _, size_part = line.partition(_CONTENT_MARKER)
        path = _LOG_PREFIX_RE.sub("", path_part.strip()).strip()
        if not path:
            continue
        objects.append(RemoteObject(path=path, size=_parse_size(size_part)))
    return objects


def base_url_from_env(
    account: str | None = None, container: str = DEFAULT_CONTAINER
) -> str:
    """Build the landing base URL from the environment, without the SAS.

    The storage account is read from ``LANDING_STORAGE_ACCOUNT`` when not
    passed explicitly; it is deliberately *not* versioned in the repository
    (see ``config.ps1``/``config.example.ps1``). The container defaults to
    ``landing``. The SAS token is read from ``LANDING_SAS_TOKEN`` at call time
    by the adapter and is never part of this URL, so the returned value is
    safe to log and to use in the comparability fingerprint (RF-7, T-32).
    """
    resolved = (
        account if account is not None else os.environ.get(STORAGE_ACCOUNT_ENV, "")
    ).strip()
    if not resolved:
        raise RemoteError(
            "Falta LANDING_STORAGE_ACCOUNT: no se puede construir la URL de la "
            "landing."
        )
    container_name = container.strip().strip("/") or DEFAULT_CONTAINER
    return f"https://{resolved}{_AZURE_BLOB_SUFFIX}/{container_name}"


def _default_runner(command: list[str]) -> subprocess.CompletedProcess:
    """Run a command capturing its output as text (the real AzCopy path)."""
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _safe_relative(remote_path: str) -> Path:
    """Map a remote key to a relative local path, rejecting escapes.

    A remote key is untrusted input (it comes from manifests and listings), so
    every segment is validated before it is joined under the temporary
    directory. Empty segments and ``.`` are dropped; a segment that is ``..``,
    contains a path separator, or looks like a Windows drive/ADS fragment
    (``C:``) is rejected, as is an empty result. This blocks traversal and the
    UNC/device forms that used to slip through a double leading slash.
    """
    normalized = remote_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError("Ruta remota no válida para descargar a un temporal.")
    for part in parts:
        if (
            part == ".."
            or "/" in part
            or "\\" in part
            or ":" in part
            or _DRIVE_RE.match(part)
        ):
            raise ValueError(
                "Ruta remota no válida para descargar a un temporal."
            )
    return Path(*parts)


def _is_within(path: Path, base: Path) -> bool:
    """Return True when ``path`` is ``base`` or lives inside it."""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


class AzCopyReader:
    """Read-only :class:`RemoteReader` backed by the pipeline's AzCopy.

    Only ``list`` and ``copy`` (download) are ever issued; there is no upload
    or delete path. Downloaded objects go to a private temporary directory
    that is removed by :meth:`close`. Supports the context-manager protocol.
    """

    def __init__(
        self,
        base_url: str,
        sas_token: str = "",
        *,
        azcopy_path: str = "azcopy",
        workdir: str | Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._sas_token = sas_token
        self._azcopy_path = azcopy_path
        self._workdir = Path(workdir) if workdir is not None else None
        self._runner: Runner = runner if runner is not None else _default_runner
        self._temp_dir: Path | None = None

    def __repr__(self) -> str:
        # The SAS is intentionally absent: never leak it through repr/debug.
        return (
            f"{type(self).__name__}(base_url={self._base_url!r}, "
            f"azcopy_path={self._azcopy_path!r})"
        )

    def __enter__(self) -> "AzCopyReader":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.close()
        return False

    @property
    def base_url(self) -> str:
        """The container URL without the SAS token."""
        return self._base_url

    # -- URL / error handling ------------------------------------------------

    def _with_sas(self, url: str) -> str:
        if not self._sas_token:
            return url
        separator = "" if self._sas_token.startswith("?") else "?"
        return f"{url}{separator}{self._sas_token}"

    def _url_for(self, remote_path: str) -> str:
        key = remote_path.strip().strip("/")
        url = f"{self._base_url}/{key}" if key else self._base_url
        return self._with_sas(url)

    def _sanitize(self, text: str) -> str:
        """Remove the SAS token (and any query string) from a message."""
        if self._sas_token:
            text = text.replace(self._sas_token, "***")
        return _QUERY_RE.sub("***", text)

    def _run(self, command: list[str], action: str) -> subprocess.CompletedProcess:
        try:
            result = self._runner(command)
        except Exception as exc:  # noqa: BLE001 - reported as a remote failure
            # Only the exception type is surfaced; its message could echo the
            # URL (and thus the SAS).
            raise RemoteError(
                f"No se pudo {action} en la landing ({type(exc).__name__})."
            ) from exc
        if result.returncode != 0:
            detail = ""
            stderr = (getattr(result, "stderr", "") or "").strip()
            if stderr:
                detail = self._sanitize(stderr.splitlines()[-1])
            suffix = f" Detalle: {detail}" if detail else ""
            raise RemoteError(
                f"No se pudo {action} en la landing (código "
                f"{result.returncode}).{suffix}"
            )
        return result

    # -- RemoteReader --------------------------------------------------------

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        """List landing objects under ``prefix`` via ``azcopy list``."""
        result = self._run(
            [self._azcopy_path, "list", self._url_for(prefix)],
            action="listar los objetos",
        )
        output = result.stdout or ""
        objects = parse_azcopy_list(output)
        if not objects:
            # Some AzCopy builds/log levels emit the listing on stderr.
            objects = parse_azcopy_list(getattr(result, "stderr", "") or "")
        return objects

    def download(self, remote_path: str, local_path: Path) -> None:
        """Download ``remote_path`` to ``local_path`` via ``azcopy copy``."""
        destination = Path(local_path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RemoteError(
                "No se pudo preparar el directorio temporal de descarga."
            ) from exc
        self._run(
            [
                self._azcopy_path,
                "copy",
                self._url_for(remote_path),
                str(destination),
            ],
            action="descargar el objeto",
        )

    # -- Temporary storage ---------------------------------------------------

    def _ensure_temp_dir(self) -> Path:
        if self._temp_dir is None or not self._temp_dir.is_dir():
            base_dir: str | None = None
            if self._workdir is not None:
                try:
                    self._workdir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise RemoteError(
                        "No se pudo crear el directorio de trabajo temporal."
                    ) from exc
                base_dir = str(self._workdir)
            self._temp_dir = Path(
                tempfile.mkdtemp(prefix="landing-read-", dir=base_dir)
            )
        return self._temp_dir

    @property
    def temp_dir(self) -> Path:
        """Return (creating on first use) this reader's temporary directory."""
        return self._ensure_temp_dir()

    def temp_path(self, remote_path: str) -> Path:
        """Return a safe local path for downloading ``remote_path``.

        The container-relative key is mapped under the reader's temporary
        directory. Empty keys, ``..`` segments, drive-like fragments and any
        key whose resolved path would leave the temporary directory are
        rejected, so a corrupt manifest can never write outside it.
        """
        temp_dir = self._ensure_temp_dir()
        candidate = temp_dir / _safe_relative(remote_path)
        if not _is_within(candidate.resolve(), temp_dir.resolve()):
            raise ValueError("Ruta remota no válida para descargar a un temporal.")
        return candidate

    def close(self) -> None:
        """Remove this reader's temporary directory; idempotent.

        Only the private directory created by this reader is removed; the
        optional ``workdir`` it was created under is preserved.
        """
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


# --- T-30: manifest parsing and publication checks (RF-6, RF-8) --------------
#
# The diagnostic reads the manifest the pipeline already writes
# (``_manifests/<scraper>/<stamp>.json``) and re-checks the published objects:
# existence, readability, row count and sha256. Parsing is pure so it can be
# tested without any remote. A failure to reach Azure raises
# :class:`RemoteError` and is *never* turned into a publication failure: the
# caller reports the publication as "not checked" (RF-8, RF-13).


def _as_int(value: object) -> int | None:
    """Return ``value`` as an int, or ``None`` (bools are not ints here)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _as_optional_str(value: object) -> str | None:
    """Return a stripped non-empty string, or ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_mapping(value: object) -> dict | None:
    """Return a shallow dict copy of a mapping, or ``None``."""
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _parse_manifest_file(item: Mapping) -> ManifestFile:
    """Build a :class:`ManifestFile` from one manifest entry, tolerantly."""
    file_name = item.get("file")
    status = item.get("status")
    return ManifestFile(
        file=file_name if isinstance(file_name, str) else "",
        status=status if isinstance(status, str) else "",
        rows=_as_int(item.get("rows")),
        bytes=_as_int(item.get("bytes")),
        sha256=_as_optional_str(item.get("sha256")),
        remote=_as_optional_str(item.get("remote")),
        error=_as_optional_str(item.get("error")),
    )


def parse_manifest(
    payload: object, *, scraper: str = "", stamp: str = ""
) -> Manifest | None:
    """Parse a manifest payload without raising.

    Returns ``None`` when the payload does not have the minimal expected shape,
    i.e. it is not a mapping or has no ``files`` list. Entries are read
    tolerantly: incomplete or mistyped optional fields become ``None``/empty,
    non-mapping entries are skipped. ``scraper`` and ``stamp`` are context the
    caller already knows (they are not stored inside the manifest body).
    """
    if not isinstance(payload, Mapping):
        return None
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return None
    files = tuple(
        _parse_manifest_file(item) for item in raw_files if isinstance(item, Mapping)
    )
    return Manifest(
        scraper=scraper,
        stamp=stamp,
        schema_version=_as_int(payload.get("schema_version")),
        total_files=_as_int(payload.get("total_files")),
        bad_files=_as_int(payload.get("bad_files")),
        files=files,
        fingerprint=_as_mapping(payload.get("fingerprint")),
    )


def _infer_scraper_stamp(remote_key: str) -> tuple[str, str]:
    """Infer ``(scraper, stamp)`` from a ``_manifests/<scraper>/<stamp>`` key."""
    parts = PurePosixPath(remote_key).parts
    if len(parts) >= 3 and parts[0] == "_manifests":
        return parts[-2], PurePosixPath(parts[-1]).stem
    return "", ""


def list_manifest_keys(reader: RemoteReader, scraper: str) -> list[str]:
    """List the manifest keys of ``scraper`` under ``_manifests/<scraper>/``.

    Only the read-only ``list_objects`` operation is used (RF-8).
    """
    prefix = f"_manifests/{scraper}/" if scraper else "_manifests/"
    return [obj.path for obj in reader.list_objects(prefix)]


def load_manifest(
    reader: RemoteReader,
    remote_key: str,
    *,
    scraper: str = "",
    stamp: str = "",
) -> Manifest | None:
    """Download and parse one manifest, returning ``None`` when unusable.

    The object is downloaded to a temporary location (the reader's own
    ``temp_path`` when available, otherwise a private temporary directory),
    read as UTF-8 JSON and parsed. Missing/invalid content yields ``None``; a
    connectivity/credential failure raises :class:`RemoteError` and is left to
    the caller as "not checked" (RF-8, RF-13).

    ``scraper``/``stamp`` default to the values inferred from the key.
    """
    inferred_scraper, inferred_stamp = _infer_scraper_stamp(remote_key)
    scraper = scraper or inferred_scraper
    stamp = stamp or inferred_stamp

    temp_path_attr = getattr(reader, "temp_path", None)
    own_dir: tempfile.TemporaryDirectory | None = None
    try:
        if callable(temp_path_attr):
            local = Path(temp_path_attr(remote_key))
        else:
            own_dir = tempfile.TemporaryDirectory(prefix="landing-manifest-")
            local = Path(own_dir.name) / _safe_relative(remote_key)
        reader.download(remote_key, local)
        payload = json.loads(local.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        if own_dir is not None:
            own_dir.cleanup()
    return parse_manifest(payload, scraper=scraper, stamp=stamp)


def _object_present(
    objects: list[RemoteObject], full_key: str, remote: str
) -> bool:
    """Return True when the exact published key appears in a listing.

    The exact container-relative key wins. As a fallback, ``azcopy list`` on
    some builds returns bare filenames relative to the requested prefix, so a
    key with no separator matching the remote file name also counts as
    present.
    """
    expected_name = PurePosixPath(remote).name
    for obj in objects:
        key = obj.path.strip().strip("/")
        if key == full_key:
            return True
        if "/" not in key and key == expected_name:
            return True
    return False


def _sha256_file(path: Path) -> str:
    """Return the lowercase sha256 hex digest of a local file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_entry(
    manifest: Manifest,
    entry: ManifestFile,
    reader: RemoteReader,
    base_dir: Path,
) -> FileCheck:
    """Verify one manifest entry against its published object (RF-8)."""
    if entry.status == "bad":
        detail = entry.error or "El manifest marcó este fichero como rechazado."
        return FileCheck(
            file=entry.file, remote=entry.remote, state=FILE_REJECTED, detail=detail
        )
    if not entry.remote:
        return FileCheck(
            file=entry.file,
            remote=None,
            state=FILE_MISSING,
            detail="No se puede localizar el objeto: falta la ruta remota.",
        )

    remote = entry.remote.lstrip("/")
    full_key = f"{manifest.scraper}/{remote}".strip("/") if manifest.scraper else remote
    directory = posixpath.dirname(remote)
    prefix_parts = [part for part in (manifest.scraper, directory) if part]
    prefix = f"{'/'.join(prefix_parts)}/" if prefix_parts else ""
    objects = reader.list_objects(prefix)
    if not _object_present(objects, full_key, remote):
        return FileCheck(
            file=entry.file,
            remote=remote,
            state=FILE_MISSING,
            detail="El objeto no aparece todavía en la landing.",
        )

    local = base_dir / _safe_relative(remote)
    reader.download(full_key, local)
    result = completeness.read_obtained_parquet(str(local))
    if not result.readable:
        return FileCheck(
            file=entry.file,
            remote=remote,
            state=FILE_UNREADABLE,
            detail=result.error or "El objeto no se puede leer como Parquet.",
        )
    if entry.rows is not None and result.rows != entry.rows:
        return FileCheck(
            file=entry.file,
            remote=remote,
            state=FILE_ROWS_MISMATCH,
            detail=f"Filas declaradas {entry.rows} != filas reales {result.rows}.",
        )
    if entry.sha256:
        actual = _sha256_file(local)
        if actual.lower() != entry.sha256.lower():
            return FileCheck(
                file=entry.file,
                remote=remote,
                state=FILE_CHECKSUM_MISMATCH,
                detail="El sha256 publicado no coincide con el objeto descargado.",
            )
    if entry.rows is None and not entry.sha256:
        return FileCheck(
            file=entry.file,
            remote=remote,
            state=FILE_UNVERIFIED,
            detail="El manifest no aporta filas ni sha256 para verificar el objeto.",
        )
    return FileCheck(
        file=entry.file,
        remote=remote,
        state=FILE_OK,
        detail="El objeto coincide con el manifest.",
    )


def _overall_state(checks: tuple[FileCheck, ...], rejected: bool) -> str:
    """Summarise the per-file states into one manifest state (RF-8)."""
    if rejected:
        return STATE_REJECTED
    states = {check.state for check in checks}
    if states & _MISMATCH_STATES:
        return STATE_MISMATCH
    if FILE_MISSING in states:
        return STATE_PENDING
    if not checks:
        return STATE_UNVERIFIED
    if states == {FILE_OK}:
        return STATE_OK
    # Present but unreadable, or with nothing to compare against: the
    # publication could not be confirmed.
    return STATE_UNVERIFIED


def verify_manifest(
    manifest: Manifest,
    reader: RemoteReader,
    *,
    workdir: str | Path | None = None,
) -> ManifestCheck:
    """Verify that a manifest's objects are published and consistent (RF-8).

    Every accepted entry is checked for existence, readability, declared row
    count and sha256; entries the manifest marked ``bad`` (or a ``bad_files``
    count above zero) are reported as rejected. When the manifest carries no
    rows/sha256 the corresponding comparison is skipped instead of inventing a
    mismatch.

    Downloads go to ``workdir`` when given; otherwise a private temporary
    directory is created and removed when the check ends. A :class:`RemoteError`
    (connectivity/credentials) propagates unchanged so the caller can report the
    publication as *not checked* rather than as wrong (RF-8, RF-13).
    """
    own_dir: tempfile.TemporaryDirectory | None = None
    if workdir is not None:
        base_dir = Path(workdir)
        base_dir.mkdir(parents=True, exist_ok=True)
    else:
        own_dir = tempfile.TemporaryDirectory(prefix="landing-verify-")
        base_dir = Path(own_dir.name)

    try:
        rejected = manifest.bad_files is not None and manifest.bad_files > 0
        checks: list[FileCheck] = []
        for entry in manifest.files:
            check = _verify_entry(manifest, entry, reader, base_dir)
            if check.state == FILE_REJECTED:
                rejected = True
            checks.append(check)
        frozen = tuple(checks)
        return ManifestCheck(
            scraper=manifest.scraper,
            stamp=manifest.stamp,
            rejected=rejected,
            files=frozen,
            state=_overall_state(frozen, rejected),
        )
    finally:
        if own_dir is not None:
            own_dir.cleanup()
