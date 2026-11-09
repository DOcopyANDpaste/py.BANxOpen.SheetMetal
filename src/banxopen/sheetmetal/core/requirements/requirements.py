"""Sheet metal requirements held in the part: one per SPEC, written as a native NX Requirement through Foundation's
store, read back by every tool that judges the sheet metal, and checked by NX itself on every change."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Final

from banxopen.foundation import (
    BodyId,
    BodyKind,
    EqualsValueRule,
    FoundationCodes,
    PartRequirementStore,
    RequirementRead,
    RequirementSeverity,
    RequirementSpec,
    RequirementValueType,
    SetOfValuesRule,
)
from banxopen.materials import (
    ConstraintSeverity,
    Material,
    MaterialAssignmentRuleContext,
    MaterialConstraint,
    MaterialRuleOrder,
    SideEffectInstruction,
)
from banxopen.sheetmetal.codes import SheetMetalCodes
from banxopen.sheetmetal.core.materials.rules import BEAD_DOMAIN
from banxopen.sheetmetal.core.materials.table import SheetMetalStandardSelection
from banxopen.sheetmetal.core.ports import SheetMetalPreferenceReader, SheetMetalRequirementReader
from banxopen.sheetmetal.model.beads import BeadOnBody, BeadSpecRow, BeadSpecStatus
from banxopen.sheetmetal.model.requirements import (
    PAIR_SEPARATOR,
    MaterialThicknessPair,
    RequirementUser,
    SheetMetalAllowance,
    SheetMetalRequirement,
)
from banxopen.sheetmetal.model.table import SheetMetalMaterialRow
from banxopen.sheetmetal.model.thickness import format_thickness
from banxopen.sheetmetal.text import SheetMetalText

REQUIREMENT_DOMAIN: Final = "SHEETMETAL.REQUIREMENT"
BEAD_FEATURE_NOUN: Final = "Bead SPEC"

RebuiltSpecs = Mapping[tuple[str, str], BeadSpecRow]
"""The SPEC rows applied in this run, by (Standard, SPEC id)."""
NOTHING_REBUILT: Final[RebuiltSpecs] = {}


class SheetMetalRequirementNames:
    """Names the NX side of a sheet metal requirement relies on."""

    THICKNESS_EXPRESSION: Final = "Sheet_Metal_Material_Thickness"
    """Created by NX with the preferences' material assignment; the sheet thickness in inches."""
    GRADE_ATTRIBUTE: Final = "SheetMetal_Material"
    """The part attribute NX writes when the preferences select a row (the grade column is an attribute column)."""
    GRADE_EXPRESSION: Final = "BA_SM_Grade"
    """A string expression reading the grade attribute: checks evaluate expressions, not attributes."""
    GRADE_EXPRESSION_FORMULA: Final = f'ug_askPartAttrValue("{GRADE_ATTRIBUTE}")'
    PAIR_EXPRESSION: Final = "BA_SM_MatThk"
    """Grade and thickness as one value, "2024-O@0.0400", so one check judges the pair."""
    PAIR_EXPRESSION_FORMULA: Final = f'{GRADE_EXPRESSION} + "{PAIR_SEPARATOR}" + format("%.4f", {THICKNESS_EXPRESSION})'
    PAIR_ASPECT: Final = "PAIR"
    LEGACY_GRADE_ASPECT: Final = "GRADE"
    """Only read: a part written before PAIR is carried over, and its next refresh replaces it."""
    LEGACY_THICKNESS_ASPECT: Final = "THICKNESS"


_SOURCE_PREFIX: Final = "Source: "
_BUILT_AT_PREFIX: Final = "Built at: "
_BUILT_AT_SUFFIX: Final = " in"


def requirement_to_specs(requirement: SheetMetalRequirement) -> tuple[RequirementSpec, ...]:
    """The one Foundation spec a requirement is held as: a String set-of-values listing every allowed pair key,
    checked against the pair expression. Severity Error: a sheet the SPEC does not allow stays wrong until the features
    are re-applied, and the failing check keeps reminding the user."""
    allowance = requirement.allowance
    users_line = (
        "Used by: (no features)"
        if not requirement.users
        else f"Used by: {', '.join(u.label for u in requirement.users)}"
    )
    source_line = f"{_SOURCE_PREFIX}{requirement.source or 'unknown'}"
    # The NX name is the SPEC alone (BA_REQ_BEAD_B1005010_3_PAIR): the full identity stays in the bookkeeping line.
    alias = requirement.domain.rsplit(".", 1)[-1]
    return (
        RequirementSpec(
            domain=requirement.domain,
            key=requirement.key,
            aspect=SheetMetalRequirementNames.PAIR_ASPECT,
            value_type=RequirementValueType.STRING,
            rule=SetOfValuesRule(tuple(f'"{p.key}"' for p in allowance.pairs)),
            severity=RequirementSeverity.ERROR,
            checked_formula=SheetMetalRequirementNames.PAIR_EXPRESSION,
            description=(
                f"{requirement.label}: the sheet metal material must be one of {allowance.pairs_text}.",
                "If this fails, re-apply the features below with their tool for the new Sheet Metal Preferences.",
                f"{_BUILT_AT_PREFIX}{allowance.thickness_text}{_BUILT_AT_SUFFIX}",
                users_line,
                source_line,
            ),
            associated_object_keys=tuple(u.feature_key for u in requirement.users),
            name_stem=f"{alias}_{requirement.spec_id}",
        ),
    )


def requirements_from_read(
    read: RequirementRead, feature_noun: str, text: SheetMetalText | None = None
) -> tuple[tuple[SheetMetalRequirement, ...], tuple[str, ...]]:
    """The requirements held for one domain, and what could not be understood. A key whose value is not what this
    writes is an error, not an absence (a caller would treat a half-read requirement as none), unless no live feature
    uses it: such a leftover guards nothing and the next refresh deletes it."""
    text = text or SheetMetalText.DEFAULT
    requirements: list[SheetMetalRequirement] = []
    errors = list(read.errors)

    groups: dict[tuple[str, str], list[RequirementSpec]] = {}
    for stored in read.requirements:
        groups.setdefault((stored.spec.domain, stored.spec.key), []).append(stored.spec)

    for (domain, key), specs in groups.items():
        bar = key.find("|")
        standard_id, spec_id = (key[:bar], key[bar + 1 :]) if bar > 0 else ("", "")
        if not spec_id:
            errors.append(text.requirement_key_malformed(key))
            continue
        allowance = _allowance_of(specs)
        if allowance is None:
            if any(s.associated_object_keys for s in specs):
                errors.append(text.requirement_incomplete(feature_noun, spec_id, standard_id))
            continue
        users: dict[str, RequirementUser] = {}
        for spec in specs:
            for object_key in spec.associated_object_keys:
                users.setdefault(object_key, RequirementUser(object_key, object_key))
        source = next((s for s in (_source_of(spec.description) for spec in specs) if s is not None), "")
        requirements.append(
            SheetMetalRequirement(domain, feature_noun, standard_id, spec_id, allowance, tuple(users.values()), source)
        )
    return tuple(requirements), tuple(errors)


def _allowance_of(specs: Sequence[RequirementSpec]) -> SheetMetalAllowance | None:
    pair = next((s for s in specs if s.aspect == SheetMetalRequirementNames.PAIR_ASPECT), None)
    if pair is not None:
        built = _built_at_of(pair.description)
        if not isinstance(pair.rule, SetOfValuesRule) or built is None:
            return None
        pairs = [MaterialThicknessPair.parse_key(_unquoted(v)) for v in pair.rule.values]
        if any(p is None for p in pairs):
            return None
        return SheetMetalAllowance(built, tuple(p for p in pairs if p is not None))

    grade = next((s for s in specs if s.aspect == SheetMetalRequirementNames.LEGACY_GRADE_ASPECT), None)
    thickness = next((s for s in specs if s.aspect == SheetMetalRequirementNames.LEGACY_THICKNESS_ASPECT), None)
    if (
        grade is not None
        and thickness is not None
        and isinstance(grade.rule, SetOfValuesRule)
        and isinstance(thickness.rule, EqualsValueRule)
    ):
        return SheetMetalAllowance.at_built_thickness(thickness.rule.value, (_unquoted(v) for v in grade.rule.values))
    return None


def _unquoted(value: str) -> str:
    """NX compares a String set of values including the quotes; an older part may hold them without."""
    return value[1:-1] if len(value) >= 2 and value[0] == '"' and value[-1] == '"' else value


def _built_at_of(description: Sequence[str]) -> float | None:
    line = next((d for d in description if d.startswith(_BUILT_AT_PREFIX)), None)
    if line is None or not line.endswith(_BUILT_AT_SUFFIX):
        return None
    try:
        return float(line[len(_BUILT_AT_PREFIX) : -len(_BUILT_AT_SUFFIX)])
    except ValueError:
        return None


def _source_of(description: Sequence[str]) -> str | None:
    line = next((d for d in description if d.startswith(_SOURCE_PREFIX)), None)
    if line is None:
        return None
    source = line[len(_SOURCE_PREFIX) :]
    return None if source == "unknown" else source


class StoreRequirementReader:
    """The default reader: each sheet metal domain's requirements through the Foundation store."""

    def __init__(
        self,
        store: PartRequirementStore,
        domains: Sequence[tuple[str, str]] = ((BEAD_DOMAIN, BEAD_FEATURE_NOUN),),
        text: SheetMetalText | None = None,
    ) -> None:
        """`domains`: (domain, feature noun) pairs; a louver or joggle domain adds its own."""
        self._store = store
        self._domains = tuple(domains)
        self._text = text

    def read_all(self) -> tuple[Sequence[SheetMetalRequirement], Sequence[str]]:
        requirements: list[SheetMetalRequirement] = []
        errors: list[str] = []
        for domain, noun in self._domains:
            read, read_errors = requirements_from_read(self._store.read(domain), noun, self._text)
            requirements.extend(read)
            errors.extend(read_errors)
        return tuple(requirements), tuple(errors)


@dataclass(frozen=True, slots=True)
class BeadRequirements:
    requirements: tuple[SheetMetalRequirement, ...]
    notes: tuple[str, ...]
    """SPECs that could not be given a requirement, or were given one with a caveat, for the caller to show."""


def create_bead_requirements(
    beads: Sequence[BeadOnBody],
    previous: Sequence[SheetMetalRequirement],
    rebuilt: RebuiltSpecs,
    current_thickness: float | None,
    text: SheetMetalText | None = None,
) -> BeadRequirements:
    """The requirements a body's beads impose, one per SPEC. The built thickness and pairs, first match wins:
    1. the row applied in this run (Apply rewrote the SPEC's shared expressions);
    2. what the part's existing requirement records: kept, never re-derived from the current sheet, or a refresh after
       a native thickness change would quietly re-bless beads still on the old row;
    3. for a SPEC with no requirement yet, the family row at the sheet's current thickness, said so in the notes.
    """
    text = text or SheetMetalText.DEFAULT
    previous_by_key: dict[str, SheetMetalRequirement] = {}
    for requirement in previous:
        if requirement.domain == BEAD_DOMAIN:
            previous_by_key.setdefault(requirement.key, requirement)

    identified = (BeadSpecStatus.STAMPED, BeadSpecStatus.STAMPED_SPEC_MISSING, BeadSpecStatus.MATCHED)
    groups: dict[tuple[str, str], list[BeadOnBody]] = {}
    for bead in beads:
        if bead.standard_id is not None and bead.spec_id is not None and bead.status in identified:
            groups.setdefault((bead.standard_id, bead.spec_id), []).append(bead)

    requirements: list[SheetMetalRequirement] = []
    notes: list[str] = []
    for (standard_id, spec_id), group in sorted(groups.items()):
        key = SheetMetalRequirement.key_for(standard_id, spec_id)
        users = tuple(
            RequirementUser(
                b.feature_key, f"{b.name} (identified by geometry)" if b.status is BeadSpecStatus.MATCHED else b.name
            )
            for b in sorted(group, key=lambda b: b.name)
        )

        allowance: SheetMetalAllowance | None = None
        source = ""
        applied = rebuilt.get((standard_id, spec_id))
        existing = previous_by_key.get(key)
        baseline = (
            next((row for row in (b.spec_at(current_thickness) for b in group) if row is not None), None)
            if current_thickness is not None
            else None
        )
        if applied is not None:
            allowance = SheetMetalAllowance.from_family(_family_of(group, applied), applied.thickness)
            source = applied.workbook_name
        elif existing is not None:
            allowance = existing.allowance
            source = existing.source
        elif current_thickness is not None and baseline is not None:
            allowance = SheetMetalAllowance.from_family(_family_of(group, baseline), baseline.thickness)
            source = f"{baseline.workbook_name} (baseline from the sheet's thickness when first recorded)"
            notes.append(
                text.requirement_recorded_at_current_thickness(
                    BEAD_FEATURE_NOUN, spec_id, standard_id, format_thickness(current_thickness)
                )
            )
        elif current_thickness is not None:
            notes.append(
                text.requirement_not_recorded_not_driven(
                    BEAD_FEATURE_NOUN, spec_id, standard_id, format_thickness(current_thickness)
                )
            )
        else:
            notes.append(text.requirement_not_recorded_thickness_unknown(BEAD_FEATURE_NOUN, spec_id, standard_id))

        if allowance is not None:
            requirements.append(
                SheetMetalRequirement(BEAD_DOMAIN, BEAD_FEATURE_NOUN, standard_id, spec_id, allowance, users, source)
            )
    return BeadRequirements(tuple(requirements), tuple(notes))


def _family_of(group: Sequence[BeadOnBody], row: BeadSpecRow) -> tuple[BeadSpecRow, ...]:
    return next((b.spec_family for b in group if b.spec_family), (row,))


class SheetMetalRequirementConstraintProvider:
    """What the requirements held in the part allow: the same requirements NX checks after a native preferences change,
    so the dialog and NX cannot disagree. A material is acceptable when at least one of its rows under the chosen
    Standard satisfies every requirement; the row gate then judges the row picked. Both only warn. Requirements are
    part-level, so they apply to the part's sheet metal bodies only."""

    domain_id = REQUIREMENT_DOMAIN

    def __init__(
        self,
        requirements: SheetMetalRequirementReader,
        preferences: SheetMetalPreferenceReader,
        selection: SheetMetalStandardSelection,
        text: SheetMetalText | None = None,
    ) -> None:
        self._requirements = requirements
        self._preferences = preferences
        self._selection = selection
        self._text = text or SheetMetalText.DEFAULT

    def constraints_for(self, body_id: BodyId) -> Sequence[MaterialConstraint]:
        if not self._preferences.read_for(body_id).is_sheet_metal_body:
            return ()
        requirements, errors = self._requirements.read_all()
        if errors:
            joined = " ".join(errors)
            text = self._text
            return (
                MaterialConstraint(
                    domain_id=self.domain_id,
                    source_label=text.source_part_requirements,
                    reason_code=FoundationCodes.REQUIREMENTS_UNREADABLE,
                    is_satisfied_by=lambda _m: False,
                    describe_violation=lambda _m: text.requirements_unreadable_for_assignment(joined),
                    severity=ConstraintSeverity.WARN,
                    violation_args=lambda _m: {"error": joined},
                ),
            )
        return tuple(self._constraint_for(r) for r in requirements if r.is_in_use)

    def _constraint_for(self, requirement: SheetMetalRequirement) -> MaterialConstraint:
        selection = self._selection
        allowance = requirement.allowance
        text = self._text

        def satisfied(material: Material) -> bool:
            rows = selection.rows_for(material.name)
            return not rows or any(allowance.is_satisfied_by(r.thickness, r.grade) for r in rows)

        return MaterialConstraint(
            domain_id=self.domain_id,
            source_label=requirement.label,
            reason_code=SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_REQUIREMENT,
            is_satisfied_by=satisfied,
            describe_violation=lambda m: text.requirement_material_not_allowed(
                m.name, requirement.label, allowance.pairs_text
            ),
            severity=ConstraintSeverity.WARN,
            violation_args=lambda m: _args_for(requirement, selection.rows_for(m.name)),
        )


def _args_for(requirement: SheetMetalRequirement, rows: Sequence[SheetMetalMaterialRow]) -> Mapping[str, str]:
    allowance = requirement.allowance
    grades: dict[str, str] = {}
    for row in rows:
        grades.setdefault(row.grade.casefold(), row.grade)
    return {
        "reason": "gradeNotAllowed"
        if any(allowance.allows_thickness(r.thickness) for r in rows)
        else "thicknessNotAllowed",
        "builtThickness": allowance.thickness_text,
        "grades": "/".join(grades.values()),
        "allowedPairs": allowance.pairs_text,
    }


class RefreshRequirementsEffectRule:
    """After a material is assigned to a sheet metal body, refreshes the part's requirements: which features each names,
    dropping those whose features are gone. Never changes what a requirement allows: only the feature tools do that.
    Ordered after the preference sync, so the grade attribute is set first."""

    INSTRUCTION_TYPE: Final = "REFRESH_SHEETMETAL_REQUIREMENTS"
    rule_id = INSTRUCTION_TYPE
    order = MaterialRuleOrder.SideEffect.DOMAIN_STATE + 10

    @property
    def instruction_types(self) -> Set[str]:
        return frozenset((self.INSTRUCTION_TYPE,))

    def generate_effects(self, context: MaterialAssignmentRuleContext) -> Sequence[SideEffectInstruction]:
        if context.target_body.kind is not BodyKind.SHEET_METAL:
            return ()
        return (SideEffectInstruction(self.INSTRUCTION_TYPE, context.target_body.id, {}),)
