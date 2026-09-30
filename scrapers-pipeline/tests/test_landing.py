"""Tests for the read-only remote landing adapter (T-29; RF-6, RF-8, RF-13).

AzCopy is never executed: a fake runner records the built commands and, for
downloads, materialises the destination file. No network and no credentials
are involved.

The SAS token is synthetic; several tests assert it never leaks into
representations or error messages.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from verification import landing

BASE = "https://acct.blob.core.windows.net/landing"
SAS = "?sv=2024-01-01&sig=SUPERSECRET&se=2099-01-01"


class RecordingRunner:
    """Fake command runner that records argv and returns a canned result."""

    def __init__(
        self,
        stdout: str = "",
        returncode: int = 0,
        stderr: str = "",
        on_call=None,
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.on_call = on_call
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess:
        self.commands.append(list(command))
        if self.on_call is not None:
            self.on_call(command)
        return subprocess.CompletedProcess(
            command, self.returncode, self.stdout, self.stderr
        )


# --------------------------------------------------------------------------
# parse_azcopy_list (pure parsing)
# --------------------------------------------------------------------------


def test_parse_azcopy_list_normal_output():
    output = (
        "INFO: linkedin/dia=2026-09-26/jobs_20260926_010000.parquet; "
        "Content Length: 1.20 KiB\n"
        "INFO: linkedin/dia=2026-09-26/jobs_20260926_020000.parquet; "
        "Content Length: 2 MiB\n"
    )

    objects = landing.parse_azcopy_list(output)

    assert [o.path for o in objects] == [
        "linkedin/dia=2026-09-26/jobs_20260926_010000.parquet",
        "linkedin/dia=2026-09-26/jobs_20260926_020000.parquet",
    ]
    assert objects[0].size == int(1.20 * 1024)
    assert objects[1].size == 2 * 1024 * 1024


def test_parse_azcopy_list_machine_readable_bytes():
    objects = landing.parse_azcopy_list(
        "INFO: indeed/dia=2026-09-26/offers.parquet; Content Length: 4096"
    )

    assert objects == [landing.RemoteObject(path="indeed/dia=2026-09-26/offers.parquet", size=4096)]


def test_parse_azcopy_list_ignores_noise_empty_and_summary_lines():
    output = (
        "INFO: Scanning...\n"
        "\n"
        "INFO: azcopy 10.19.0: A newer version is available to download\n"
        "   \n"
        "INFO: linkedin/file.parquet; Content Length: 10 B\n"
        "INFO: 1 object listed\n"
        "some malformed line without the marker\n"
        "INFO: broken.parquet; Content Length: not-a-size\n"
    )

    objects = landing.parse_azcopy_list(output)

    assert len(objects) == 2
    assert objects[0] == landing.RemoteObject(path="linkedin/file.parquet", size=10)
    # A malformed size still yields the object, but without a size.
    assert objects[1].path == "broken.parquet"
    assert objects[1].size is None


def test_parse_azcopy_list_keeps_semicolons_in_path():
    objects = landing.parse_azcopy_list(
        "INFO: odd/na;me.parquet; Content Length: 5 B\n"
    )

    assert objects == [landing.RemoteObject(path="odd/na;me.parquet", size=5)]


def test_parse_azcopy_list_empty_output():
    assert landing.parse_azcopy_list("") == []


# --------------------------------------------------------------------------
# AzCopyReader: command construction and parsing
# --------------------------------------------------------------------------


def test_list_objects_builds_command_and_parses_output():
    runner = RecordingRunner(stdout="INFO: x.parquet; Content Length: 7 B\n")
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    objects = reader.list_objects("linkedin/dia=2026-09-26/")

    assert runner.commands == [
        ["azcopy", "list", BASE + "/linkedin/dia=2026-09-26" + SAS]
    ]
    # AzCopy 10.32.4 lists names relative to the prefix: the adapter joins
    # the requested prefix so the key is container-relative (T-50).
    assert objects == [
        landing.RemoteObject(path="linkedin/dia=2026-09-26/x.parquet", size=7)
    ]


def test_list_objects_with_empty_prefix_uses_container_url():
    runner = RecordingRunner(stdout="INFO: a.parquet; Content Length: 3 B\n")
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    objects = reader.list_objects("")

    assert runner.commands[0][2] == BASE + SAS
    # With no prefix there is nothing to join: paths stay exactly as listed.
    assert objects == [landing.RemoteObject("a.parquet", 3)]


def test_list_objects_uses_configured_azcopy_path():
    runner = RecordingRunner()
    reader = landing.AzCopyReader(
        BASE, SAS, azcopy_path=r"C:\Tools\azcopy\azcopy.exe", runner=runner
    )

    reader.list_objects("linkedin")

    assert runner.commands[0][0] == r"C:\Tools\azcopy\azcopy.exe"


def test_list_objects_falls_back_to_stderr_listing():
    # Some AzCopy builds emit the listing on stderr; the relative names are
    # completed with the requested prefix just like stdout listings (T-50).
    runner = RecordingRunner(
        stderr="INFO: 20260928_051557.json; Content Length: 3 B\n"
    )
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    assert reader.list_objects("_manifests/indeed/") == [
        landing.RemoteObject("_manifests/indeed/20260928_051557.json", 3)
    ]


def test_list_objects_prefixes_short_names_from_azcopy_1032():
    # Regression: AzCopy 10.32.4 lists bare names relative to the requested
    # prefix; they must become container-relative keys so the manifest and
    # trend downloads use them directly (RF-6, RF-7, RF-8).
    runner = RecordingRunner(
        stdout=(
            "INFO: 20260928_051557.json; Content Length: 1.20 KiB\n"
            "INFO: 20260928_061557.json; Content Length: 4096\n"
        )
    )
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    assert reader.list_objects("_manifests/indeed/") == [
        landing.RemoteObject(
            path="_manifests/indeed/20260928_051557.json",
            size=int(1.20 * 1024),
        ),
        landing.RemoteObject(
            path="_manifests/indeed/20260928_061557.json", size=4096
        ),
    ]


def test_list_objects_keeps_full_keys_without_duplicating_prefix():
    runner = RecordingRunner(
        stdout=(
            "INFO: _manifests/indeed/20260928_051557.json; "
            "Content Length: 3 B\n"
            "INFO: _manifests/indeed; Content Length: 3 B\n"
        )
    )
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    assert reader.list_objects("_manifests/indeed/") == [
        landing.RemoteObject("_manifests/indeed/20260928_051557.json", 3),
        landing.RemoteObject("_manifests/indeed", 3),
    ]


def test_list_manifest_keys_with_azcopy_reader_returns_full_keys():
    runner = RecordingRunner(
        stdout="INFO: 20260928_051557.json; Content Length: 3 B\n"
    )
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    keys = landing.list_manifest_keys(reader, "indeed")

    assert keys == ["_manifests/indeed/20260928_051557.json"]


def test_list_failure_raises_remote_error():
    runner = RecordingRunner(returncode=1, stderr="ERROR: something failed")
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    with pytest.raises(landing.RemoteError):
        reader.list_objects("linkedin")


# --------------------------------------------------------------------------
# AzCopyReader: download
# --------------------------------------------------------------------------


def test_download_builds_command_and_writes_temporary_file(tmp_path):
    def on_call(command: list[str]) -> None:
        if command[1] == "copy":
            destination = Path(command[3])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"parquet-bytes")

    runner = RecordingRunner(on_call=on_call)
    reader = landing.AzCopyReader(BASE, SAS, workdir=tmp_path, runner=runner)
    local = reader.temp_path("linkedin/dia=2026-09-26/jobs.parquet")

    reader.download("linkedin/dia=2026-09-26/jobs.parquet", local)

    assert runner.commands == [
        [
            "azcopy",
            "copy",
            BASE + "/linkedin/dia=2026-09-26/jobs.parquet" + SAS,
            str(local),
        ]
    ]
    assert local.read_bytes() == b"parquet-bytes"
    assert tmp_path in local.parents
    reader.close()


def test_download_failure_raises_remote_error(tmp_path):
    runner = RecordingRunner(returncode=1, stderr="ERROR: copy failed")
    reader = landing.AzCopyReader(BASE, SAS, workdir=tmp_path, runner=runner)

    with pytest.raises(landing.RemoteError):
        reader.download("a/b.parquet", tmp_path / "b.parquet")


def test_temp_path_rejects_traversal(tmp_path):
    reader = landing.AzCopyReader(BASE, SAS, workdir=tmp_path, runner=RecordingRunner())

    with pytest.raises(ValueError):
        reader.temp_path("../escape.parquet")

    reader.close()


# --------------------------------------------------------------------------
# AzCopyReader: temporary-directory lifecycle
# --------------------------------------------------------------------------


def test_close_removes_temp_dir_and_is_idempotent(tmp_path):
    reader = landing.AzCopyReader(BASE, SAS, workdir=tmp_path, runner=RecordingRunner())
    temp = reader.temp_dir
    assert temp.is_dir()
    (temp / "downloaded.parquet").write_text("x", encoding="utf-8")

    reader.close()

    assert not temp.exists()
    # The workdir that hosted the temp directory is preserved.
    assert tmp_path.is_dir()
    reader.close()  # second close is a no-op, never raises


def test_close_without_download_is_safe(tmp_path):
    reader = landing.AzCopyReader(BASE, SAS, workdir=tmp_path, runner=RecordingRunner())

    reader.close()
    reader.close()
    # No temporary directory was created, so nothing is left behind.
    assert list(tmp_path.iterdir()) == []


def test_context_manager_cleans_temp_dir(tmp_path):
    with landing.AzCopyReader(
        BASE, SAS, workdir=tmp_path, runner=RecordingRunner()
    ) as reader:
        temp = reader.temp_dir
        assert temp.is_dir()

    assert not temp.exists()


# --------------------------------------------------------------------------
# SAS never leaks
# --------------------------------------------------------------------------


def test_reader_repr_does_not_leak_sas():
    reader = landing.AzCopyReader(BASE, SAS, runner=RecordingRunner())

    representation = repr(reader)

    assert SAS not in representation
    assert "SUPERSECRET" not in representation


def test_error_message_does_not_leak_sas():
    runner = RecordingRunner(returncode=1, stderr="ERROR fetching " + BASE + SAS)
    reader = landing.AzCopyReader(BASE, SAS, runner=runner)

    with pytest.raises(landing.RemoteError) as excinfo:
        reader.list_objects("linkedin")

    message = str(excinfo.value)
    assert SAS not in message
    assert "SUPERSECRET" not in message


def test_runner_exception_does_not_leak_sas():
    def boom(command: list[str]) -> subprocess.CompletedProcess:
        raise OSError("cannot start " + BASE + SAS)

    reader = landing.AzCopyReader(BASE, SAS, runner=boom)

    with pytest.raises(landing.RemoteError) as excinfo:
        reader.list_objects("linkedin")

    assert "SUPERSECRET" not in str(excinfo.value)


def test_remote_object_never_contains_sas():
    objects = landing.parse_azcopy_list("INFO: a.parquet; Content Length: 1 B\n")

    assert SAS not in objects[0].path


# --------------------------------------------------------------------------
# base_url_from_env
# --------------------------------------------------------------------------


def test_base_url_from_env_defaults(monkeypatch):
    monkeypatch.setenv(landing.STORAGE_ACCOUNT_ENV, "myaccount")
    monkeypatch.setenv(landing.SAS_TOKEN_ENV, SAS)

    url = landing.base_url_from_env()

    assert url == "https://myaccount.blob.core.windows.net/landing"
    # The SAS is read elsewhere; it is never part of the base URL/fingerprint.
    assert "SUPERSECRET" not in url


def test_base_url_from_env_custom_container(monkeypatch):
    monkeypatch.setenv(landing.STORAGE_ACCOUNT_ENV, "acct")

    assert (
        landing.base_url_from_env(container="bronze")
        == "https://acct.blob.core.windows.net/bronze"
    )


def test_base_url_from_env_explicit_account_overrides_env(monkeypatch):
    monkeypatch.setenv(landing.STORAGE_ACCOUNT_ENV, "fromenv")

    assert (
        landing.base_url_from_env(account="explicit")
        == "https://explicit.blob.core.windows.net/landing"
    )


def test_base_url_from_env_missing_account_raises(monkeypatch):
    monkeypatch.delenv(landing.STORAGE_ACCOUNT_ENV, raising=False)

    with pytest.raises(landing.RemoteError):
        landing.base_url_from_env()


# --------------------------------------------------------------------------
# Interface stability for later tasks
# --------------------------------------------------------------------------


def test_azcopy_reader_satisfies_remote_reader_protocol():
    reader = landing.AzCopyReader(BASE, SAS, runner=RecordingRunner())

    assert isinstance(reader, landing.RemoteReader)
