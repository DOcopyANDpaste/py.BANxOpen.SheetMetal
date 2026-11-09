"""Identifying an unstamped bead from its geometry, which SPEC each bead on a body is built to, and splitting curves
into chains."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from banxopen.sheetmetal.core.beads.settings import BeadSettings, BeadSpecColumns
from banxopen.sheetmetal.core.ports import BeadSpecLookup
from banxopen.sheetmetal.model.beads import (
    BeadGeometry,
    BeadOnBody,
    BeadSpecMatch,
    BeadSpecRow,
    BeadSpecStatus,
    BodyFeatureInventory,
    CurveEnds,
)


def match_bead_spec(geometry: BeadGeometry, rows: Iterable[BeadSpecRow], settings: BeadSettings) -> BeadSpecMatch:
    """A row matches when every mapped parameter is within the tolerance of its column. Only an unambiguous match
    identifies a bead: a guessed SPEC would enforce the wrong restriction with full confidence.

    The same (Standard, workbook, SPEC) reached twice is one SPEC, not an ambiguity. The workbook is part of the key:
    without it, two different rows sharing a SpecId would collapse into one confident wrong match.
    """
    seen: set[tuple[str, str, str]] = set()
    candidates: list[BeadSpecRow] = []
    for row in rows:
        fits = all(
            abs(geometry.value_of(m.feature) - BeadSpecColumns.value_of(row, m.spec_column))
            <= settings.geometry_match_tolerance
            for m in settings.parameter_mapping
        )
        key = (row.standard_id, row.workbook_name, row.spec_id)
        if fits and key not in seen:
            seen.add(key)
            candidates.append(row)
    return BeadSpecMatch(tuple(candidates))


def resolve_beads_on_body(
    inventory: BodyFeatureInventory, specs: BeadSpecLookup, settings: BeadSettings
) -> tuple[BeadOnBody, ...]:
    """Which SPEC each bead is built to: off its stamp, or for an unstamped bead from its geometry. The one place this
    is decided, so the material engine's constraint and the bead tool cannot disagree about a bead."""
    beads: list[BeadOnBody] = []
    for stamp in inventory.stamps:
        family = tuple(specs.find_all(stamp.standard_id, stamp.spec_id))
        beads.append(
            BeadOnBody(
                stamp.feature_key,
                stamp.name,
                BeadSpecStatus.STAMPED if family else BeadSpecStatus.STAMPED_SPEC_MISSING,
                stamp.standard_id,
                stamp.spec_id,
                family,
            )
        )

    all_specs: Sequence[BeadSpecRow] | None = None  # read once: an unstamped bead has no Standard, so all are searched
    for feature in inventory.unstamped_features:
        if feature.geometry is None:
            beads.append(BeadOnBody(feature.feature_key, feature.name, BeadSpecStatus.GEOMETRY_UNREADABLE, None, None))
            continue

        if all_specs is None:
            all_specs = specs.all_specs()
        match = match_bead_spec(feature.geometry, all_specs, settings)
        row = match.single
        if row is not None:
            # Widened to the whole family: a matched bead is rebuilt by a re-thicknessing exactly like a stamped one.
            # An empty family means the id spans two workbooks; the row is in hand, so the bead stays identified.
            family = tuple(specs.find_all(row.standard_id, row.spec_id)) or (row,)
            beads.append(
                BeadOnBody(
                    feature.feature_key, feature.name, BeadSpecStatus.MATCHED, row.standard_id, row.spec_id, family
                )
            )
        else:
            status = BeadSpecStatus.AMBIGUOUS if match.is_ambiguous else BeadSpecStatus.UNMATCHED
            beads.append(BeadOnBody(feature.feature_key, feature.name, status, None, None, (), match.candidates))
    return tuple(beads)


def group_curve_chains(curves: Sequence[CurveEnds], tolerance: float) -> tuple[tuple[int, ...], ...]:
    """Connected chains: two curves are in one when an end of one coincides with an end of the other, within
    `tolerance`, directly or through other curves. One chain is one bead; a branching chain is still one chain.

    Chains are ordered by their first curve in `curves`, and ids keep the input order: the order the user picked in.
    """
    parent = list(range(len(curves)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(curves)):  # quadratic, but a selection is tens of curves at most
        for j in range(i + 1, len(curves)):
            if _touch(curves[i], curves[j], tolerance):
                parent[find(j)] = find(i)

    chains: dict[int, list[int]] = {}
    for i, curve in enumerate(curves):
        chains.setdefault(find(i), []).append(curve.id)
    return tuple(tuple(chain) for chain in chains.values())


def _touch(a: CurveEnds, b: CurveEnds, tolerance: float) -> bool:
    return any(_near(p, q, tolerance) for p in (a.start, a.end) for q in (b.start, b.end))


def _near(p: tuple[float, float, float], q: tuple[float, float, float], tolerance: float) -> bool:
    return sum((x - y) ** 2 for x, y in zip(p, q, strict=True)) <= tolerance * tolerance
