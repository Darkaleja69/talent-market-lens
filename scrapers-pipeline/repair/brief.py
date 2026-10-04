"""Build the English JSON brief of one repair target (T-05; RF-1, RF-3).

The brief is the entry document of a repair: it gives the ``web-inspector``
and the implementer the source and its role, the reason (outcome and
diagnostic failures, or the completeness trigger with its field), the
diagnostic evidence verbatim, the local evidence paths, the applicable
playbook of plan §6, the per-field quality profile and the proposed bounded
live-test scope. Keys and labels are in English so the brief can be stored as
``context.json``; the diagnostic evidence stays verbatim in Spanish because it
is data of the 001 process.

The brief also carries the run and snapshot offer counters and a
``completeness_scope`` (``run`` or ``historical_snapshot``) so an aggregate
historical snapshot (LinkedIn case, plan §4) is not mistaken for an active
failure. The diagnostic contract does not publish the age of the rows missing
a field, so the brief states the aggregate but never invents that age.

Design rules:

- :func:`build_brief` is pure: it only reads the target and returns a
  JSON-serializable dictionary. :func:`render_brief` serializes it with
  ``ensure_ascii=False`` so the Spanish evidence keeps its accents.
- Paths are only the ones the target already carries (the playbook table of
  plan §2.3 plus the diagnostic); an absolute path inside the repository is
  rewritten relative to the repository root. No path is invented.
- :func:`validate_brief` is the defensive gate: a brief carrying credential
  keys (password/secret/token/SAS...) or browser-profile paths (AppData,
  Chrome, Default, Cookies...) is rejected with a Spanish message before it
  reaches a record. :func:`build_brief` validates its own result.
- An unknown source gets the generic playbook (§6.7), the minimum
  representative scope and no invented evidence paths.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from repair import targets

# Version of the brief contract; T-13 stores the brief as `context.json`.
BRIEF_SCHEMA_VERSION = 1

# Scope of the source completeness carried by the diagnostic (plan §4): the
# run measurement or an aggregate historical snapshot. A non-null
# `offers_snapshot` is the only signal the contract publishes: it never says
# how old the rows missing a field are, so the brief does not invent that age.
COMPLETENESS_SCOPE_RUN = "run"
COMPLETENESS_SCOPE_HISTORICAL_SNAPSHOT = "historical_snapshot"

# repair/brief.py -> parents[2] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

# English outcome labels for the primary reason summary. The codes are the
# machine outcomes of the 001 run evidence; an unknown code is echoed as is.
_OUTCOME_LABELS: dict[str, str] = {
    "ok": "ok: run completed",
    "empty": "empty: no offers captured in the run",
    "error": "error: technical failure or non-zero exit",
    "blocked": "blocked: captcha/anti-bot",
    "no_evidence": "no_evidence: source could not be confirmed",
}

# Playbook of plan §6 per source. The recipe is a short English summary of the
# known facts, questions and probable change; the plan stays the authority.
_PLAYBOOKS: dict[str, dict[str, str]] = {
    "infojobs": {
        "section": "6.1",
        "title": "InfoJobs - CAPTCHA block",
        "recipe": (
            "Blocked by a visible CAPTCHA on open (Distil) with 0 offers. "
            "Check which detector marker fires, whether the real user session "
            "gets the SERP, and whether an allowed internal JSON endpoint "
            "exists. Design to avoid the challenge: real session and "
            "challenge-token reuse, coherent fingerprint, human headers and "
            "pace; log the block reason and support an assisted pause. Red "
            "lines: no CAPTCHA solvers, no identity checks, no paid services."
        ),
    },
    "indeed": {
        "section": "6.2",
        "title": "Indeed - description at 0% and second page",
        "recipe": (
            "enrich_success is 0/53-0/72: the right-panel wait times out and "
            "the code continues before the /viewjob fallback; page 2 asks for "
            "login and --limit triggers Cloudflare. Check the real panel, "
            "/viewjob with the real session, page-2 behaviour and mosaic-data. "
            "Reorder the fallback, update selectors, emit progress during "
            "enrichment and resolve or document login and --limit."
        ),
    },
    "linkedin": {
        "section": "6.3",
        "title": "LinkedIn - historical description and optional fields",
        "recipe": (
            "About 970 rows without description with scraped_at <= "
            "17-09-2026; local coverage is ~100% since 18-09; salary 16.3% "
            "(regex) and work_mode 42.4%. Check whether the SDUI detail or "
            "the guest API expose structured salary/workplace and whether the "
            "store retries rows without description. Backfill the history (or "
            "justify why not) and extract salary/work_mode when the site "
            "exposes them."
        ),
    },
    "irishjobs": {
        "section": "6.4",
        "title": "IrishJobs - 0 cards and watchdog exit",
        "recipe": (
            "0 cards in every combination, no PROGRESS line and exit=75 "
            "watchdog; stepstone_nl shares the class and works. Check what the "
            "IE SERP returns in a real browser (challenge, different HTML, "
            "geo/rate) and whether an internal API exists. Update the "
            "selectors/parser, detect the block with a reason and emit "
            "PROGRESS per page (0 cards is also progress)."
        ),
    },
    "glassdoor": {
        "section": "6.5",
        "title": "Glassdoor - watchdog during descriptions",
        "recipe": (
            "The BFF returns 30 offers; up to 30 x 60 s descriptions run "
            "without PROGRESS and the watchdog stops the job (exit=75). Check "
            "page timing with the real session, whether descriptions can be "
            "batched or fetched on demand, and the right description budget. "
            "Emit a heartbeat inside the loop and bound max_description_jobs "
            "or the timeout without losing coverage."
        ),
    },
    "stepstone_nl": {
        "section": "6.6",
        "title": "StepStone NL - optional completeness",
        "recipe": (
            "Optional gap: salary, skills and work_mode at 0%. Extract the "
            "missing field from the detail (JSON-LD/DOM) and document what the "
            "portal exposes; a field the portal does not publish is not a "
            "failure and the complete description stays the priority."
        ),
    },
    "nvb": {
        "section": "6.6",
        "title": "NVB - optional completeness",
        "recipe": (
            "Optional gap: work_mode at 0% with 55 invalid values. Fix the "
            "mapping and validation against the API fields "
            "(workingPlace/contractType); a field the portal does not publish "
            "is not a failure and the complete description stays the priority."
        ),
    },
    "jobs_ch": {
        "section": "6.6",
        "title": "Jobs.ch - optional completeness",
        "recipe": (
            "Optional gap: salary 40.4%, skills 32.7%, work_mode 11.5%. Widen "
            "the detail extraction (JSON-LD and headers) and document what the "
            "portal exposes; a field the portal does not publish is not a "
            "failure and the complete description stays the priority."
        ),
    },
}

_GENERIC_PLAYBOOK: dict[str, str] = {
    "section": "6.7",
    "title": "Generic playbook for new sources or future failures",
    "recipe": (
        "Any id in sources[] or investigations[] is repairable without "
        "changing the spec. Check for an internal JSON API, the anti-bot "
        "vendor, server- vs client-side content, session needs, robots.txt "
        "and terms of use, and why offers or the field are missing; apply the "
        "anti-bot ladder and propose the change with the diagnostic evidence. "
        "Quality target: what the web/API allows, never below the current "
        "coverage."
    ),
}

# Proposed bounded live-test scope per source (plan §9 and §6.1-§6.5). The
# structure is stable: description, parameters, constraints and command.
_NO_ARTIFACT_CONSTRAINTS = [
    "no Azure upload",
    "no landing update",
    "no merge",
]
_PORTAL_CONSTRAINTS = [
    "only the affected portal",
    "no merge",
    "no Azure upload",
    "no landing update",
]

_TEST_SCOPES: dict[str, dict[str, object]] = {
    "infojobs": {
        "description": "1 keyword x 1 city x 1 page",
        "parameters": {"keywords": 1, "cities": 1, "pages": 1},
        "constraints": list(_NO_ARTIFACT_CONSTRAINTS),
        "command": None,
    },
    "indeed": {
        "description": "1 country x 1 term x 1 page",
        "parameters": {"countries": 1, "terms": 1, "pages": 1},
        "constraints": list(_NO_ARTIFACT_CONSTRAINTS),
        "command": None,
    },
    "linkedin": {
        "description": "1 role x 1 city in guest mode",
        "parameters": {"roles": 1, "cities": 1, "mode": "guest"},
        "constraints": list(_NO_ARTIFACT_CONSTRAINTS),
        "command": None,
    },
    "irishjobs": {
        "description": "affected portal only, 1 role, one page",
        "parameters": {"roles": 1, "max_pages": 1, "portals": ["irishjobs"]},
        "constraints": list(_PORTAL_CONSTRAINTS),
        "command": "python -m src.irishjobs --max-pages 1",
    },
    "glassdoor": {
        "description": (
            "affected portal only, 1 role, one page, watching time and "
            "PROGRESS"
        ),
        "parameters": {"roles": 1, "max_pages": 1, "portals": ["glassdoor"]},
        "constraints": list(_PORTAL_CONSTRAINTS),
        "command": "python -m src.glassdoor --max-pages 1",
    },
}

# Secondary-only sources of plan §6.6: their live test is a representative
# sample of the detail for the affected field.
_SECONDARY_DETAIL_SOURCES = frozenset({"stepstone_nl", "nvb", "jobs_ch"})
_SECONDARY_DETAIL_SCOPE: dict[str, object] = {
    "description": (
        "representative sample of the source detail for the affected field"
    ),
    "parameters": {"scope": "representative sample", "detail": True},
    "constraints": list(_NO_ARTIFACT_CONSTRAINTS),
    "command": None,
}

# Unknown source (plan §6.7): the minimum representative configuration,
# documented and repeatable; in Multi-site, only the affected portal.
_GENERIC_SCOPE: dict[str, object] = {
    "description": (
        "minimum representative configuration of the source, documented and "
        "repeatable"
    ),
    "parameters": {"scope": "minimum representative configuration"},
    "constraints": list(_NO_ARTIFACT_CONSTRAINTS),
    "command": None,
}

# Defensive patterns of the brief safety gate (RF-11 hygiene): credential-like
# keys and browser-profile paths must never reach a record.
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|token|sas|api[_-]?key|credential|private[_-]?key)"
)
# Real browser-profile locations, with either separator. A bare `AppData`
# segment is intentionally NOT matched: temporary diagnostics commonly live
# under `AppData\Local\Temp` and are not browser profiles.
_PROFILE_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])User Data(?:[\\/]|$)"
    r"|Google[\\/]Chrome"
    r"|(?:^|[\\/])Chromium(?:[\\/]|$)"
    r"|Mozilla[\\/]Firefox[\\/]Profiles"
    r"|Microsoft[\\/]Edge[\\/]User Data"
    r"|(?:^|[\\/])Cookies(?:[\\/]|$)"
    r"|(?:^|[\\/])Login Data(?:[\\/]|$)"
    r"|(?:^|[\\/])(?:chrome|cdp|browser)_profile(?:[\\/]|$)"
)


class BriefError(RuntimeError):
    """The brief carries forbidden content (credentials or browser profiles)."""


def build_brief(target: targets.RepairTarget) -> dict[str, object]:
    """Build the English, JSON-serializable brief of one repair target.

    The brief contains the source identity and role, the run and snapshot
    offer counters with the completeness scope, the structured reason (outcome
    and failures for a primary target; trigger, field and current percentage
    for a secondary one), the diagnostic evidence verbatim, the local evidence
    paths, the applicable playbook of plan §6, the per-field quality profile
    and the proposed bounded live-test scope. It is pure and validates its own
    result with :func:`validate_brief`.
    """
    scope = _test_scope(target)
    brief: dict[str, object] = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "source": target.source,
        "kind": target.kind,
        "role": target.role,
        "field": target.field,
        "trigger": target.trigger,
        "run_date": (
            target.run_date.isoformat() if target.run_date is not None else None
        ),
        "offers_current_run": target.offers_current_run,
        "offers_snapshot": target.offers_snapshot,
        "completeness_scope": _completeness_scope(target),
        "reason": _reason(target),
        "evidence": list(target.evidence),
        "evidence_paths": [
            _repo_relative(path) for path in target.evidence_paths
        ],
        "evidence_paths_base": "repository_root",
        "playbook": _playbook(target, scope),
        "quality": _quality(target.quality),
        "test_scope": scope,
    }
    validate_brief(brief)
    return brief


def render_brief(brief: dict[str, object]) -> str:
    """Serialize a brief as pretty JSON text, keeping Spanish accents (T-16)."""
    return json.dumps(brief, ensure_ascii=False, indent=2)


def validate_brief(brief: dict[str, object]) -> None:
    """Reject a brief with credentials or browser-profile paths (Spanish).

    Keys matching credential patterns (password, secret, token, SAS, API key,
    credential) and strings matching real browser-profile locations (User
    Data, Google\\Chrome, Chromium, Mozilla\\Firefox\\Profiles,
    Microsoft\\Edge\\User Data, Cookies, Login Data, *_profile directories)
    raise :class:`BriefError` with a Spanish message. A temporary diagnostic
    under ``AppData\\Local\\Temp`` is not a profile and is accepted.
    """
    _validate_value(brief)


def _validate_value(value: object) -> None:
    """Walk the brief and raise on the first forbidden key or path."""
    if isinstance(value, dict):
        for item_key, item in value.items():
            if isinstance(item_key, str) and _SENSITIVE_KEY_RE.search(item_key):
                raise BriefError(
                    f"el brief contiene una clave sensible «{item_key}»: no "
                    "debe incluir credenciales"
                )
            _validate_value(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_value(item)
    elif isinstance(value, str):
        if _PROFILE_PATH_RE.search(value):
            raise BriefError(
                f"el brief contiene una ruta de perfil de navegador: «{value}»"
            )


def _completeness_scope(target: targets.RepairTarget) -> str:
    """Return the scope of the source completeness: run or historical snapshot.

    The diagnostic contract does not publish the age of the rows missing a
    field; a non-null ``offers_snapshot`` only tells that the completeness
    aggregates an older snapshot (plan §4, LinkedIn case). The scope never
    invents that age.
    """
    if target.offers_snapshot is not None:
        return COMPLETENESS_SCOPE_HISTORICAL_SNAPSHOT
    return COMPLETENESS_SCOPE_RUN


def _reason(target: targets.RepairTarget) -> dict[str, object]:
    """Build the structured English reason of the target."""
    if target.role == targets.ROLE_SECONDARY:
        return {
            "trigger": target.trigger,
            "field": target.field,
            "current_pct": target.current_pct,
            "summary": _secondary_summary(target),
        }
    return {
        "outcome": target.outcome,
        "failures": list(target.failures),
        "summary": _primary_summary(target),
    }


def _primary_summary(target: targets.RepairTarget) -> str:
    """Return an English summary of the primary outcome and its failures."""
    if target.outcome is None:
        label = "outcome: unknown"
    else:
        label = _OUTCOME_LABELS.get(target.outcome, f"outcome: {target.outcome}")
    if target.failures:
        return f"{label} ({len(target.failures)} diagnostic failure reason(s))"
    return label


def _secondary_summary(target: targets.RepairTarget) -> str:
    """Return an English summary of the completeness trigger and its field."""
    measured = (
        "not measured"
        if target.current_pct is None
        else f"{target.current_pct}%"
    )
    if target.trigger == targets.TRIGGER_REQUIRED_FIELD:
        return f"required field '{target.field}' below target ({measured})"
    return f"optional field '{target.field}' at or below threshold ({measured})"


def _playbook(
    target: targets.RepairTarget,
    scope: dict[str, object],
) -> dict[str, object]:
    """Return the applicable plan §6 reference plus the proposed test scope."""
    entry = _PLAYBOOKS.get(target.source, _GENERIC_PLAYBOOK)
    return {
        "section": entry["section"],
        "title": entry["title"],
        "recipe": entry["recipe"],
        "test_scope": copy.deepcopy(scope),
    }


def _quality(
    profile: tuple[targets.QualityProfile, ...],
) -> list[dict[str, object]]:
    """Convert the target quality profile into JSON-ready English entries."""
    return [
        {
            "field": entry.field,
            "required": entry.required,
            "current_pct": entry.current_pct,
            "target_pct": entry.target_pct,
            "is_focus": entry.is_focus,
        }
        for entry in profile
    ]


def _test_scope(target: targets.RepairTarget) -> dict[str, object]:
    """Return the proposed bounded live-test scope of the target (plan §9).

    A secondary target of StepStone NL, NVB or Jobs.ch tests a representative
    sample of the detail for its field (§6.6); any other target uses its
    source scope (§6.1-§6.5) and an unknown source the minimum representative
    configuration (§6.7). The returned structure is a fresh copy.
    """
    if (
        target.role == targets.ROLE_SECONDARY
        and target.source in _SECONDARY_DETAIL_SOURCES
    ):
        scope = copy.deepcopy(_SECONDARY_DETAIL_SCOPE)
        parameters = scope["parameters"]
        assert isinstance(parameters, dict)
        parameters["field"] = target.field
        return scope
    known = _TEST_SCOPES.get(target.source)
    if known is not None:
        return copy.deepcopy(known)
    scope = copy.deepcopy(_GENERIC_SCOPE)
    if target.kind == "multi_site":
        scope["description"] = (
            "minimum representative configuration of the affected portal, "
            "documented and repeatable"
        )
        parameters = scope["parameters"]
        assert isinstance(parameters, dict)
        parameters["portals"] = [target.source]
        constraints = scope["constraints"]
        assert isinstance(constraints, list)
        constraints.insert(0, "only the affected portal")
    return scope


def _repo_relative(path: str) -> str:
    """Rewrite a path inside the repository as relative to its root.

    Relative paths and paths outside the repository are returned unchanged:
    the brief only re-anchors what it can, it never invents a path.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        return path
    try:
        resolved = candidate.resolve()
    except OSError:
        return path
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path
