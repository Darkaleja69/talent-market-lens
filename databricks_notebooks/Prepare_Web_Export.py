"""Export of the Gold layer for the public web dashboard (geography part).

This module owns the Parquet + ``meta.json`` export consumed by the static site
under ``docs/dashboard``. This first step versions the geography that Power BI
used to compute:

- ``geo_country`` ports the M partition of ``Fact_Offers`` (trim, US states
  used as country, language aliases, empty -> ``(Not specified)``).
- ``REGION_MAP`` is the full ``Dim_RegionMap`` catalog and ``geo_region`` maps
  exact ``(country, region)`` pairs to their target region with ``(Other)`` as
  fallback.

``Dim_Geo`` and that M partition live only in the semantic model (a documented
exception in ``Prepare_Gold.py``), so they are ported here on purpose to keep
the export and the map independent from Power BI. If the model mapping changes,
this constant is updated together with its fidelity test.

Only the standard library is imported at module level, so ``geo_country``,
``geo_region`` and ``REGION_MAP`` can be imported and tested without Spark.
PySpark is imported lazily inside the functions that need it.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Reference data (equivalent to the Power BI table Dim_RegionMap)
# ---------------------------------------------------------------------------

# Exact copy of every row of the Dim_RegionMap #table(...) block, in model
# order, including the country-level "(Country)" fallback rows and the
# duplicated ("Spain", "Cantabria", "Cantabria") row that exists in the model.
REGION_MAP = (
    # Spain
    ("Spain", "Madrid", "Madrid"),
    ("Spain", "Catalonia", "Catalonia"),
    ("Spain", "Valencia", "Valencia"),
    ("Spain", "Sevilla", "Sevilla"),
    ("Spain", "Basque Country", "Basque Country"),
    ("Spain", "Málaga", "Málaga"),
    ("Spain", "Zaragoza", "Zaragoza"),
    ("Spain", "Granada", "Granada"),
    ("Spain", "Pontevedra", "Pontevedra"),
    ("Spain", "Murcia", "Murcia"),
    ("Spain", "Islas Baleares", "Islas Baleares"),
    ("Spain", "Las Palmas", "Las Palmas"),
    ("Spain", "Santa Cruz de Tenerife", "Santa Cruz de Tenerife"),
    ("Spain", "A Coruña", "A Coruña"),
    ("Spain", "Asturias", "Asturias"),
    ("Spain", "Cantabria", "Cantabria"),
    ("Spain", "Navarra", "Navarra"),
    ("Spain", "La Rioja", "La Rioja"),
    ("Spain", "Valladolid", "Valladolid"),
    ("Spain", "Alicante", "Alicante"),
    ("Spain", "Córdoba", "Córdoba"),
    ("Spain", "Salamanca", "Salamanca"),
    ("Spain", "Burgos", "Burgos"),
    ("Spain", "Pozuelo de Alarcón", "Madrid"),
    ("Spain", "Alcobendas", "Madrid"),
    ("Spain", "Moncloa-Aravaca", "Madrid"),
    ("Spain", "Boadilla del Monte", "Madrid"),
    ("Spain", "Moralzarzal", "Madrid"),
    ("Spain", "El Prat de Llobregat", "Catalonia"),
    ("Spain", "Esplugues de Llobregat", "Catalonia"),
    ("Spain", "Hospitalet de Llobregat", "Catalonia"),
    ("Spain", "Sant Just Desvern", "Catalonia"),
    ("Spain", "Sant Vicenç dels Horts", "Catalonia"),
    ("Spain", "Terrassa", "Catalonia"),
    ("Spain", "Sabadell", "Catalonia"),
    ("Spain", "Amorebieta-Etxano", "Basque Country"),
    ("Spain", "Amorebieta", "Basque Country"),
    ("Spain", "Getxo", "Basque Country"),
    ("Spain", "Mungia", "Basque Country"),
    ("Spain", "Igorre", "Basque Country"),
    ("Spain", "Mallabia", "Basque Country"),
    ("Spain", "Ibarra", "Basque Country"),
    ("Spain", "Valencian Community", "Valencia"),
    ("Spain", "Comunidad Valenciana", "Valencia"),
    ("Spain", "Andalusia", "Sevilla"),
    ("Spain", "Andalucía", "Sevilla"),
    ("Spain", "Galicia", "A Coruña"),
    ("Spain", "Castile and Leon", "Valladolid"),
    ("Spain", "Canary Islands", "Las Palmas"),
    ("Spain", "Castile-La Mancha", "Toledo"),
    ("Spain", "Cantabria", "Cantabria"),
    # Ireland
    ("Ireland", "Dublin", "Dublin"),
    ("Ireland", "Dublín", "Dublin"),
    ("Ireland", "County Dublin", "Dublin"),
    ("Ireland", "Fingal", "Dublin"),
    ("Ireland", "Dún Laoghaire-Rathdown", "Dublin"),
    ("Ireland", "Cork", "Cork"),
    ("Ireland", "County Cork", "Cork"),
    ("Ireland", "Galway", "Galway"),
    ("Ireland", "County Galway", "Galway"),
    ("Ireland", "Limerick", "Limerick"),
    ("Ireland", "County Limerick", "Limerick"),
    ("Ireland", "Waterford", "Waterford"),
    ("Ireland", "County Waterford", "Waterford"),
    ("Ireland", "Condado de Waterford", "Waterford"),
    ("Ireland", "Kilkenny", "Kilkenny"),
    ("Ireland", "County Kilkenny", "Kilkenny"),
    ("Ireland", "Sligo", "Sligo"),
    ("Ireland", "County Sligo", "Sligo"),
    ("Ireland", "Louth", "Louth"),
    ("Ireland", "County Louth", "Louth"),
    ("Ireland", "Westmeath", "Westmeath"),
    ("Ireland", "County Westmeath", "Westmeath"),
    ("Ireland", "Kerry", "Kerry"),
    ("Ireland", "County Kerry", "Kerry"),
    ("Ireland", "Clare", "Clare"),
    ("Ireland", "Co. Clare", "Clare"),
    ("Ireland", "County Clare", "Clare"),
    ("Ireland", "Shannon", "Clare"),
    ("Ireland", "Wexford", "Wexford"),
    ("Ireland", "County Wexford", "Wexford"),
    ("Ireland", "Wicklow", "Wicklow"),
    ("Ireland", "County Wicklow", "Wicklow"),
    ("Ireland", "Condado de Wicklow", "Wicklow"),
    ("Ireland", "Carlow", "Carlow"),
    ("Ireland", "County Carlow", "Carlow"),
    ("Ireland", "Kildare", "Kildare"),
    ("Ireland", "County Kildare", "Kildare"),
    ("Ireland", "Condado de Kildare", "Kildare"),
    ("Ireland", "Mayo", "Mayo"),
    ("Ireland", "County Mayo", "Mayo"),
    ("Ireland", "Longford", "Longford"),
    ("Ireland", "County Longford", "Longford"),
    ("Ireland", "Monaghan", "Monaghan"),
    ("Ireland", "County Monaghan", "Monaghan"),
    ("Ireland", "Roscommon", "Roscommon"),
    ("Ireland", "County Roscommon", "Roscommon"),
    ("Ireland", "Meath", "Meath"),
    ("Ireland", "County Meath", "Meath"),
    ("Ireland", "Condado de Meath", "Meath"),
    ("Ireland", "Tipperary", "Tipperary"),
    ("Ireland", "County Tipperary", "Tipperary"),
    ("Ireland", "Offaly", "Offaly"),
    ("Ireland", "County Offaly", "Offaly"),
    ("Ireland", "Leitrim", "Leitrim"),
    ("Ireland", "County Leitrim", "Leitrim"),
    ("Ireland", "Laois", "Laois"),
    ("Ireland", "County Laois", "Laois"),
    ("Ireland", "Donegal", "Donegal"),
    ("Ireland", "County Donegal", "Donegal"),
    ("Ireland", "Cavan", "Cavan"),
    ("Ireland", "County Cavan", "Cavan"),
    # Netherlands
    ("Netherlands", "North Holland", "North Holland"),
    ("Netherlands", "Holanda Septentrional", "North Holland"),
    ("Netherlands", "Noord-Holland", "North Holland"),
    ("Netherlands", "Amsterdam", "North Holland"),
    ("Netherlands", "South Holland", "South Holland"),
    ("Netherlands", "Holanda Meridional", "South Holland"),
    ("Netherlands", "Zuid-Holland", "South Holland"),
    ("Netherlands", "Utrecht", "Utrecht"),
    ("Netherlands", "Gelderland", "Gelderland"),
    ("Netherlands", "Flevoland", "Flevoland"),
    ("Netherlands", "Flevolanda", "Flevoland"),
    ("Netherlands", "North Brabant", "North Brabant"),
    ("Netherlands", "Noord-Brabant", "North Brabant"),
    ("Netherlands", "Groningen", "Groningen"),
    ("Netherlands", "Limburg", "Limburg"),
    ("Netherlands", "Overijssel", "Overijssel"),
    ("Netherlands", "Friesland", "Friesland"),
    ("Netherlands", "Zeeland", "Zeeland"),
    ("Netherlands", "Drenthe", "Drenthe"),
    # Switzerland
    ("Switzerland", "Zurich", "Zurich"),
    ("Switzerland", "Zürich", "Zurich"),
    ("Switzerland", "Zúrich", "Zurich"),
    ("Switzerland", "Geneva", "Geneva"),
    ("Switzerland", "Ginebra", "Geneva"),
    ("Switzerland", "Genf", "Geneva"),
    ("Switzerland", "Thônex", "Geneva"),
    ("Switzerland", "Basel", "Basel-Stadt"),
    ("Switzerland", "Basel-Stadt", "Basel-Stadt"),
    ("Switzerland", "Basilea", "Basel-Stadt"),
    ("Switzerland", "Bern", "Bern"),
    ("Switzerland", "Berne", "Bern"),
    ("Switzerland", "Vaud", "Vaud"),
    ("Switzerland", "Lausanne", "Vaud"),
    ("Switzerland", "Lucerne", "Lucerne"),
    ("Switzerland", "Lucerna", "Lucerne"),
    ("Switzerland", "Luzern", "Lucerne"),
    ("Switzerland", "St. Gallen", "St. Gallen"),
    ("Switzerland", "St Gallen", "St. Gallen"),
    ("Switzerland", "San Galo", "St. Gallen"),
    ("Switzerland", "Ticino", "Ticino"),
    ("Switzerland", "Tesino", "Ticino"),
    ("Switzerland", "Zug", "Zug"),
    ("Switzerland", "Fribourg", "Fribourg"),
    ("Switzerland", "Friburgo", "Fribourg"),
    ("Switzerland", "Neuchâtel", "Neuchâtel"),
    ("Switzerland", "Neuchatel", "Neuchâtel"),
    ("Switzerland", "Aargau", "Aargau"),
    ("Switzerland", "Argovia", "Aargau"),
    ("Switzerland", "Schaffhausen", "Schaffhausen"),
    ("Switzerland", "Escafusa", "Schaffhausen"),
    ("Switzerland", "Schwyz", "Schwyz"),
    ("Switzerland", "Glarus", "Glarus"),
    ("Switzerland", "Thurgau", "Thurgau"),
    ("Switzerland", "Turgovia", "Thurgau"),
    ("Switzerland", "Obwalden", "Obwalden"),
    ("Switzerland", "Solothurn", "Solothurn"),
    # United States
    ("United States", "AL", "Alabama"),
    ("United States", "AK", "Alaska"),
    ("United States", "AZ", "Arizona"),
    ("United States", "AR", "Arkansas"),
    ("United States", "CA", "California"),
    ("United States", "CO", "Colorado"),
    ("United States", "CT", "Connecticut"),
    ("United States", "DE", "Delaware"),
    ("United States", "DC", "District of Columbia"),
    ("United States", "FL", "Florida"),
    ("United States", "GA", "Georgia"),
    ("United States", "HI", "Hawaii"),
    ("United States", "ID", "Idaho"),
    ("United States", "IL", "Illinois"),
    ("United States", "IN", "Indiana"),
    ("United States", "IA", "Iowa"),
    ("United States", "KS", "Kansas"),
    ("United States", "KY", "Kentucky"),
    ("United States", "LA", "Louisiana"),
    ("United States", "ME", "Maine"),
    ("United States", "MD", "Maryland"),
    ("United States", "MA", "Massachusetts"),
    ("United States", "MI", "Michigan"),
    ("United States", "MN", "Minnesota"),
    ("United States", "MS", "Mississippi"),
    ("United States", "MO", "Missouri"),
    ("United States", "MT", "Montana"),
    ("United States", "NE", "Nebraska"),
    ("United States", "NV", "Nevada"),
    ("United States", "NH", "New Hampshire"),
    ("United States", "NJ", "New Jersey"),
    ("United States", "NM", "New Mexico"),
    ("United States", "NY", "New York"),
    ("United States", "NC", "North Carolina"),
    ("United States", "ND", "North Dakota"),
    ("United States", "OH", "Ohio"),
    ("United States", "OK", "Oklahoma"),
    ("United States", "OR", "Oregon"),
    ("United States", "PA", "Pennsylvania"),
    ("United States", "RI", "Rhode Island"),
    ("United States", "SC", "South Carolina"),
    ("United States", "SD", "South Dakota"),
    ("United States", "TN", "Tennessee"),
    ("United States", "TX", "Texas"),
    ("United States", "UT", "Utah"),
    ("United States", "VT", "Vermont"),
    ("United States", "VA", "Virginia"),
    ("United States", "WA", "Washington"),
    ("United States", "WV", "West Virginia"),
    ("United States", "WI", "Wisconsin"),
    ("United States", "WY", "Wyoming"),
    ("United States", "PR", "Puerto Rico"),
    # Country-level fallback rows
    ("Spain", "Spain", "(Country)"),
    ("Ireland", "Ireland", "(Country)"),
    ("Netherlands", "Netherlands", "(Country)"),
    ("Switzerland", "Switzerland", "(Country)"),
    ("United States", "United States", "(Country)"),
    ("Europe", "Europe", "(Country)"),
)

# Exact pair lookup used by geo_region. The duplicated Cantabria pair in the
# model collapses safely in a dict.
_REGION_LOOKUP = {
    (country, region): target
    for country, region, target in REGION_MAP
}


def _text(value) -> str:
    """M ``try Text.Trim(value) otherwise ""``: null becomes empty, then trim."""
    if value is None:
        return ""
    return str(value).strip()


def geo_country(value) -> str:
    """Normalize a location_country value exactly like the model M partition.

    Empty or ``(Not specified)`` -> ``(Not specified)``; the US states used as
    country in the source (``CA``/``FL``/``PA``) -> ``United States``;
    ``Alemania`` -> ``Germany``; ``Austria y Suiza``/``Austria and
    Switzerland`` -> ``Austria``; ``Oriente Medio y África`` -> ``Middle East &
    Africa``; anything else is kept as trimmed. Comparisons are exact (no case
    folding).
    """
    country = _text(value)
    if country == "" or country == "(Not specified)":
        return "(Not specified)"
    if country in ("CA", "FL", "PA"):
        return "United States"
    if country == "Alemania":
        return "Germany"
    if country in ("Austria y Suiza", "Austria and Switzerland"):
        return "Austria"
    if country == "Oriente Medio y África":
        return "Middle East & Africa"
    return country


def geo_region(country, region) -> str:
    """Map an exact trimmed ``(country, region)`` pair against REGION_MAP.

    Equivalent to the model left merge against Dim_RegionMap followed by
    ``TargetRegion{0} otherwise "(Other)"``: catalog pairs return their target
    region (including the country-level ``(Country)`` fallback) and unknown
    pairs return ``(Other)``.
    """
    c = _text(country)
    r = _text(region)
    return _REGION_LOOKUP.get((c, r), "(Other)")
