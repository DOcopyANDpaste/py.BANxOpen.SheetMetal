"""Validating one candidate SPEC against one sheet metal profile, and finding the SPECs that validate."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from banxopen.foundation import GateRule, RuleDecision, RuleOutcome
from banxopen.sheetmetal.codes import SheetMetalCodes
from banxopen.sheetmetal.model.beads import BeadSpecRow
from banxopen.sheetmetal.model.table import SheetMetalProfile
from banxopen.sheetmetal.model.thickness import format_thickness, thickness_matches
from banxopen.sheetmetal.text import SheetMetalText


@dataclass(frozen=True, slots=True)
class BeadValidationContext:
    profile: SheetMetalProfile
    candidate: BeadSpecRow


BeadValidationRule = GateRule[BeadValidationContext, RuleOutcome]


class ThicknessMatchRule:
    """The candidate SPEC's THICKNESS must match the sheet metal's thickness, within the tolerance."""

    rule_id = "THICKNESS_MATCH"
    order = 100

    def __init__(self, text: SheetMetalText | None = None) -> None:
        self._text = text or SheetMetalText.DEFAULT

    def evaluate(self, context: BeadValidationContext) -> RuleOutcome:
        if thickness_matches(context.profile.thickness, context.candidate.thickness):
            return RuleOutcome(self.rule_id, RuleDecision.ALLOW, None, None)

        spec_thickness = format_thickness(context.candidate.thickness)
        sheet_thickness = format_thickness(context.profile.thickness)
        return RuleOutcome(
            self.rule_id,
            RuleDecision.BLOCK,
            SheetMetalCodes.THICKNESS_MISMATCH,
            self._text.bead_thickness_mismatch(context.candidate.spec_id, spec_thickness, sheet_thickness),
            args={
                "spec": context.candidate.spec_id,
                "specThickness": spec_thickness,
                "sheetThickness": sheet_thickness,
            },
        )


class MaterialAllowedRule:
    """The sheet metal's grade must be one of the SPEC's "Allowed Material" YES columns. Tells "grade is not a column
    at all" (likely a SheetMetal_Material value no workbook spells the same way) from "marked '-'" (a genuine
    restriction): the two call for different user action."""

    rule_id = "MATERIAL_ALLOWED"
    order = 200

    def __init__(self, text: SheetMetalText | None = None) -> None:
        self._text = text or SheetMetalText.DEFAULT

    def evaluate(self, context: BeadValidationContext) -> RuleOutcome:
        grade = context.profile.material_grade_label
        if grade is None or not grade.strip():
            return RuleOutcome(
                self.rule_id, RuleDecision.BLOCK, SheetMetalCodes.MATERIAL_MISSING, self._text.bead_material_missing
            )

        candidate = context.candidate
        if candidate.is_allowed_for(grade):
            return RuleOutcome(self.rule_id, RuleDecision.ALLOW, None, None)

        if grade not in candidate.allowed_material_grades:
            return RuleOutcome(
                self.rule_id,
                RuleDecision.BLOCK,
                SheetMetalCodes.MATERIAL_GRADE_UNRECOGNIZED,
                self._text.bead_grade_not_a_column(grade, candidate.standard_id),
                args={"grade": grade, "standard": candidate.standard_id},
            )

        return RuleOutcome(
            self.rule_id,
            RuleDecision.BLOCK,
            SheetMetalCodes.MATERIAL_NOT_ALLOWED,
            self._text.bead_spec_not_allowed_on_grade(candidate.spec_id, grade),
            args={"spec": candidate.spec_id, "grade": grade},
        )


@dataclass(frozen=True, slots=True)
class BeadValidationResult:
    is_valid: bool
    outcomes: tuple[RuleOutcome, ...]

    @property
    def blocking_message(self) -> str | None:
        """The message of the first blocking outcome, or None if valid: what the dialog shows as the reason."""
        return next((o.message for o in self.outcomes if o.decision is RuleDecision.BLOCK), None)


class BeadSpecValidator:
    """Runs the ordered rules for one candidate SPEC against one profile. A BLOCK short-circuits the rest."""

    def __init__(self, rules: Iterable[BeadValidationRule]) -> None:
        self._rules = tuple(sorted(rules, key=lambda r: r.order))

    @staticmethod
    def standard(text: SheetMetalText | None = None) -> BeadSpecValidator:
        """Thickness, then material: what every bead tool validates with."""
        return BeadSpecValidator((ThicknessMatchRule(text), MaterialAllowedRule(text)))

    def validate(self, profile: SheetMetalProfile, candidate: BeadSpecRow) -> BeadValidationResult:
        context = BeadValidationContext(profile, candidate)
        outcomes: list[RuleOutcome] = []
        for rule in self._rules:
            outcome = rule.evaluate(context)
            outcomes.append(outcome)
            if outcome.decision is RuleDecision.BLOCK:
                break
        return BeadValidationResult(all(o.decision is not RuleDecision.BLOCK for o in outcomes), tuple(outcomes))

    def find_valid(self, profile: SheetMetalProfile, candidates: Iterable[BeadSpecRow]) -> tuple[BeadSpecRow, ...]:
        """The rows that validate: the "green items in the drop-down", and the alternatives beside an error."""
        return tuple(row for row in candidates if self.validate(profile, row).is_valid)


def allowed_grades_at(specs: Iterable[BeadSpecRow], thickness: float) -> frozenset[str]:
    """Grades allowed by at least one of `specs` whose thickness matches `thickness`."""
    return frozenset(
        grade for spec in specs if thickness_matches(spec.thickness, thickness) for grade in spec.allowed_grades
    )


def grades_allowed_by_all(specs: Sequence[BeadSpecRow], thickness: float) -> frozenset[str] | None:
    """Grades every one of `specs` allows at `thickness`: what the sheet can be made of without invalidating a bead
    already built to them. A SPEC of another thickness allows nothing. None when there are no SPECs: unrestricted."""
    allowed: frozenset[str] | None = None
    for spec in specs:
        grades = allowed_grades_at((spec,), thickness)
        allowed = grades if allowed is None else allowed & grades
    return allowed
