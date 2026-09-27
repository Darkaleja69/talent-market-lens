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

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

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


@dataclass(frozen=True)
class RemoteObject:
    """One object in the landing container.

    ``path`` is the container-relative key and never contains the SAS token;
    ``size`` is the object size in bytes when azcopy reported it.
    """

    path: str
    size: int | None = None


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

    Leading slashes and ``.`` segments are dropped; any ``..`` segment is
    rejected so a remote key can never write outside the temporary directory.
    """
    parts = [
        part
        for part in PurePosixPath(remote_path.replace("\\", "/")).parts
        if part not in ("", ".", "/")
    ]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(
            "Ruta remota no válida para descargar a un temporal."
        )
    return Path(*parts)


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
        directory. ``..`` segments and empty keys are rejected.
        """
        return self._ensure_temp_dir() / _safe_relative(remote_path)

    def close(self) -> None:
        """Remove this reader's temporary directory; idempotent.

        Only the private directory created by this reader is removed; the
        optional ``workdir`` it was created under is preserved.
        """
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
