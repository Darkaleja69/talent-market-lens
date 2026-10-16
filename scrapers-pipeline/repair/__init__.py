"""Deterministic core of the scraper-repair process (spec 004).

The package reads the daily diagnostic, selects the repair targets, builds
their briefs, enforces the incremental offer threshold and the quality gate
and writes the repair records. The business logic lives in these modules;
the CLI and the agents only coordinate inputs and outputs.
"""
