"""Tests for the local Structured Streaming lab (spec 011, T-02).

The five scenarios run on a ``local[2]`` session and write only under
pytest's temporary directories. One module-scoped ``run_all`` feeds all the
scenario tests and the final timing table used by ``design-streaming.md``
section 5, so each scenario runs exactly once per test module.

``conftest.py`` calls ``configure_hadoop_home()`` when the test directory is
imported, before any test module or SparkSession exists; the JVM caches
``hadoop.home.dir`` at startup. The fixture requests ``local[2]``, but
``getOrCreate()`` may reuse a session created by another test module
(possibly ``local[1]``); the lab's semantics do not depend on the number of
cores. When the host cannot write with Hadoop (Windows without winutils),
every write test skips with instructions from ``require_writable_local_fs``.
"""
import json
import os
import sys
import tempfile
import time

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F841

from pyspark.sql import SparkSession  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import streaming_lab as lab  # noqa: E402
from _local_spark import (  # noqa: E402
    require_writable_local_fs,
    restore_shared_session,
    snapshot_shared_session,
)


# ---------------------------------------------------------------------------
# SparkSession for the lab: requests local[2], no UI, fixed UTC session zone.
# Same Windows-friendly configuration as test_upsert_job_offers.py, without
# Delta (plan.md section 5 excludes it).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def spark():
    """Create (or reuse) the lab session and restore its time zone.

    The fixture pins UTC and stops the session in its teardown, so the zone
    it sets does not reach the next module (the inheritance documented in
    ``SHARED_SESSION_KEYS`` comes from a module that leaves it running).
    HADOOP_HOME is configured by ``conftest.py`` at import time, before any
    SparkSession exists, so this fixture does not touch the environment.
    """
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-")
    ipv4 = "-Djava.net.preferIPv4Stack=true"
    # getOrCreate copies the runtime SQL options of this builder (the two
    # shuffle partitions) into the session of an earlier module; record what
    # that session had before the copy.
    previous_config = snapshot_shared_session(SparkSession.getActiveSession())
    session = (
        SparkSession.builder
        .master("local[2]")
        .appName("streaming-lab-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.extraJavaOptions", ipv4)
        .config("spark.executor.extraJavaOptions", ipv4)
        .config("spark.python.worker.timeout", "180")
        .config("spark.network.timeout", "300")
        .config("spark.executorEnv.PYSPARK_PYTHON", sys.executable)
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    # getOrCreate may return a session created by another test module, where
    # builder configs no longer apply: save and set the time zone explicitly.
    previous_time_zone = session.conf.get("spark.sql.session.timeZone", None)
    session.conf.set("spark.sql.session.timeZone", "UTC")
    # Warm-up: force Python workers to start and retry the known transient
    # handshake failure on Windows/local.
    for _ in range(3):
        try:
            session.sparkContext.parallelize(range(8), 2).map(
                lambda value: value
            ).count()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    try:
        yield session
    finally:
        if previous_time_zone is not None:
            session.conf.set(
                "spark.sql.session.timeZone", previous_time_zone
            )
        # Put the settings captured in ``previous_config`` back where they
        # were found, and before the ``stop()`` below on purpose. The fixture
        # stops the session right away, so the next module creates a new one
        # anyway: this is belt and braces, it preserves the invariant should
        # someone remove the ``stop()`` or reorder the teardown.
        restore_shared_session(session, previous_config)
        session.stop()


@pytest.fixture(scope="module")
def lab_results(spark, tmp_path_factory):
    """Run the five scenarios once; every test asserts on its own slice."""
    require_writable_local_fs(spark)
    base = tmp_path_factory.mktemp("streaming-lab")
    return lab.run_all(spark, base)


# ---------------------------------------------------------------------------
# One test per scenario, with assertions on the observed numbers.
# ---------------------------------------------------------------------------

def test_checkpoint_restart(spark, lab_results):
    require_writable_local_fs(spark)
    result = lab_results["scenarios"]["checkpoint_restart"]

    assert result["run1_rows"] == 2, (
        "la primera ejecución debe ingerir las 2 filas iniciales"
    )
    assert result["run2_rows"] == 1, (
        "la segunda ejecución debe procesar solo la fila nueva"
    )
    assert result["sink_rows"] == 3, (
        "el sumidero debe acumular 3 filas sin reprocesar las 2 primeras"
    )
    assert result["run1_batch_id"] == 0, (
        "la primera ejecución debe usar el batch 0"
    )
    assert result["run2_batch_id"] == result["run1_batch_id"] + 1, (
        "el batchId debe continuar después del reinicio"
    )
    assert json.dumps(result), (
        "el resultado del escenario debe ser serializable a JSON"
    )


def test_microbatch_triggers(spark, lab_results):
    require_writable_local_fs(spark)
    result = lab_results["scenarios"]["microbatch_triggers"]

    available = result["available_now"]
    assert available["batches"] == 3, (
        "availableNow debe generar un microbatch por fichero con "
        "maxFilesPerTrigger=1"
    )
    assert available["rows"] == 3, (
        "availableNow debe procesar las 3 filas disponibles"
    )
    assert available["sink_rows"] == 3, (
        "el sumidero de availableNow debe tener 3 filas"
    )

    once = result["once"]
    assert once["batches"] == 1, (
        "once debe ejecutar un único microbatch"
    )
    assert once["rows"] == 1, (
        "la primera ejecución once debe procesar el único fichero presente"
    )
    assert once["pending_after_first"] == 2, (
        "deben quedar 2 ficheros pendientes tras la primera ejecución once"
    )
    assert once["second_batches"] == 1, (
        "la segunda ejecución once también debe ser un único microbatch"
    )
    # Spark 4.2 reads all pending files in the second batch; a runtime that
    # honours maxFilesPerTrigger would need another run. The test does not
    # fix the number of files per batch, only that pending files advance and
    # drain.
    assert 1 <= once["second_rows"] <= once["pending_after_first"], (
        "la segunda ejecución once debe procesar parte de lo pendiente"
    )
    assert (
        once["pending_after_second"]
        == once["pending_after_first"] - once["second_rows"]
    ), "lo pendiente tras la segunda ejecución debe cuadrar con lo procesado"
    assert once["pending_after_drain"] == 0, (
        "el checkpoint debe drenar los ficheros pendientes tras las "
        "ejecuciones acotadas"
    )
    assert once["runs_to_drain"] >= 2, (
        "debe constar al menos la primera y la segunda ejecución once"
    )
    assert once["sink_rows"] == 3, (
        "el sumidero de once debe acabar con las 3 filas"
    )

    processing = result["processing_time"]
    assert processing["alive_after_first_batch"] is True, (
        "processingTime debe seguir vivo tras el primer batch"
    )
    assert processing["picked_up_added_file"] is True, (
        "processingTime debe recoger el fichero añadido mientras corre"
    )
    assert processing["batches"] >= 2, (
        "processingTime debe acumular al menos 2 microbatches"
    )
    assert processing["rows"] >= 2, (
        "processingTime debe procesar al menos 2 filas"
    )
    assert processing["sink_rows"] == 4, (
        "el sumidero de processingTime debe tener 4 filas"
    )
    assert json.dumps(result), (
        "el resultado del escenario debe ser serializable a JSON"
    )


def test_watermark_late_event(spark, lab_results):
    require_writable_local_fs(spark)
    result = lab_results["scenarios"]["watermark_late_event"]

    assert result["windows_run1"] == 2, (
        "la primera ejecución debe emitir 2 ventanas"
    )
    assert result["windows_run2"] == 3, (
        "la segunda ejecución debe dejar 3 ventanas en el sumidero"
    )
    assert result["new_windows_run2"] == 1, (
        "la segunda ejecución debe añadir solo la ventana de 12:20"
    )
    assert result["late_event_window"]["present"] is False, (
        "la ventana tardía de 11:50 no debe aparecer en el sumidero"
    )
    assert "2026-10-01 11:50" not in result["windows"], (
        "ninguna ventana debe empezar antes del watermark restaurado"
    )
    assert all(
        window_start >= "2026-10-01 12:00"
        for window_start in result["windows"]
    ), "las ventanas deben empezar a las 12:00 o después"
    # The late event is evidenced by the absence of the 11:50 window;
    # numRowsDroppedByWatermark is the engine counter and is 0 here.
    assert result["state_operators"], (
        "stateOperators debe copiarse de lastProgress"
    )
    assert any(
        operator.get("operatorName")
        for operator in result["state_operators"]
    ), "cada stateOperator debe conservar su operatorName"
    assert "numRowsTotal" in result["state_operators"][0], (
        "stateOperators debe conservar los campos de lastProgress"
    )
    assert json.dumps(result), (
        "el resultado del escenario debe ser serializable a JSON"
    )


def test_exactly_once(spark, lab_results):
    require_writable_local_fs(spark)
    result = lab_results["scenarios"]["exactly_once"]

    assert result["with_checkpoint_rows"] == 3, (
        "con checkpoint el sumidero debe tener 3 filas"
    )
    assert result["second_run_rows"] == 0, (
        "la segunda ejecución con checkpoint no debe reprocesar"
    )
    assert result["rows_after_second_run"] == 3, (
        "el sumidero no debe crecer en la segunda ejecución con checkpoint"
    )
    assert result["without_checkpoint_rows"] == 6, (
        "sin checkpoint el mismo lote debe duplicarse hasta 6 filas"
    )
    assert result["reprocessed_rows"] == 3, (
        "sin checkpoint deben reprocesarse las 3 filas del lote"
    )
    assert result["upsert_rows"] == 3, (
        "el upsert por clave debe mantener 3 filas"
    )
    assert result["duplicates_without_checkpoint"] == 3, (
        "deben aparecer 3 duplicados al borrar el checkpoint"
    )
    assert json.dumps(result), (
        "el resultado del escenario debe ser serializable a JSON"
    )


def test_foreach_batch_upsert(spark, lab_results):
    require_writable_local_fs(spark)
    result = lab_results["scenarios"]["foreach_batch_upsert"]

    assert result["append_rows"] == 6, (
        "append debe duplicar el microbatch: 3 filas por ejecución"
    )
    assert result["upsert_rows"] == 2, (
        "el upsert debe dejar una fila por clave"
    )
    assert result["distinct_keys"] == 2, (
        "deben quedar 2 claves distintas"
    )
    assert result["latest_values"] == {"1": "new", "2": "only"}, (
        "el upsert debe conservar el captured_at más reciente por clave"
    )
    assert json.dumps(result), (
        "el resultado del escenario debe ser serializable a JSON"
    )


# ---------------------------------------------------------------------------
# Timing table for design-streaming.md section 5.
# ---------------------------------------------------------------------------

def test_run_all_prints_the_timing_table(spark, lab_results):
    require_writable_local_fs(spark)
    result = lab_results

    assert list(result["scenarios"]) == list(lab.SCENARIO_NAMES), (
        "run_all debe devolver los cinco escenarios en el orden del plan"
    )
    assert set(result["timings_ms"]) == set(lab.SCENARIO_NAMES), (
        "run_all debe medir los cinco escenarios"
    )
    assert all(
        result["timings_ms"][name] > 0 for name in lab.SCENARIO_NAMES
    ), "cada escenario debe tener un tiempo positivo"
    assert json.dumps(result), (
        "run_all debe devolver un dict serializable a JSON"
    )

    print("\nLocal streaming lab timings (ms):")
    print(f"{'scenario':<28} {'ms':>10}")
    print("-" * 40)
    for name in lab.SCENARIO_NAMES:
        print(f"{name:<28} {result['timings_ms'][name]:>10.1f}")
    print("-" * 40)
    print(f"{'total':<28} {sum(result['timings_ms'].values()):>10.1f}")
