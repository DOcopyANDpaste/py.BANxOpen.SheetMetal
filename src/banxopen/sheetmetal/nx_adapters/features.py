"""Building beads in NX: the curve Section, the SPEC's named expressions, the bead builder (commit or preview), the
direction arrow, and turning the dialog's selection blocks into chains and beads.

Every builder setting follows a journal recorded from NX's own Bead dialog (NX 2412); where this differs, the dialog
wins. Cross section is Circular, end type Formed; the side is absolute, so the same selection and SPEC always give the
same bead.
"""

# NX 2412's Python stubs declare instance methods without `self`, type enum members as int and give collections
# no __iter__; these rules would only report those stub defects here. Everything else is still checked.
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportReturnType=false
# pyright: reportCallIssue=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import NXOpen
import NXOpen.Features
import NXOpen.Features.SheetMetal

from banxopen.foundation import OperationResult
from banxopen.foundation.nx import NxSessionContext, expression_unlocked, lock_expression, nx_error_detail
from banxopen.sheetmetal import (
    BeadFeatureParameter,
    BeadSettings,
    BeadSpecRow,
    CurveEnds,
    SheetMetalCodes,
    group_curve_chains,
)
from banxopen.sheetmetal.nx_adapters import uf_modl
from banxopen.sheetmetal.nx_adapters.beads import is_bead_feature

_log = logging.getLogger(__name__)

HeightSide = Any
"""`NXOpen.Features.SheetMetal.BeadBuilder.HeightSideOptions`."""


def height_side(flipped: bool) -> HeightSide:
    """VERIFY on a live part that unflipped (the arrow as placed, along the face normal) is SectionNormalSide; swap
    the two here if the preview shows the opposite."""
    options = NXOpen.Features.SheetMetal.BeadBuilder.HeightSideOptions
    return options.SectionReverseNormalSide if flipped else options.SectionNormalSide


def create_curve_section(work_part: NXOpen.Part, chain: Sequence[NXOpen.NXObject]) -> NXOpen.Section:
    """A curves-only Section over one chain, with the part's modeling tolerances (chaining = 0.95 x distance, as NX
    records it): a parameterless section carries zero tolerances and fails the bead builder's validation. Also how a
    chain is put back into the dialog's curve block, which only takes Sections."""
    if not chain:
        raise ValueError("A bead needs at least one curve.")
    modeling = work_part.Preferences.Modeling
    distance = modeling.DistanceToleranceData
    section = work_part.Sections.CreateSection(distance * 0.95, distance, modeling.AngleToleranceData)
    section.SetAllowedEntityTypes(NXOpen.Section.AllowTypes.OnlyCurves)
    section.AllowSelfIntersection(True)
    section.AllowDegenerateCurves(False)
    rule = work_part.ScRuleFactory.CreateRuleBaseCurveDumb(list(chain))
    section.AddToSection([rule], None, None, None, NXOpen.Point3d(0.0, 0.0, 0.0), NXOpen.Section.Mode.Create, False)
    return section


def destroy_section(context: NxSessionContext, section: NXOpen.Section | None) -> None:
    """Destroys a Section no feature took, unless NX already freed it."""
    if section is None or not context.is_alive(section.Tag):
        return
    try:
        section.Destroy()
    except NXOpen.NXException as ex:
        _log.warning("Destroying an unused bead Section failed: %s", nx_error_detail(ex))


@dataclass(frozen=True, slots=True)
class SpecExpressions:
    depth: NXOpen.Expression
    radius: NXOpen.Expression
    die_radius: NXOpen.Expression


def _token(value: str) -> str:
    """Punctuation becomes '_', so B1005010-3 and B10050103 stay distinct."""
    return "".join(c if c.isascii() and c.isalnum() else "_" for c in value)


def _sanitize(value: str) -> str:
    """The naming before the Standard was dropped: punctuation removed."""
    return "".join(c for c in value if c.isalnum())


def ensure_spec_expressions(
    context: NxSessionContext, standard_id: str, spec_id: str, depth: float, radius: float, die_radius: float
) -> SpecExpressions:
    """One named user expression per SPEC value, `Bead_<SPEC>_Depth/_Radius/_DieRadius`, shared by every bead on the
    SPEC. Expressions an earlier version named `Bead_<Standard>_<SPEC>_*` are renamed in place, so beads bound to
    them keep their link. Locked against hand edits: a value typed over would put every bead on the SPEC off-spec."""
    unit = context.work_part.UnitCollection.GetBase("Length")
    prefix, legacy = f"Bead_{_token(spec_id)}", f"Bead_{_sanitize(standard_id)}_{_sanitize(spec_id)}"
    return SpecExpressions(
        _ensure_named(context, f"{prefix}_Depth", f"{legacy}_Depth", depth, unit),
        _ensure_named(context, f"{prefix}_Radius", f"{legacy}_Radius", radius, unit),
        _ensure_named(context, f"{prefix}_DieRadius", f"{legacy}_DieRadius", die_radius, unit),
    )


def find_expression(work_part: NXOpen.Part, name: str) -> NXOpen.Expression | None:
    """By name rather than FindObject-and-catch: that catch also swallowed a failed edit."""
    return next((e for e in work_part.Expressions if e.Name == name), None)


def _ensure_named(context: NxSessionContext, name: str, legacy_name: str, value: float, unit: Any) -> NXOpen.Expression:
    expressions = context.work_part.Expressions
    formula = repr(value)
    existing = find_expression(context.work_part, name)
    if existing is None and (legacy := find_expression(context.work_part, legacy_name)) is not None:
        with expression_unlocked(legacy):
            expressions.Rename(legacy, name)  # NX carries the rename into every formula that references it
        _log.debug("Expression '%s' renamed to '%s'.", legacy_name, name)
        existing = legacy
    if existing is not None:
        with expression_unlocked(existing):
            expressions.EditExpressionWithUnits(existing, unit, formula)
        lock_expression(existing)  # also locks one created before locking was introduced
        return existing
    created = expressions.CreateNumberExpression(f"{name}={formula}", unit)
    lock_expression(created)
    return created


class BeadPreview:
    """One bead's live preview: the open builder NX draws it from. `close()` removes it and destroys the builder and
    its Section without committing. Safe to close twice."""

    def __init__(self, context: NxSessionContext, builder: Any, section: NXOpen.Section | None) -> None:
        self._context = context
        self._builder: Any = builder
        self._section = section

    def close(self) -> None:
        builder, self._builder = self._builder, None
        if builder is None:
            return
        for step, action in (
            ("Removing a bead preview", builder.PreviewBuilder.DeletePreview),
            ("Destroying a bead preview builder", builder.Destroy),
        ):
            try:
                action()
            except NXOpen.NXException as ex:  # a preview NX already dropped must not stop the builder going
                _log.warning("%s failed: %s", step, nx_error_detail(ex))
        destroy_section(self._context, self._section)
        self._section = None


class BeadFeatureService:
    """Creates or updates one Bead feature from one chain of curves. Depth/Radius/DieRadius are bound to the SPEC's
    named expressions; which SPEC column supplies each comes from the bead settings, the same mapping that identifies an
    unstamped bead, so what is written and what is later recognised cannot disagree."""

    def __init__(self, context: NxSessionContext, settings: BeadSettings) -> None:
        self._context = context
        self._settings = settings

    def create_or_update(
        self,
        chain: Sequence[NXOpen.NXObject],
        spec: BeadSpecRow,
        existing: NXOpen.Features.Feature | None,
        side: HeightSide,
    ) -> OperationResult[NXOpen.Features.Feature]:
        """Create when `existing` is None; otherwise re-open its builder in place (its curves untouched)."""
        value = self._settings.spec_value_for
        try:
            # Before the builder opens: the interactive dialog never edits expressions with a builder open.
            expressions = ensure_spec_expressions(
                self._context,
                spec.standard_id,
                spec.spec_id,
                value(spec, BeadFeatureParameter.HEIGHT),
                value(spec, BeadFeatureParameter.RADIUS),
                value(spec, BeadFeatureParameter.DIE_RADIUS),
            )
        except NXOpen.NXException as ex:
            _log.error("Bead SPEC expressions could not be created: %s", nx_error_detail(ex))
            return OperationResult.fail(SheetMetalCodes.BEAD_EXPRESSIONS_FAILED, nx_error_detail(ex))

        builder = self._create_builder(existing)
        if not builder.ok:
            return OperationResult.fail(builder.error_code or "", builder.message or "")
        bead: Any = builder.value
        section: NXOpen.Section | None = None
        committed = False
        try:
            section = self._configure(bead, chain, existing, side)
            # The builder's own expressions are pointed at the named ones by formula: that is the associativity.
            bead.Height.SetFormula(expressions.depth.Name)
            bead.Radius.SetFormula(expressions.radius.Name)
            bead.DieRadius.SetFormula(expressions.die_radius.Name)
            invalid = self._validate(bead)
            if invalid is not None:
                return OperationResult.fail(SheetMetalCodes.BEAD_VALIDATION_FAILED, invalid)
            feature = bead.CommitFeature()
            committed = True
            return OperationResult.success(feature)
        except NXOpen.NXException as ex:
            _log.error("Bead feature create/update failed: %s", nx_error_detail(ex))
            return OperationResult.fail(SheetMetalCodes.BEAD_CREATE_FAILED, nx_error_detail(ex))
        finally:
            bead.Destroy()
            if not committed:  # a committed feature owns its Section
                destroy_section(self._context, section)

    def open_preview(
        self,
        chain: Sequence[NXOpen.NXObject],
        spec: BeadSpecRow,
        existing: NXOpen.Features.Feature | None,
        side: HeightSide,
    ) -> OperationResult[BeadPreview]:
        """NX's own preview of one bead, committing nothing. Values are literals: binding the named expressions would
        change the part just to show a preview."""
        builder = self._create_builder(existing)
        if not builder.ok:
            return OperationResult.fail(builder.error_code or "", builder.message or "")
        bead: Any = builder.value
        section: NXOpen.Section | None = None
        try:
            section = self._configure(bead, chain, existing, side)
            value = self._settings.spec_value_for
            bead.Height.SetFormula(repr(value(spec, BeadFeatureParameter.HEIGHT)))
            bead.Radius.SetFormula(repr(value(spec, BeadFeatureParameter.RADIUS)))
            bead.DieRadius.SetFormula(repr(value(spec, BeadFeatureParameter.DIE_RADIUS)))
            invalid = self._validate(bead)
            if invalid is not None:
                BeadPreview(self._context, bead, section).close()
                return OperationResult.fail(SheetMetalCodes.BEAD_VALIDATION_FAILED, invalid)
            bead.PreviewBuilder.Preview()
            return OperationResult.success(BeadPreview(self._context, bead, section))
        except NXOpen.NXException as ex:
            BeadPreview(self._context, bead, section).close()
            return OperationResult.fail(SheetMetalCodes.BEAD_PREVIEW_FAILED, nx_error_detail(ex))

    def _create_builder(self, existing: NXOpen.Features.Feature | None) -> OperationResult[Any]:
        try:
            return OperationResult.success(
                self._context.work_part.Features.SheetmetalManager.CreateBeadFeatureBuilder(existing)
            )
        except NXOpen.NXException as ex:
            _log.error("CreateBeadFeatureBuilder failed: %s", nx_error_detail(ex))
            return OperationResult.fail(SheetMetalCodes.BEAD_BUILDER_CREATE_FAILED, nx_error_detail(ex))

    def _configure(
        self, bead: Any, chain: Sequence[NXOpen.NXObject], existing: NXOpen.Features.Feature | None, side: HeightSide
    ) -> NXOpen.Section | None:
        """The Section created for a new bead, for the caller to destroy should no feature take it; None for an
        existing bead, whose Section is left untouched."""
        builder_type = NXOpen.Features.SheetMetal.BeadBuilder
        bead.IncludeRounding = True
        bead.CrossSectionType = builder_type.CrossSectionTypeOptions.Circular
        bead.EndType = builder_type.EndTypeOptions.Formed
        bead.HeightSide = side
        # Default true turns the previous sheet metal feature internal: hidden yet still building. The dialog sets it
        # off.
        bead.ParentFeatureInternal = False
        if existing is not None:
            return None
        # NX's own default for a new bead: linked to the preferences' Minimum Tool Clearance expression.
        preferences = self._context.work_part.Preferences.SheetMetalPreferences
        bead.MinimumToolClearance.SetFormula(preferences.GetMinimumToolClearance().Name)
        bead.Sketch = None  # as the recorded journal: no slave sketch, then the Section assigned
        section = create_curve_section(self._context.work_part, chain)
        bead.Section = section
        return section

    def _validate(self, bead: Any) -> str | None:
        validity = bead.ValidateBuilderData()
        if validity == 0:
            return None
        return (
            f"NX rejected the bead builder data (code {validity}): {uf_modl.fail_message(self._context.uf, validity)}"
        )


def find_direction(
    context: NxSessionContext,
    chain: Sequence[NXOpen.NXObject],
    existing: NXOpen.Features.Feature | None,
    body: NXOpen.Body,
) -> tuple[uf_modl.Point, uf_modl.Point] | None:
    """Where the direction arrow goes: the start of the bead's first curve, along the normal of the body face it lies
    on. Only places the arrow; every failure hides it rather than stopping the dialog."""
    try:
        curve = chain[0] if chain else _first_section_curve(existing)
        if curve is None:
            return None
        point = uf_modl.curve_point(context.uf, curve.Tag, 0.0)
        normal = uf_modl.nearest_face_normal(context.uf, [f.Tag for f in body.GetFaces()], point)
        return (point, normal) if normal is not None else None
    except (NXOpen.NXException, TypeError, IndexError, ValueError) as ex:
        _log.warning("Could not place the direction arrow: %s", ex)
        return None


def _first_section_curve(feature: NXOpen.Features.Feature | None) -> NXOpen.NXObject | None:
    if feature is None:
        return None
    return next((c for s in feature.GetSections() for c in s.GetOutputCurves()), None)


@dataclass(frozen=True, slots=True)
class SelectedItem:
    """One thing the bead tool works on: a chain of curves (a new bead) or an existing Bead feature."""

    curves: tuple[NXOpen.NXObject, ...]
    bead_feature: NXOpen.Features.Feature | None
    from_sketch: NXOpen.Sketch | None
    sources: tuple[NXOpen.NXObject, ...]
    """What the user picked for this item, so deleting its line takes exactly those out of the blocks."""


def expand_selection(
    context: NxSessionContext,
    curve_block_objects: Sequence[NXOpen.NXObject],
    feature_block_objects: Sequence[NXOpen.NXObject],
) -> tuple[SelectedItem, ...]:
    """The curve block (Sections) is split into connected chains, one bead each; the feature block keeps Bead features
    only. In selection order, no chain or feature twice. What cannot be built from is left out and traced."""
    items: list[SelectedItem] = []
    seen_chains: set[tuple[str, ...]] = set()
    sketch_of_curve: dict[str, NXOpen.Sketch] | None = None
    for obj in curve_block_objects:
        if not isinstance(obj, NXOpen.Section):
            _log.debug("Left out (not a curve or a Bead feature): %s", obj.JournalIdentifier)
            continue
        curves = _curves_of(obj)
        if not curves:
            _log.debug("Section %s: no curves", obj.JournalIdentifier)
            continue
        for chain in _split_into_chains(context, curves):
            key = tuple(sorted(str(c.Tag) for c in chain))
            if key in seen_chains:
                continue
            seen_chains.add(key)
            if sketch_of_curve is None:
                sketch_of_curve = _sketch_of_every_curve(context)
            items.append(SelectedItem(tuple(chain), None, _sketch_holding_all(chain, sketch_of_curve), tuple(chain)))
    seen_features: set[str] = set()
    for obj in feature_block_objects:
        if isinstance(obj, NXOpen.Features.Feature) and is_bead_feature(obj):
            if str(obj.Tag) not in seen_features:
                seen_features.add(str(obj.Tag))
                items.append(SelectedItem((), obj, None, (obj,)))
        else:
            _log.debug("Left out (not a Bead feature): %s", obj.JournalIdentifier)
    return tuple(items)


def _curves_of(section: NXOpen.Section) -> list[NXOpen.NXObject]:
    """The curves the section was built from, not GetOutputCurves (copies the section makes): a bead on one line is
    only accepted when the line is part of a sketch, and the traceback needs the real curves. Edges are refused."""
    curves: list[NXOpen.NXObject] = []
    seen: set[str] = set()
    try:
        for data in section.GetSectionData():
            for element in data.GetSectionElementsData():
                parent = element.GetSectionElementData1()[0]  # [verify] (parent, start_connector, ...)
                if parent is None or str(parent.Tag) in seen:
                    continue
                seen.add(str(parent.Tag))
                if isinstance(parent, NXOpen.Curve):
                    curves.append(parent)
    except NXOpen.NXException as ex:
        _log.warning("Could not read the curves of section '%s': %s", section.JournalIdentifier, nx_error_detail(ex))
        return []
    return curves


def _split_into_chains(context: NxSessionContext, curves: Sequence[NXOpen.NXObject]) -> list[list[NXOpen.NXObject]]:
    """By connected ends at the part's distance tolerance. A curve whose ends cannot be read stays a chain of its own:
    the user picked it, and the builder then says why it cannot be used."""
    if len(curves) == 1:
        return [list(curves)]
    ends: list[CurveEnds] = []
    unreadable: list[NXOpen.NXObject] = []
    for i, curve in enumerate(curves):
        try:
            ends.append(
                CurveEnds(
                    i, uf_modl.curve_point(context.uf, curve.Tag, 0.0), uf_modl.curve_point(context.uf, curve.Tag, 1.0)
                )
            )
        except (NXOpen.NXException, TypeError, IndexError) as ex:
            _log.warning("Could not read the end points of '%s': %s", curve.JournalIdentifier, ex)
            unreadable.append(curve)
    tolerance = context.work_part.Preferences.Modeling.DistanceToleranceData
    chains = [[curves[i] for i in chain] for chain in group_curve_chains(ends, tolerance)]
    return chains + [[c] for c in unreadable]


def _sketch_of_every_curve(context: NxSessionContext) -> dict[str, NXOpen.Sketch]:
    owners: dict[str, NXOpen.Sketch] = {}
    for sketch in context.work_part.Sketches:
        try:
            for geometry in sketch.GetAllGeometry():
                owners[str(geometry.Tag)] = sketch
        except NXOpen.NXException:
            pass  # a sketch that cannot be read names no chain
    return owners


def _sketch_holding_all(chain: Sequence[NXOpen.NXObject], owners: dict[str, NXOpen.Sketch]) -> NXOpen.Sketch | None:
    sketch: NXOpen.Sketch | None = None
    for curve in chain:
        owner = owners.get(str(curve.Tag))
        if owner is None or (sketch is not None and sketch.Tag != owner.Tag):
            return None
        sketch = owner
    return sketch
