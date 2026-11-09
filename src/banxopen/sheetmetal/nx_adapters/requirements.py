"""Brings the part's bead requirements in line with the beads on a body, as native NX Requirements through
Foundation's store, plus the bridge expressions their PAIR check reads."""

# NX 2412's Python stubs declare instance methods without `self`, type enum members as int and give collections
# no __iter__; these rules would only report those stub defects here. Everything else is still checked.
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportReturnType=false
# pyright: reportCallIssue=false, reportGeneralTypeIssues=false

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import NXOpen

from banxopen.foundation import (
    BodyId,
    FoundationCodes,
    OperationResult,
    PartRequirementStore,
    RequirementDiff,
    RequirementRead,
    RequirementSpec,
)
from banxopen.foundation.nx import BodyResolver, NxSessionContext, nx_error_detail
from banxopen.materials import MaterialsCodes, SideEffectInstruction
from banxopen.sheetmetal import (
    BEAD_DOMAIN,
    BEAD_FEATURE_NOUN,
    NOTHING_REBUILT,
    BeadSettings,
    BeadSpecLookup,
    FeatureInventory,
    RefreshRequirementsEffectRule,
    SheetMetalCodes,
    SheetMetalRequirementNames,
    SheetMetalRequirementReader,
    SheetMetalText,
    create_bead_requirements,
    requirement_to_specs,
    requirements_from_read,
    resolve_beads_on_body,
)
from banxopen.sheetmetal.core.requirements.requirements import RebuiltSpecs
from banxopen.sheetmetal.nx_adapters.features import find_expression
from banxopen.sheetmetal.nx_adapters.preferences import NxSheetMetalPreferences

_log = logging.getLogger(__name__)

_BRIDGE = (
    (SheetMetalRequirementNames.GRADE_EXPRESSION, SheetMetalRequirementNames.GRADE_EXPRESSION_FORMULA),
    (SheetMetalRequirementNames.PAIR_EXPRESSION, SheetMetalRequirementNames.PAIR_EXPRESSION_FORMULA),
)
"""Grade first: the pair expression refers to it."""


@dataclass(frozen=True, slots=True)
class BeadRequirementSync:
    changed: bool
    """False when the part already held what its beads impose."""
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Planned:
    specs: tuple[RequirementSpec, ...]
    notes: tuple[str, ...]
    held: RequirementRead


class SheetMetalRequirementService:
    """One requirement per SPEC, naming every bead built to it. Called by the bead tool on open (nothing applied) and
    inside its Apply undo mark (the SPEC just applied), and after a material assignment (nothing applied). Nothing
    applied only refreshes which beads each names and drops SPECs with none left; it never re-derives pairs."""

    def __init__(
        self,
        context: NxSessionContext,
        inventory: FeatureInventory,
        specs: BeadSpecLookup,
        settings: BeadSettings,
        preferences: NxSheetMetalPreferences,
        store: PartRequirementStore,
        reader: SheetMetalRequirementReader,
        text: SheetMetalText,
    ) -> None:
        self._context = context
        self._inventory = inventory
        self._specs = specs
        self._settings = settings
        self._preferences = preferences
        self._store = store
        self._reader = reader
        self._text = text

    def read(self) -> tuple[Sequence[object], Sequence[str]]:
        return self._reader.read_all()

    def audit_beads(self) -> Sequence[str]:
        """Diagnostic: every bead requirement check and the objects it holds, alive or dead."""
        return self._store.audit(BEAD_DOMAIN)

    def refresh_beads(self, body_id: BodyId, rebuilt: RebuiltSpecs) -> OperationResult[tuple[str, ...]]:
        """Rewrites the bead requirements for the body's beads. The caller holds the undo mark."""
        planned = self._plan(body_id, rebuilt)
        if not planned.ok or planned.value is None:
            return OperationResult.fail(
                planned.error_code or SheetMetalCodes.REQUIREMENT_REFRESH_FAILED, planned.message or ""
            )
        written = self._write(planned.value)
        if not written.ok:
            return OperationResult.fail(
                written.error_code or FoundationCodes.REQUIREMENT_WRITE_FAILED, written.message or ""
            )
        return OperationResult.success(planned.value.notes)

    def sync_beads(self, body_id: BodyId) -> OperationResult[BeadRequirementSync]:
        """Writes only when the part differs: beads added or deleted since, a legacy requirement, or a bridge
        expression missing or repointed."""
        planned = self._plan(body_id, NOTHING_REBUILT)
        if not planned.ok or planned.value is None:
            return OperationResult.fail(
                planned.error_code or SheetMetalCodes.REQUIREMENT_REFRESH_FAILED, planned.message or ""
            )
        plan = planned.value
        held = [r.spec for r in plan.held.requirements]
        if RequirementDiff.compute(held, plan.specs).is_empty and (not plan.specs or self._bridge_is_current()):
            return OperationResult.success(BeadRequirementSync(False, plan.notes))
        written = self._write(plan)
        if not written.ok:
            return OperationResult.fail(
                written.error_code or FoundationCodes.REQUIREMENT_WRITE_FAILED, written.message or ""
            )
        return OperationResult.success(BeadRequirementSync(True, plan.notes))

    def _plan(self, body_id: BodyId, rebuilt: RebuiltSpecs) -> OperationResult[_Planned]:
        """Fails only on what makes writing unsafe: unreadable beads, or requirements NX could not read (writing would
        delete them unseen). One this domain does not understand is rewritten from its SPEC family, with a note."""
        text = self._text
        inventory = self._inventory.read(body_id)
        if inventory.read_error is not None:
            return OperationResult.fail(
                SheetMetalCodes.BEAD_FEATURES_UNREADABLE, text.beads_unreadable_for_requirements(inventory.read_error)
            )
        held = self._store.read(BEAD_DOMAIN)
        if not held.ok:
            return OperationResult.fail(
                FoundationCodes.REQUIREMENTS_UNREADABLE, text.requirements_unreadable_for_refresh(" ".join(held.errors))
            )
        previous, unreadable = requirements_from_read(held, BEAD_FEATURE_NOUN, text)
        beads = resolve_beads_on_body(inventory, self._specs, self._settings)
        result = create_bead_requirements(
            beads, previous, rebuilt, self._preferences.read_for(body_id).body_thickness, text
        )
        specs = tuple(s for r in result.requirements for s in requirement_to_specs(r))
        # Only needed to write one: with no beads left, the refresh to nothing still removes the leftovers.
        if specs and find_expression(self._context.work_part, SheetMetalRequirementNames.THICKNESS_EXPRESSION) is None:
            return OperationResult.fail(
                SheetMetalCodes.THICKNESS_EXPRESSION_MISSING,
                text.thickness_expression_missing(SheetMetalRequirementNames.THICKNESS_EXPRESSION),
            )
        notes = tuple(text.requirement_rewritten(e) for e in unreadable) + result.notes
        return OperationResult.success(_Planned(specs, notes, held))

    def _write(self, plan: _Planned) -> OperationResult[None]:
        if plan.specs:
            bridge = self._ensure_bridge_expressions()
            if not bridge.ok:
                return bridge
        written = self._store.refresh(BEAD_DOMAIN, plan.specs)
        if not written.ok:
            return written
        for note in plan.notes:
            _log.warning(note)
        return OperationResult.success()

    def _bridge_is_current(self) -> bool:
        return all(
            (e := find_expression(self._context.work_part, name)) is not None and e.RightHandSide == formula
            for name, formula in _BRIDGE
        )

    def _ensure_bridge_expressions(self) -> OperationResult[None]:
        """Creates them, or points them back at their formulas, and makes them re-read the attribute: the grade
        expression reads a part attribute outside NX's update graph, so nothing marks it out of date and the PAIR check
        reports Unknown. Editing the formula (even to itself) and updating makes NX evaluate it now."""
        part = self._context.work_part
        for name, formula in _BRIDGE:
            try:
                expression = find_expression(part, name)
                if expression is None:
                    part.Expressions.CreateExpression("String", f"{name}={formula}")
                else:
                    part.Expressions.Edit(expression, formula)
            except NXOpen.NXException as ex:
                _log.error("The '%s' expression could not be written: %s", name, nx_error_detail(ex))
                return OperationResult.fail(
                    SheetMetalCodes.GRADE_EXPRESSION_FAILED,
                    self._text.grade_expression_failed(name, nx_error_detail(ex)),
                )
        self._update_now()
        return OperationResult.success()

    def _update_now(self) -> None:
        """On an invisible mark nested in the caller's: the caller's mark still takes the whole Apply back."""
        session = self._context.session
        mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, "Update requirement expressions")
        errors = session.UpdateManager.DoUpdate(mark)
        if errors:
            _log.warning("NX reported %s update error(s) while refreshing the requirement expressions.", errors)
        session.DeleteUndoMark(mark, None)


class RefreshRequirementsExecutor:
    """Executes REFRESH_SHEETMETAL_REQUIREMENTS inside the assignment's undo mark: makes sure the grade part attribute
    holds the grade of the row the preferences were just set to (covers a standards file that does not declare the
    column as an attribute), then refreshes the bead requirements. A failed refresh only warns: the assignment it
    follows has succeeded."""

    instruction_type = RefreshRequirementsEffectRule.INSTRUCTION_TYPE

    def __init__(
        self,
        context: NxSessionContext,
        preferences: NxSheetMetalPreferences,
        requirements: SheetMetalRequirementService,
        text: SheetMetalText,
    ) -> None:
        self._context = context
        self._preferences = preferences
        self._requirements = requirements
        self._text = text

    def execute(self, instruction: SideEffectInstruction, body: NXOpen.Body) -> OperationResult[None]:
        if instruction.instruction_type != self.instruction_type:
            return OperationResult.fail(
                MaterialsCodes.UNSUPPORTED_INSTRUCTION,
                f"RefreshRequirementsExecutor cannot execute instruction type '{instruction.instruction_type}'.",
            )
        grade = self.ensure_grade_attribute()
        if not grade.ok:
            return grade
        refreshed = self._requirements.refresh_beads(BodyResolver.body_id(body), NOTHING_REBUILT)
        if not refreshed.ok:
            _log.warning(
                "The bead requirements were not refreshed (%s): %s Re-open the Bead tool to rewrite them.",
                refreshed.error_code,
                refreshed.message,
            )
        return OperationResult.success()

    def ensure_grade_attribute(self) -> OperationResult[None]:
        preference = self._preferences.read_for_part().preference
        row = preference.row if preference is not None else None
        if row is None:
            return OperationResult.success()  # not a Material Table entry: no grade for any requirement to read
        part = self._context.work_part
        title = SheetMetalRequirementNames.GRADE_ATTRIBUTE
        try:
            has = part.HasUserAttribute(title, NXOpen.NXObject.AttributeType.String, -1)
            current = part.GetStringUserAttribute(title, -1) if has else None
            if current != row.grade:
                part.SetUserAttribute(title, -1, row.grade, NXOpen.Update.Option.Later)
                _log.info("Part attribute '%s' set to '%s' (was '%s').", title, row.grade, current or "unset")
            return OperationResult.success()
        except NXOpen.NXException as ex:
            return OperationResult.fail(
                SheetMetalCodes.GRADE_ATTRIBUTE_FAILED, self._text.grade_attribute_failed(title, nx_error_detail(ex))
            )
