"""Make the `scrapers-pipeline` directory importable during tests.

The directory name contains a hyphen, so it cannot be imported as a package.
Tests import the business modules as top-level modules (`verification.sources`,
`verification.field_contract`, `coherence`), which requires the directory on
sys.path.
"""
from __future__ import annotations

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
