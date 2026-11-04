"""Local Spark helpers for the streaming lab tests (spec 011, T-02).

``configure_hadoop_home`` must be called explicitly before creating the
``SparkSession``; importing this module has no side effects, so the other test
modules keep their own Windows fallback/skip behaviour.

``require_writable_local_fs`` checks the JVM Hadoop filesystem used by the
streaming sinks and skips with instructions when the host cannot write.

``snapshot_shared_session``/``restore_shared_session`` keep a test module from
reconfiguring the ``SparkSession`` that the next module reuses (see the
comment on ``SHARED_SESSION_KEYS``).
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest


def configure_hadoop_home():
    """Set HADOOP_HOME/PATH from %LOCALAPPDATA%\\hadoop when winutils exists.

    If ``HADOOP_HOME`` is already set it is respected; only its ``bin``
    directory is added to ``PATH`` when it exists. On Linux/CI, where
    ``LOCALAPPDATA`` is absent, the function does nothing.
    """
    hadoop_home = os.environ.get("HADOOP_HOME")
    if hadoop_home:
        bin_dir = Path(hadoop_home) / "bin"
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return
        candidate = Path(local_app_data) / "hadoop"
        if not (candidate / "bin" / "winutils.exe").exists():
            return
        os.environ["HADOOP_HOME"] = str(candidate)
        bin_dir = candidate / "bin"

    if bin_dir.is_dir():
        current_path = os.environ.get("PATH", "")
        if str(bin_dir) not in current_path.split(os.pathsep):
            os.environ["PATH"] = str(bin_dir) + os.pathsep + current_path


# ---------------------------------------------------------------------------
# Shared session isolation
# ---------------------------------------------------------------------------

#: Runtime SQL configurations that ``SparkSession.builder.getOrCreate()``
#: copies into a session it did not create: PySpark 4.2 warns "Using an
#: existing Spark session; only runtime SQL configurations will take effect"
#: and applies them with ``applyModifiableSettings``. This tuple lists only
#: the keys that must come out of a module as they went in: they change how
#: the next module runs (shuffle partitioning, session catalog) and, left
#: behind, they break the Delta catalog of the shared session (every later
#: table lookup fails). ``restore_shared_session`` returns each key to the
#: value it had when the module started, or removes it when it was not set:
#: in practice the Delta catalog ends up absent and the shuffle partitions
#: keep the value they already had (``1``, the one the creating module asked
#: for), they do not end up absent.
#:
#: Static keys need no guard here. ``spark.sql.warehouse.dir`` and
#: ``spark.sql.extensions`` are really ignored on an existing session;
#: ``spark.app.name``, ``spark.master`` or ``spark.python.worker.timeout`` do
#: land in its ``SQLConf`` (measured) but are inert, they change neither the
#: engine nor the isolation. The keys that break the catalog are the
#: catalog/extension kind, and the only one Spark applies on an existing
#: session is the dynamic ``spark.sql.catalog.spark_catalog``, which is why it
#: is in the tuple above.
#:
#: ``spark.sql.session.timeZone`` is another runtime key of the same builder,
#: and it is neither saved nor restored: it stays behind as a known,
#: documented inheritance. Saving it would not give a previous value back,
#: because the session this module writes onto was created without an
#: explicit zone: a "restore" would write the host default zone, not a value
#: that was set before. No module depends on the inherited value:
#: ``test_silver_incremental`` pins UTC itself, ``test_streaming_lab`` stops
#: the session, ``test_upsert_job_offers`` creates a new one pinned to UTC,
#: and ``test_web_export`` does not depend on the zone at all (it uses
#: ``DateType`` and Python's UTC). Do not build anything on that
#: inheritance: whoever adds another dynamic key must decide explicitly
#: whether to save and restore it or to inherit it, and write it down.
SHARED_SESSION_KEYS = (
    "spark.sql.shuffle.partitions",
    "spark.sql.catalog.spark_catalog",
)


def snapshot_shared_session(spark, keys=SHARED_SESSION_KEYS):
    """Return the current value of ``keys`` (``None`` means "not set").

    It must be called *before* the module's builder runs ``getOrCreate``,
    because that is when Spark writes the builder options into the session
    that already exists (the one created by an earlier module). ``spark`` may
    be ``None``: the module then creates the session with its own options and
    has nothing to restore.
    """
    if spark is None:
        return {}
    return {key: spark.conf.get(key, None) for key in keys}


def restore_shared_session(spark, snapshot):
    """Undo the runtime SQL settings ``snapshot`` recorded.

    Without this the settings outlive their module: the next module reuses the
    same session and inherits a ``spark_catalog`` of Delta whose class is not
    on the classloader of the session created without ``spark.jars.packages``
    (every later table/temporary-view lookup fails), or another module's
    shuffle partitioning. Only dynamic keys belong here: ``unset`` on a static
    key raises "Cannot modify the value of a static config".
    """
    for key, value in snapshot.items():
        if value is None:
            spark.conf.unset(key)
        else:
            spark.conf.set(key, value)


def require_writable_local_fs(spark):
    """Skip the test if Spark cannot write to the local filesystem.

    The probe uses the JVM Hadoop FileSystem, the same path the streaming
    sinks and checkpoints use. On Windows, Hadoop needs ``winutils.exe`` for
    ``chmod`` (and the native ``hadoop.dll``); if either is missing, creating
    or deleting a directory fails and the test skips instead of reporting a
    misleading failure.
    """
    probe = Path(tempfile.mkdtemp(prefix="streaming-lab-fs-"))
    target = probe / "write-probe"
    try:
        jvm = spark.sparkContext._jvm
        hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
        filesystem = jvm.org.apache.hadoop.fs.FileSystem.get(hadoop_conf)
        path = jvm.org.apache.hadoop.fs.Path(str(target))
        filesystem.mkdirs(path)
        if not filesystem.exists(path):
            raise RuntimeError("mkdirs no creó el directorio de prueba")
        filesystem.delete(path, True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            "Hadoop no puede escribir en el sistema de ficheros local: "
            f"{type(exc).__name__}: {exc}. En Windows, Hadoop necesita "
            "winutils.exe para chmod: instálalo en "
            "%LOCALAPPDATA%\\hadoop\\bin\\winutils.exe y define "
            "HADOOP_HOME=%LOCALAPPDATA%\\hadoop (la carpeta que contiene "
            "bin\\); si winutils.exe ya está, comprueba que la librería "
            "nativa hadoop.dll esté en la misma carpeta bin. En Linux/CI no "
            "hace falta."
        )
    finally:
        shutil.rmtree(probe, ignore_errors=True)
