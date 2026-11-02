"""Local tests for the T-09 current mechanism parameters and stream summary.

No Spark and no Databricks: parameters, widgets and ``stream_result``.
"""
from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path

import pytest

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE))

# ``mechanism_current`` imports ``Upsert_Job_Offers`` at module level (in the
# demo both files are uploaded side by side). These tests only exercise the
# pure parameter resolution, so a stub keeps them independent from Spark and
# from ``databricks_notebooks``.
sys.modules.setdefault(
    "Upsert_Job_Offers", types.ModuleType("Upsert_Job_Offers")
)

import mechanism_current as mc  # noqa: E402


def _widgets(values):
    return lambda name: values.get(name)


def test_explicit_argument_wins_over_widget():
    calls = []

    def widget_getter(name):
        calls.append(name)
        return "from-widget"

    assert (
        mc._resolve("landing_root", "/explicit", widget_getter) == "/explicit"
    )
    assert calls == []


def test_widget_is_used_without_explicit_argument():
    getter = _widgets({"landing_root": "/from-widget"})

    assert mc._resolve("landing_root", None, getter) == "/from-widget"


def test_error_names_the_missing_parameter_and_how_to_pass_it():
    with pytest.raises(ValueError) as excinfo:
        mc._resolve("target_schema", None, _widgets({}))

    message = str(excinfo.value)
    assert "target_schema" in message
    assert "run(target_schema=...)" in message
    assert "dbutils.widgets.text('target_schema'" in message


def test_missing_widget_falls_through_to_the_error():
    def raising_widget_getter(name):
        raise KeyError(name)

    with pytest.raises(ValueError, match="landing_root"):
        mc._resolve("landing_root", None, raising_widget_getter)


def test_default_is_used_when_there_is_no_argument_nor_widget():
    assert (
        mc._resolve("schema_path", None, None, default="/landing/schema.json")
        == "/landing/schema.json"
    )


def test_module_is_a_library_and_does_not_run_on_import():
    source = (CANDIDATE / "mechanism_current.py").read_text(encoding="utf-8")

    assert '__name__ == "__main__"' not in source
    assert "spark.conf.get" not in source


def _install_sdk_stub(monkeypatch, dbutils):
    """Install a fake ``databricks.sdk.runtime`` that exposes ``dbutils``."""
    runtime = types.ModuleType("databricks.sdk.runtime")
    runtime.dbutils = dbutils
    sdk = types.ModuleType("databricks.sdk")
    sdk.runtime = runtime
    databricks = types.ModuleType("databricks")
    databricks.sdk = sdk
    monkeypatch.setitem(sys.modules, "databricks", databricks)
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)
    monkeypatch.setitem(sys.modules, "databricks.sdk.runtime", runtime)
    monkeypatch.delattr(builtins, "dbutils", raising=False)


def test_without_builtins_or_sdk_there_is_no_widget_getter(monkeypatch):
    monkeypatch.delattr(builtins, "dbutils", raising=False)
    monkeypatch.setitem(sys.modules, "databricks", None)
    monkeypatch.setitem(sys.modules, "databricks.sdk", None)
    monkeypatch.setitem(sys.modules, "databricks.sdk.runtime", None)

    assert mc._dbutils_widget_getter() is None


def test_sdk_widget_getter_returns_the_widget_value(monkeypatch):
    class _Widgets:
        def get(self, name):
            return {"landing_root": "/from-sdk-widget"}.get(name)

    _install_sdk_stub(monkeypatch, types.SimpleNamespace(widgets=_Widgets()))

    getter = mc._dbutils_widget_getter()
    assert getter is not None
    assert getter("landing_root") == "/from-sdk-widget"


def test_widgets_lookup_error_is_not_propagated(monkeypatch):
    class _FailingProxy:
        @property
        def widgets(self):
            raise RuntimeError("widgets unavailable")

    monkeypatch.setattr(builtins, "dbutils", _FailingProxy(), raising=False)

    assert mc._dbutils_widget_getter() is None


def test_widget_get_error_ends_in_the_clear_value_error(monkeypatch):
    class _Widgets:
        def get(self, name):
            raise RuntimeError(f"widget {name} not found")

    monkeypatch.setattr(
        builtins,
        "dbutils",
        types.SimpleNamespace(widgets=_Widgets()),
        raising=False,
    )

    with pytest.raises(ValueError, match="landing_root"):
        mc._resolve("landing_root", None, mc._dbutils_widget_getter())


def _fake_query(
    *,
    query_id="q-1",
    status="Stopped",
    last_progress=None,
    recent_progress=(),
):
    return types.SimpleNamespace(
        id=query_id,
        status={"message": status},
        lastProgress=last_progress,
        recentProgress=list(recent_progress),
    )


def test_stream_result_without_progress_reports_zero_rows_and_no_batch():
    # A query without ``lastProgress`` did not run any micro-batch.
    query = types.SimpleNamespace(
        id="q-1", status={"message": "Stopped"}, recentProgress=[]
    )

    result = mc.stream_result("indeed", query)

    assert result["num_input_rows"] == 0
    assert result["batch_id"] is None
    assert result["batches"] == 0


def test_stream_result_reports_an_empty_microbatch_with_its_batch_id():
    # An empty micro-batch ran: zero rows, but the engine assigned a batch id.
    progress = {"numInputRows": 0, "batchId": 0}
    query = _fake_query(last_progress=progress, recent_progress=[progress])

    result = mc.stream_result("indeed", query)

    assert result["num_input_rows"] == 0
    assert result["batch_id"] == 0
    assert result["batches"] == 1


def test_stream_result_reads_rows_and_batch_from_last_progress():
    progress = {"numInputRows": 89, "batchId": 3}
    query = _fake_query(
        last_progress=progress, recent_progress=[progress, progress]
    )

    result = mc.stream_result("indeed", query)

    assert result["num_input_rows"] == 89
    assert result["batch_id"] == 3
    assert result["batches"] == 2


def test_stream_result_keeps_source_query_id_and_status():
    query = _fake_query(query_id="abc-42", status="Stopped")

    result = mc.stream_result("infojobs", query)

    assert result["source"] == "infojobs"
    assert result["query_id"] == "abc-42"
    assert result["status"] == "Stopped"
