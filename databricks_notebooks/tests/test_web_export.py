"""Pure tests for the web export geography (no pyspark).

Cover ``geo_country`` (the M normalization of ``Fact_Offers``) and
``geo_region`` plus ``REGION_MAP`` (the ``Dim_RegionMap`` catalog as a versioned
constant). The fidelity test parses the TMDL file directly, so the semantic
model and the constant cannot drift apart.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Prepare_Web_Export as w  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REGION_MAP_TMDL = (REPO_ROOT / "Job_Offers_Dashboard.SemanticModel"
                   / "definition" / "tables" / "Dim_RegionMap.tmdl")

_ROW_RE = re.compile(r'\{\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\}')

# geo_country


def test_geo_country_us_states_used_as_country():
    assert w.geo_country("CA") == "United States"
    assert w.geo_country("FL") == "United States"
    assert w.geo_country("PA") == "United States"


def test_geo_country_language_aliases():
    assert w.geo_country("Alemania") == "Germany"
    assert w.geo_country("Austria y Suiza") == "Austria"
    assert w.geo_country("Austria and Switzerland") == "Austria"
    assert w.geo_country("Oriente Medio y África") == "Middle East & Africa"


def test_geo_country_empty_missing_and_placeholder():
    assert w.geo_country("") == "(Not specified)"
    assert w.geo_country("   ") == "(Not specified)"
    assert w.geo_country(None) == "(Not specified)"
    assert w.geo_country("(Not specified)") == "(Not specified)"
    assert w.geo_country("  (Not specified)  ") == "(Not specified)"


def test_geo_country_trims_and_keeps_unknown_exactly():
    assert w.geo_country("  Spain  ") == "Spain"
    assert w.geo_country("Portugal") == "Portugal"
    # Comparisons are exact: lowercase values are data, not aliases.
    assert w.geo_country("alemania") == "alemania"
    assert w.geo_country("ca") == "ca"
    assert w.geo_country("austria y suiza") == "austria y suiza"
    assert w.geo_country("CA ") == "United States"


# geo_region


def test_geo_region_catalog_pairs():
    assert w.geo_region("Spain", "Madrid") == "Madrid"
    assert w.geo_region("Spain", "Terrassa") == "Catalonia"
    assert w.geo_region("Spain", "Sant Vicenç dels Horts") == "Catalonia"
    assert w.geo_region("Ireland", "Dublín") == "Dublin"
    assert w.geo_region("Switzerland", "Zúrich") == "Zurich"
    assert w.geo_region("Netherlands", "Amsterdam") == "North Holland"
    assert w.geo_region("United States", "CA") == "California"
    assert w.geo_region("United States", "NY") == "New York"


def test_geo_region_country_fallback_rows():
    assert w.geo_region("Spain", "Spain") == "(Country)"
    assert w.geo_region("Ireland", "Ireland") == "(Country)"
    assert w.geo_region("Netherlands", "Netherlands") == "(Country)"
    assert w.geo_region("Switzerland", "Switzerland") == "(Country)"
    assert w.geo_region("United States", "United States") == "(Country)"
    assert w.geo_region("Europe", "Europe") == "(Country)"


def test_geo_region_unknown_pairs_fall_back_to_other():
    # Barcelona is not a Dim_RegionMap row (it only exists in Dim_Geo), so the
    # exact pair match falls back to (Other) like the model's left merge.
    assert w.geo_region("Spain", "Barcelona") == "(Other)"
    assert w.geo_region("Portugal", "Lisboa") == "(Other)"
    assert w.geo_region("Spain", "") == "(Other)"
    assert w.geo_region("", "") == "(Other)"
    assert w.geo_region(None, None) == "(Other)"


def test_geo_region_trims_and_is_case_sensitive():
    assert w.geo_region(" Spain ", " Madrid ") == "Madrid"
    assert w.geo_region("  Ireland  ", "  Dublín  ") == "Dublin"
    assert w.geo_region("spain", "madrid") == "(Other)"
    assert w.geo_region("Spain", "barcelona") == "(Other)"


# Fidelity with the semantic model


def test_region_map_matches_semantic_model():
    text = REGION_MAP_TMDL.read_text(encoding="utf-8")
    parsed = [tuple(match) for match in _ROW_RE.findall(text)]
    assert parsed, "Dim_RegionMap.tmdl should contain #table rows"
    # Multiset equality: any added, removed, changed or duplicated row in the
    # model forces REGION_MAP to be updated.
    assert sorted(parsed) == sorted(w.REGION_MAP)
