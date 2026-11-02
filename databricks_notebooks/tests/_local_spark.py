"""Local Spark helpers for the streaming lab tests (spec 011, T-02).

``configure_hadoop_home`` must be called explicitly before creating the
``SparkSession``; importing this module has no side effects, so the other test
modules keep their own Windows fallback/skip behaviour.

``require_writable_local_fs`` checks the JVM Hadoop filesystem used by the
streaming sinks and skips with instructions when the host cannot write.
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
