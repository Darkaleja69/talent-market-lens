"""Configure Hadoop before any test module creates a SparkSession.

The Spark JVM caches ``hadoop.home.dir`` when it starts, so ``HADOOP_HOME``
must exist before the first session, whichever test module creates it.
Importing this conftest happens before test collection, which guarantees the
order. There are no fixtures or sessions here, and the environment is not
restored: it belongs to the pytest process, which starts clean on every run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _local_spark import configure_hadoop_home  # noqa: E402

configure_hadoop_home()
