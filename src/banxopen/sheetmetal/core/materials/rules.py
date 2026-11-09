"""What sheet metal contributes to the shared material engine: constraints, the picked-row gate, and the preference
sync effect."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Final

from banxopen.foundation import BodyId, BodyKind, RuleDecision, RuleOutcome
from banxopen.materials import (
    ConstraintSeverity,
    Material,
    MaterialAssignmentRuleContext,
    MaterialConstraint,
    MaterialRuleOrder,
    SideEffectInstruction,
)
from banxopen.sheetmetal.codes import SheetMetalCodes
from banxopen.sheetmetal.core.beads.matching import resolve_beads_on_body
from banxopen.sheetmetal.core.beads.settings import BeadSettings
from banxopen.sheetmetal.core.materials.table import SheetMetalMaterialTable, SheetMetalStandardSelection
from banxopen.sheetmetal.core.ports import (
    BeadSpecLookup,
    FeatureInventory,
    SheetMetalPreferenceReader,
    SheetMetalRequirementReader,
)
from banxopen.sheetmetal.model.beads import BeadOnBody, BeadSpecRow, BeadSpecStatus
from banxopen.sheetmetal.model.requirements import SheetMetalAllowance, SheetMetalRequirement
from banxopen.sheetmetal.model.table import SheetMetalMaterialColumns, SheetMetalMaterialRow
from banxopen.sheetmetal.model.thickness import format_thickness, thickness_matches
from banxopen.sheetmetal.text import SheetMetalText

BEAD_DOMAIN: Final = "SHEETMETAL.BEAD"
PREFERENCES_DOMAIN: Final = "SHEETMETAL.PREFERENCES"


def _never(_: Material) -> bool:
    return False


def _advisory(domain: str, source: str, code: str, message: str, args: Mapping[str, str]) -> MaterialConstraint:
    """A WARN that is always unsatisfied: surfaced, never enforced."""
    return MaterialConstraint(
        domain_id=domain,
        source_label=source,
        reason_code=code,
        is_satisfied_by=_never,
        describe_violation=lambda _m: message,
        severity=ConstraintSeverity.WARN,
        violation_args=lambda _m: args,
    )


@dataclass(frozen=True, slots=True)
class _ApplicableSpec:
    """A SPEC that applies to the body, off a stamp or matched from geometry. An empty family is a stamped SPEC that
    could not be found."""

    standard_id: str
    spec_id: str
    family: tuple[BeadSpecRow, ...]
    label: str


class BeadMaterialConstraintProvider:
    """What the beads already on a body allow its material to be: the reverse of `MaterialAllowedRule`, answered
    through the same `BeadSpecRow.is_allowed_for`, so the two directions cannot disagree.

    Deliberately thickness-blind: the engine asks about a physical material, and how thick the sheet becomes is only
    settled by the row the user picks (`SheetMetalRowGateRule`). So this refuses only a grade no row of the SPEC allows
    at any thickness.

    Everything here WARNs, never blocks: feature validation must not stand in the way of a preference update, but must
    not be silent either. SPECs the part already holds a requirement for are judged by the requirement provider instead
    (thickness-exact, what NX checks), except for a warning when the workbook no longer agrees with the requirement.
    """

    domain_id = BEAD_DOMAIN

    def __init__(
        self,
        inventory: FeatureInventory,
        specs: BeadSpecLookup,
        table: SheetMetalMaterialTable,
        settings: BeadSettings,
        requirements: SheetMetalRequirementReader,
        text: SheetMetalText | None = None,
    ) -> None:
        self._inventory = inventory
        self._specs = specs
        self._table = table
        self._settings = settings
        self._requirements = requirements
        self._text = text or SheetMetalText.DEFAULT

    def constraints_for(self, body_id: BodyId) -> Sequence[MaterialConstraint]:
        text = self._text
        inventory = self._inventory.read(body_id)
        if inventory.is_empty:
            return ()
        if inventory.read_error is not None:
            error = inventory.read_error
            return (
                _advisory(
                    self.domain_id,
                    text.source_body_features,
                    SheetMetalCodes.BEAD_FEATURES_UNREADABLE,
                    text.bead_features_unreadable(error),
                    {"error": error},
                ),
            )

        applicable: list[_ApplicableSpec] = []
        warnings: list[MaterialConstraint] = []
        for bead in resolve_beads_on_body(inventory, self._specs, self._settings):
            if bead.standard_id is not None and bead.spec_id is not None and (bead.is_stamped or bead.is_spec_known):
                label = (
                    text.source_stamped_bead(bead.spec_id, bead.standard_id)
                    if bead.is_stamped
                    else text.source_matched_bead(bead.name, bead.spec_id, bead.standard_id)
                )
                applicable.append(_ApplicableSpec(bead.standard_id, bead.spec_id, bead.spec_family, label))
            else:
                warnings.append(
                    _advisory(
                        self.domain_id,
                        text.source_unidentified_bead(bead.name),
                        SheetMetalCodes.BEAD_SPEC_UNMATCHED,
                        self._unidentified_message(bead),
                        _unidentified_args(bead),
                    )
                )

        # One constraint per SPEC; the first label, a stamped one where there is one, names it.
        distinct: dict[tuple[str, str], _ApplicableSpec] = {}
        for spec in applicable:
            distinct.setdefault((spec.standard_id, spec.spec_id), spec)
        specs = list(distinct.values())

        requirements, errors = self._requirements.read_all()
        covered = {r.key: r for r in requirements if r.domain == BEAD_DOMAIN and r.is_in_use}

        constraints: list[MaterialConstraint] = []
        for spec in specs:
            requirement = covered.get(SheetMetalRequirement.key_for(spec.standard_id, spec.spec_id))
            if requirement is not None and (drift := self._drift(requirement, spec.family)) is not None:
                constraints.append(
                    _advisory(self.domain_id, spec.label, SheetMetalCodes.REQUIREMENT_OUT_OF_DATE, drift[0], drift[1])
                )

        # With requirements that could not all be read nothing is handed over: the workbook check keeps every bead
        # checked even if it is not registered.
        if not errors:
            specs = [s for s in specs if SheetMetalRequirement.key_for(s.standard_id, s.spec_id) not in covered]

        if specs:
            table = self._table
            constraints.append(
                MaterialConstraint(
                    domain_id=self.domain_id,
                    source_label=specs[0].label if len(specs) == 1 else text.source_bead_spec_count(len(specs)),
                    reason_code=SheetMetalCodes.MATERIAL_GRADE_UNRECOGNIZED,
                    is_satisfied_by=lambda m: table.grade_for_physical_material(m.name) is not None,
                    describe_violation=lambda m: text.bead_grade_unrecognized(m.name),
                    severity=ConstraintSeverity.WARN,
                )
            )
        constraints.extend(self._constraint_for(spec) for spec in specs)
        constraints.extend(warnings)
        return tuple(constraints)

    def _unidentified_message(self, bead: BeadOnBody) -> str:
        match bead.status:
            case BeadSpecStatus.GEOMETRY_UNREADABLE:
                return self._text.bead_geometry_unreadable(bead.name)
            case BeadSpecStatus.AMBIGUOUS:
                return self._text.bead_ambiguous(bead.name, ", ".join(c.spec_id for c in bead.candidates))
            case _:
                return self._text.bead_unmatched(bead.name)

    def _constraint_for(self, spec: _ApplicableSpec) -> MaterialConstraint:
        text = self._text
        if not spec.family:
            return _advisory(
                self.domain_id,
                spec.label,
                SheetMetalCodes.BEAD_SPEC_NOT_FOUND,
                text.bead_spec_not_found(spec.spec_id, spec.standard_id),
                {"spec": spec.spec_id, "standard": spec.standard_id},
            )

        table = self._table
        family = spec.family

        def allowed(material: Material) -> bool:
            # An unmapped grade passes here on purpose: the grade-recognition constraint warns about it already.
            grade = table.grade_for_physical_material(material.name)
            return grade is None or any(row.is_allowed_for(grade) for row in family)

        return MaterialConstraint(
            domain_id=self.domain_id,
            source_label=spec.label,
            reason_code=SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD,
            is_satisfied_by=allowed,
            describe_violation=lambda m: text.bead_material_not_allowed(
                m.name, table.grade_for_physical_material(m.name), spec.spec_id
            ),
            severity=ConstraintSeverity.WARN,
            violation_args=lambda m: {"spec": spec.spec_id, "grade": table.grade_for_physical_material(m.name) or ""},
        )

    def _drift(
        self, requirement: SheetMetalRequirement, family: Sequence[BeadSpecRow]
    ) -> tuple[str, Mapping[str, str]] | None:
        """Why the requirement no longer matches its workbook, or None. It still governs: it is what the beads were
        built to; re-applying them would change it."""
        if not family:
            return None
        recorded = requirement.allowance
        workbook = SheetMetalAllowance.from_family(family, recorded.built_thickness)
        if workbook.same_pairs_as(recorded):
            return None
        args = {
            "requirement": requirement.label,
            "builtThickness": recorded.thickness_text,
            "workbookPairs": workbook.pairs_text,
            "recordedPairs": recorded.pairs_text,
        }
        return self._text.requirement_pairs_changed(requirement.label, workbook.pairs_text, recorded.pairs_text), args


def _unidentified_args(bead: BeadOnBody) -> Mapping[str, str]:
    status = {
        BeadSpecStatus.GEOMETRY_UNREADABLE: "geometryUnreadable",
        BeadSpecStatus.AMBIGUOUS: "ambiguous",
    }.get(bead.status, "noMatch")
    return {"feature": bead.name, "status": status, "candidates": ", ".join(c.spec_id for c in bead.candidates)}


class SheetMetalPreferenceConstraintProvider:
    """What a sheet metal body's Sheet Metal Preferences allow. Assigning a material also sets the preferences to a row
    made of it, so this refuses up front any material that sync could not be carried out for: one with no row, or any
    material when the preferences could not be read. A material that merely differs from the preferences is allowed:
    the sync brings them in line. More than one sheet metal body in the part warns."""

    domain_id = PREFERENCES_DOMAIN

    def __init__(
        self, reader: SheetMetalPreferenceReader, table: SheetMetalMaterialTable, text: SheetMetalText | None = None
    ) -> None:
        self._reader = reader
        self._table = table
        self._text = text or SheetMetalText.DEFAULT

    def constraints_for(self, body_id: BodyId) -> Sequence[MaterialConstraint]:
        text = self._text
        read = self._reader.read_for(body_id)
        if not read.is_sheet_metal_body:
            return ()

        preference = read.preference
        if preference is None:
            error = read.read_error or text.no_preferences_returned
            return (
                MaterialConstraint(
                    domain_id=self.domain_id,
                    source_label=text.source_preferences,
                    reason_code=SheetMetalCodes.PREFERENCES_UNREADABLE,
                    is_satisfied_by=_never,
                    describe_violation=lambda _m: text.preferences_unreadable_for_assignment(error),
                    violation_args=lambda _m: {"error": error},
                ),
            )

        table = self._table
        column = SheetMetalMaterialColumns.PHYSICAL_MATERIAL_NAME
        constraints = [
            MaterialConstraint(
                domain_id=self.domain_id,
                source_label=text.source_preferences,
                reason_code=SheetMetalCodes.PREFERENCE_MATERIAL_NOT_IN_TABLE,
                is_satisfied_by=lambda m: table.first_row_for_physical_material(m.name) is not None,
                describe_violation=lambda m: text.preference_material_has_no_row(m.name, column),
                violation_args=lambda _m: {"column": column},
            )
        ]
        count = preference.sheet_metal_body_count
        if count > 1:
            constraints.append(
                _advisory(
                    self.domain_id,
                    text.source_preferences,
                    SheetMetalCodes.PREFERENCE_SHARED_BY_BODIES,
                    text.preferences_shared_by_bodies(count),
                    {"count": str(count)},
                )
            )
        return tuple(constraints)


class SheetMetalRowGateRule:
    """Checks the standards-file row the user picked for a sheet metal body (`requested_variant`), which the sync then
    sets as the part's preferences:
    - a row that is not one of the material's rows under the chosen Standard is refused;
    - a row the part's requirements do not allow, or allow at another thickness than the features were built at, is
      warned about (the requirement's failing NX check keeps reminding the user); unreadable requirements warn;
    - a row whose thickness is not the body's needs confirming (usually a mistake, but the user's to make). A
      thickness NX will not report counts as matching.
    A body that is not sheet metal, or no row picked, is allowed: nothing to check."""

    rule_id = "SHEETMETAL_PREFERENCE_ROW"
    order = MaterialRuleOrder.Validation.CONFIRMATION

    def __init__(
        self,
        reader: SheetMetalPreferenceReader,
        selection: SheetMetalStandardSelection,
        requirements: SheetMetalRequirementReader,
        text: SheetMetalText | None = None,
    ) -> None:
        self._reader = reader
        self._selection = selection
        self._requirements = requirements
        self._text = text or SheetMetalText.DEFAULT

    def evaluate(self, context: MaterialAssignmentRuleContext) -> RuleOutcome:
        row_name = context.requested_variant
        if context.target_body.kind is not BodyKind.SHEET_METAL or row_name is None:
            return self._allow()

        material = context.requested_material.name
        row = next((r for r in self._selection.rows_for(material) if r.name == row_name), None)
        if row is None:
            standard = self._selection.selected
            return RuleOutcome(
                self.rule_id,
                RuleDecision.BLOCK,
                SheetMetalCodes.ROW_NOT_OFFERED,
                self._text.row_not_offered(row_name, material, standard),
                args={"row": row_name, "material": material, "standard": standard or ""},
            )

        requirements, errors = self._requirements.read_all()
        warning = (
            self._unreadable_warning(row)
            if errors
            else self._requirement_warning_for(row, [r for r in requirements if r.is_in_use])
        )

        # Unreadable preferences are refused by the preference constraint; there is no thickness to compare here.
        body_thickness = self._reader.read_for(context.target_body.id).body_thickness
        if body_thickness is not None and not thickness_matches(body_thickness, row.thickness):
            message = self._text.row_thickness_mismatch(
                row.name, format_thickness(row.thickness), format_thickness(body_thickness)
            )
            # Only one outcome is returned and a confirmation outranks a warning, so the warning rides along.
            return RuleOutcome(
                self.rule_id,
                RuleDecision.REQUIRE_CONFIRMATION,
                SheetMetalCodes.ROW_THICKNESS_MISMATCH,
                message if warning is None else f"{message} {warning.message}",
                args={
                    "row": row.name,
                    "rowThickness": format_thickness(row.thickness),
                    "bodyThickness": format_thickness(body_thickness),
                },
            )
        return warning or self._allow()

    def _allow(self) -> RuleOutcome:
        return RuleOutcome(self.rule_id, RuleDecision.ALLOW, None, None)

    def _unreadable_warning(self, row: SheetMetalMaterialRow) -> RuleOutcome:
        return RuleOutcome(
            self.rule_id,
            RuleDecision.WARN,
            SheetMetalCodes.ROW_REFUSED_BY_REQUIREMENT,
            self._text.row_refused_requirements_unreadable,
            args={
                "row": row.name,
                "rowThickness": format_thickness(row.thickness),
                "refusal": "requirementsUnreadable",
            },
        )

    def _requirement_warning_for(
        self, row: SheetMetalMaterialRow, requirements: Sequence[SheetMetalRequirement]
    ) -> RuleOutcome | None:
        row_thickness = format_thickness(row.thickness)
        refusing = next((r for r in requirements if not r.allowance.is_satisfied_by(row.thickness, row.grade)), None)
        if refusing is not None:
            return RuleOutcome(
                self.rule_id,
                RuleDecision.WARN,
                SheetMetalCodes.ROW_REFUSED_BY_REQUIREMENT,
                self._text.row_pair_not_allowed(
                    row.name, refusing.label, row.grade, row_thickness, refusing.allowance.pairs_text
                ),
                args={
                    "row": row.name,
                    "rowThickness": row_thickness,
                    "refusal": "pairNotAllowed",
                    "requirement": refusing.label,
                    "grade": row.grade,
                    "builtThickness": refusing.allowance.thickness_text,
                    "allowedPairs": refusing.allowance.pairs_text,
                },
            )
        # Allowed, but the features carry the built row's geometry: the NX check passes, so the user hears it here.
        rebuild = next((r for r in requirements if not r.allowance.matches_built_thickness(row.thickness)), None)
        if rebuild is not None:
            return RuleOutcome(
                self.rule_id,
                RuleDecision.WARN,
                SheetMetalCodes.ROW_NEEDS_REAPPLY,
                self._text.row_needs_reapply(row.name, rebuild.label, rebuild.allowance.thickness_text, row_thickness),
                args={
                    "row": row.name,
                    "rowThickness": row_thickness,
                    "requirement": rebuild.label,
                    "builtThickness": rebuild.allowance.thickness_text,
                },
            )
        return None


class SyncSheetMetalPreferenceEffectRule:
    """When a material is assigned to a sheet metal body, emits SYNC_SHEETMETAL_PREFERENCE_MATERIAL carrying the row the
    user picked (`requested_variant`, already checked by the row gate). Emits nothing when no row was picked, rather
    than choosing one itself."""

    INSTRUCTION_TYPE: Final = "SYNC_SHEETMETAL_PREFERENCE_MATERIAL"
    MATERIAL_NAME_DATA_KEY: Final = "MaterialName"
    """The standards file row name to set: a `str`."""

    rule_id = INSTRUCTION_TYPE
    order = MaterialRuleOrder.SideEffect.DOMAIN_STATE
    """After the display material sync."""

    def __init__(self, table: SheetMetalMaterialTable) -> None:
        self._table = table

    @property
    def instruction_types(self) -> Set[str]:
        return frozenset((self.INSTRUCTION_TYPE,))

    def generate_effects(self, context: MaterialAssignmentRuleContext) -> Sequence[SideEffectInstruction]:
        if context.target_body.kind is not BodyKind.SHEET_METAL or context.requested_variant is None:
            return ()
        row = self._table.find(context.requested_variant)
        # The gate refuses another material's row; this only guards a caller that skipped planning.
        if row is None or row.physical_material_name.casefold() != context.requested_material.name.casefold():
            return ()
        return (
            SideEffectInstruction(
                self.INSTRUCTION_TYPE, context.target_body.id, {self.MATERIAL_NAME_DATA_KEY: row.name}
            ),
        )
