"""Tests for publication-stage classification (T-31; RF-3, RF-6, RF-8).

No network, no PyArrow and no landing access are involved: the module under
test is pure and only reuses the ``landing`` state constants.
"""
from __future__ import annotations

import pytest

from verification import completeness, landing, publication


def _field(
    field: str, required: bool, valid: int, total: int = 100
) -> completeness.FieldCompleteness:
    """Build a FieldCompleteness with an exact completeness percentage."""
    return completeness.FieldCompleteness(
        field=field,
        required=required,
        total=total,
        valid=valid,
        absent=total - valid,
        invalid=0,
        completeness_pct=0.0 if total == 0 else 100.0 * valid / total,
    )


def _source(
    source: str,
    fields: dict[str, completeness.FieldCompleteness],
    total_offers: int | None = None,
) -> completeness.SourceCompleteness:
    if total_offers is None:
        total_offers = max((stats.total for stats in fields.values()), default=0)
    return completeness.SourceCompleteness(
        source=source, total_offers=total_offers, fields=fields
    )


def _linkedin_offers(count: int) -> list[dict]:
    """Build ``count`` valid, unique LinkedIn offers for completeness."""
    return [
        {
            "job_id": f"l{index}",
            "title": f"Data Engineer {index}",
            "company_name": "ACME Corp",
            "description_full": "A real description.",
            "scraped_at": "2026-09-26",
        }
        for index in range(count)
    ]


# --- Constants ---------------------------------------------------------------


def test_publication_state_constants_match_the_design():
    assert publication.PUBLICATION_OK == "ok"
    assert publication.PUBLICATION_NO_NEW_OFFERS == "no_new_offers"
    assert publication.PUBLICATION_EMPTY == "empty"
    assert publication.PUBLICATION_PENDING == "pending"
    assert publication.PUBLICATION_MISMATCH == "mismatch"
    assert publication.PUBLICATION_REJECTED == "rejected"
    assert publication.PUBLICATION_NOT_CHECKED == "not_checked"
    assert publication.PUBLICATION_NOT_APPLICABLE == "not_applicable"


# --- Snapshot vs. delta: the key rule (RF-3, RF-8) ---------------------------


def test_snapshot_with_offers_and_empty_delta_is_no_new_offers():
    # A visible, empty OnlyNewOffers delta with a non-empty snapshot means
    # everything was already uploaded; it is NOT zero captured offers.
    state = publication.classify_publication(
        obtained_offers=42, delta_offers=0, manifest_state=landing.STATE_OK
    )

    assert state == publication.PUBLICATION_NO_NEW_OFFERS
    assert publication.is_zero_offers_captured(state) is False


def test_empty_snapshot_and_empty_delta_is_empty():
    state = publication.classify_publication(
        obtained_offers=0, delta_offers=0, manifest_state=landing.STATE_OK
    )

    assert state == publication.PUBLICATION_EMPTY
    assert publication.is_zero_offers_captured(state) is True


def test_unmeasured_snapshot_and_empty_delta_is_not_checked():
    # An absent snapshot is missing evidence, not a measured zero: only an
    # explicit `obtained_offers == 0` can prove zero captured offers (RF-3).
    state = publication.classify_publication(
        obtained_offers=None, delta_offers=0, manifest_state=landing.STATE_OK
    )

    assert state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(state) is False


def test_delta_with_offers_is_ok():
    state = publication.classify_publication(
        obtained_offers=50, delta_offers=7, manifest_state=landing.STATE_OK
    )

    assert state == publication.PUBLICATION_OK
    assert publication.is_zero_offers_captured(state) is False


# --- Pending vs. error (RF-8) ------------------------------------------------


def test_pending_manifest_is_pending_not_zero():
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=None, manifest_state=landing.STATE_PENDING
    )

    assert state == publication.PUBLICATION_PENDING
    assert publication.is_zero_offers_captured(state) is False


def test_missing_manifest_is_pending_not_zero():
    # No manifest/state at all but a publication was expected: pending, never
    # an error and never zero captured.
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=None, manifest_state=None
    )

    assert state == publication.PUBLICATION_PENDING
    assert publication.is_zero_offers_captured(state) is False


def test_unverified_manifest_is_not_checked_not_pending():
    # The object is visible but unreadable or lacks rows/sha256 to compare, so
    # the publication cannot be declared correct; it is not "not there yet".
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=9, manifest_state=landing.STATE_UNVERIFIED
    )

    assert state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(state) is False


def test_unverified_manifest_wins_over_an_empty_delta():
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=0, manifest_state=landing.STATE_UNVERIFIED
    )

    assert state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(state) is False


def test_visible_object_without_published_metric_is_not_checked():
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=None, manifest_state=landing.STATE_OK
    )

    assert state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(state) is False


# --- Rejected / mismatch -----------------------------------------------------


def test_rejected_manifest_is_rejected():
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=9, manifest_state=landing.STATE_REJECTED
    )

    assert state == publication.PUBLICATION_REJECTED
    assert publication.is_zero_offers_captured(state) is False


def test_rejected_flag_overrides_the_manifest_state():
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=9, manifest_state=landing.STATE_OK, rejected=True
    )

    assert state == publication.PUBLICATION_REJECTED


def test_rows_mismatch_is_mismatch():
    state = publication.classify_publication(
        obtained_offers=10, delta_offers=9, manifest_state=landing.STATE_MISMATCH
    )

    assert state == publication.PUBLICATION_MISMATCH
    assert publication.is_zero_offers_captured(state) is False


# --- Not applicable: nothing was prepared to publish (T-55) ------------------


def test_not_applicable_when_nothing_was_prepared():
    # The run prepared no data, so the absent manifest is not a pending upload
    # and it never fails the source (T-55, RF-8).
    state = publication.classify_publication(
        obtained_offers=None, delta_offers=None, not_applicable=True
    )

    assert state == publication.PUBLICATION_NOT_APPLICABLE
    assert publication.is_zero_offers_captured(state) is False


def test_not_checked_wins_over_not_applicable():
    state = publication.classify_publication(
        obtained_offers=None,
        delta_offers=None,
        not_checked=True,
        not_applicable=True,
    )

    assert state == publication.PUBLICATION_NOT_CHECKED


def test_build_source_publication_not_applicable():
    result = publication.build_source_publication(
        source="infojobs",
        obtained=None,
        published=None,
        not_applicable=True,
    )

    assert result.stage.state == publication.PUBLICATION_NOT_APPLICABLE
    assert result.stage.obtained_offers is None
    assert result.stage.delta_offers is None
    assert publication.is_zero_offers_captured(result.stage.state) is False


# --- Not checked (RemoteError) ----------------------------------------------


def test_not_checked_wins_over_every_other_signal():
    state = publication.classify_publication(
        obtained_offers=10,
        delta_offers=0,
        manifest_state=landing.STATE_OK,
        not_checked=True,
    )

    assert state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(state) is False


# --- Only EMPTY means zero captured -----------------------------------------


def test_only_empty_is_zero_offers_captured():
    for state in (
        publication.PUBLICATION_OK,
        publication.PUBLICATION_NO_NEW_OFFERS,
        publication.PUBLICATION_PENDING,
        publication.PUBLICATION_MISMATCH,
        publication.PUBLICATION_REJECTED,
        publication.PUBLICATION_NOT_CHECKED,
        publication.PUBLICATION_NOT_APPLICABLE,
    ):
        assert publication.is_zero_offers_captured(state) is False, state

    assert publication.is_zero_offers_captured(publication.PUBLICATION_EMPTY) is True


# --- compare_completeness (RF-6) --------------------------------------------


def test_compare_completeness_identical_stages_have_zero_difference():
    obtained = _source(
        "linkedin",
        {
            "title": _field("title", True, 90),
            "salary": _field("salary", False, 50),
        },
    )
    published = _source(
        "linkedin",
        {
            "title": _field("title", True, 90),
            "salary": _field("salary", False, 50),
        },
    )

    comparisons = publication.compare_completeness(obtained, published)

    # Contract order puts `title` before `salary`.
    assert [comparison.field for comparison in comparisons] == ["title", "salary"]
    for comparison in comparisons:
        assert comparison.difference == 0.0


def test_compare_completeness_reports_the_percentage_difference():
    obtained = _source("linkedin", {"title": _field("title", True, 50)})
    published = _source("linkedin", {"title": _field("title", True, 100)})

    (comparison,) = publication.compare_completeness(obtained, published)

    assert comparison.field == "title"
    assert comparison.required is True
    assert comparison.obtained_pct == 50.0
    assert comparison.published_pct == 100.0
    assert comparison.difference == 50.0


def test_compare_completeness_difference_can_be_negative():
    obtained = _source("linkedin", {"description": _field("description", True, 100)})
    published = _source("linkedin", {"description": _field("description", True, 50)})

    (comparison,) = publication.compare_completeness(obtained, published)

    assert comparison.obtained_pct == 100.0
    assert comparison.published_pct == 50.0
    assert comparison.difference == -50.0


def test_compare_completeness_ignores_fields_measured_in_only_one_stage():
    obtained = _source(
        "linkedin",
        {
            "title": _field("title", True, 100),
            "company": _field("company", True, 100),  # not measured published
        },
    )
    published = _source(
        "linkedin",
        {
            "title": _field("title", True, 100),
            "salary": _field("salary", False, 100),  # not measured obtained
        },
    )

    comparisons = publication.compare_completeness(obtained, published)

    assert [comparison.field for comparison in comparisons] == ["title"]


def test_compare_completeness_empty_when_stages_share_no_field():
    obtained = _source("linkedin", {"title": _field("title", True, 100)})
    published = _source("linkedin", {"salary": _field("salary", False, 100)})

    assert publication.compare_completeness(obtained, published) == ()


# --- build_source_publication with real completeness ------------------------


def test_build_source_publication_snapshot_with_offers_and_empty_delta():
    obtained = completeness.measure_offers_completeness(
        _linkedin_offers(3), "linkedin"
    )
    published = completeness.measure_offers_completeness([], "linkedin")

    result = publication.build_source_publication(
        source="linkedin",
        obtained=obtained,
        published=published,
        manifest_state=landing.STATE_OK,
    )

    assert result.source == "linkedin"
    assert result.obtained is obtained
    assert result.published is published
    assert result.stage.obtained_offers == 3
    assert result.stage.delta_offers == 0
    assert result.stage.state == publication.PUBLICATION_NO_NEW_OFFERS
    assert publication.is_zero_offers_captured(result.stage.state) is False


def test_build_source_publication_delta_with_offers_is_ok():
    obtained = completeness.measure_offers_completeness(
        _linkedin_offers(5), "linkedin"
    )
    published = completeness.measure_offers_completeness(
        _linkedin_offers(2), "linkedin"
    )

    result = publication.build_source_publication(
        source="linkedin",
        obtained=obtained,
        published=published,
        manifest_state=landing.STATE_OK,
    )

    assert result.stage.obtained_offers == 5
    assert result.stage.delta_offers == 2
    assert result.stage.state == publication.PUBLICATION_OK


def test_build_source_publication_absent_snapshot_and_empty_delta_not_checked():
    # No obtained stage: the snapshot was not measured, so an empty published
    # delta must not become a false "zero captured".
    published = completeness.measure_offers_completeness([], "indeed")

    result = publication.build_source_publication(
        source="indeed",
        obtained=None,
        published=published,
        manifest_state=landing.STATE_OK,
    )

    assert result.stage.obtained_offers is None
    assert result.stage.delta_offers == 0
    assert result.stage.state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(result.stage.state) is False


def test_build_source_publication_measured_empty_snapshot_and_delta_is_empty():
    obtained = completeness.measure_offers_completeness([], "indeed")
    published = completeness.measure_offers_completeness([], "indeed")

    result = publication.build_source_publication(
        source="indeed",
        obtained=obtained,
        published=published,
        manifest_state=landing.STATE_OK,
    )

    assert result.stage.obtained_offers == 0
    assert result.stage.delta_offers == 0
    assert result.stage.state == publication.PUBLICATION_EMPTY
    assert publication.is_zero_offers_captured(result.stage.state) is True


def test_build_source_publication_pending_manifest_is_pending():
    obtained = completeness.measure_offers_completeness(
        _linkedin_offers(4), "linkedin"
    )

    result = publication.build_source_publication(
        source="linkedin",
        obtained=obtained,
        published=None,
        manifest_state=landing.STATE_PENDING,
    )

    assert result.stage.obtained_offers == 4
    assert result.stage.delta_offers is None
    assert result.stage.state == publication.PUBLICATION_PENDING
    assert publication.is_zero_offers_captured(result.stage.state) is False


def test_build_source_publication_missing_delta_with_visible_manifest_not_checked():
    # Visible manifest but no measurable published delta: the delta cannot be
    # judged, so the stage is not checked rather than zero captured.
    obtained = completeness.measure_offers_completeness(
        _linkedin_offers(4), "linkedin"
    )

    result = publication.build_source_publication(
        source="linkedin",
        obtained=obtained,
        published=None,
        manifest_state=landing.STATE_OK,
    )

    assert result.stage.state == publication.PUBLICATION_NOT_CHECKED
    assert publication.is_zero_offers_captured(result.stage.state) is False


def test_build_source_publication_not_checked():
    result = publication.build_source_publication(
        source="linkedin", obtained=None, published=None, not_checked=True
    )

    assert result.stage.state == publication.PUBLICATION_NOT_CHECKED
    assert result.stage.obtained_offers is None
    assert result.stage.delta_offers is None


def test_build_source_publication_rejected():
    obtained = completeness.measure_offers_completeness(
        _linkedin_offers(4), "linkedin"
    )

    result = publication.build_source_publication(
        source="linkedin", obtained=obtained, published=None, rejected=True
    )

    assert result.stage.state == publication.PUBLICATION_REJECTED
    assert publication.is_zero_offers_captured(result.stage.state) is False


# --- Immutability ------------------------------------------------------------


def test_published_stage_is_frozen():
    stage = publication.PublishedStage(
        source="linkedin", state=publication.PUBLICATION_OK, obtained_offers=1, delta_offers=1
    )
    with pytest.raises(Exception):
        stage.state = publication.PUBLICATION_EMPTY  # type: ignore[misc]


def test_field_comparison_is_frozen():
    comparison = publication.FieldComparison(
        field="title",
        required=True,
        obtained_pct=50.0,
        published_pct=100.0,
        difference=50.0,
    )
    with pytest.raises(Exception):
        comparison.difference = 0.0  # type: ignore[misc]


def test_source_publication_is_frozen():
    stage = publication.PublishedStage(
        source="linkedin", state=publication.PUBLICATION_OK, obtained_offers=1, delta_offers=1
    )
    result = publication.SourcePublication(
        source="linkedin", obtained=None, published=None, stage=stage
    )
    with pytest.raises(Exception):
        result.source = "indeed"  # type: ignore[misc]
