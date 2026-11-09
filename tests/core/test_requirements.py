"""Ports of Requirements/*Tests.cs: the mapper, the factory, the requirement constraint and row checks, and the bead
provider once the part holds requirements."""

from __future__ import annotations

import dataclasses

import pytest

from banxopen.foundation import (
    EqualsValueRule,
    FoundationCodes,
    RequirementDiff,
    RequirementRead,
    RequirementSeverity,
    RequirementSpec,
    RequirementStatus,
    RequirementValueType,
    RuleDecision,
    SetOfValuesRule,
    StoredRequirement,
)
from banxopen.materials import ConstraintSeverity
from banxopen.sheetmetal import (
    BEAD_DOMAIN,
    NOTHING_REBUILT,
    TOLERANCE_INCHES,
    BeadMaterialConstraintProvider,
    BeadSettings,
    BodyFeatureInventory,
    FeatureSpecStamp,
    MaterialThicknessPair,
    SheetMetalAllowance,
    SheetMetalCodes,
    SheetMetalPreferenceRead,
    SheetMetalRequirementConstraintProvider,
    SheetMetalRequirementNames,
    SheetMetalRowGateRule,
    SheetMetalStandardSelection,
    create_bead_requirements,
    requirement_to_specs,
    requirements_from_read,
)

from builders import (
    BODY_ID,
    STANDARD,
    FakeRequirementReader,
    FixedInventory,
    FixedPreferenceReader,
    SpecLookup,
    aluminum_table,
    context,
    gate,
    matched,
    requirement,
    sheet_metal_read,
    spec_row,
    stamped,
    unmatched,
)

TABLE = aluminum_table()


# ---- the mapper ----

AT40 = spec_row("B1005010-2", 0.04, "2024-O", "5052-O")
AT0625 = spec_row("B1005010-2", 0.0625, "2024-O")


def stored(specs, status=RequirementStatus.PASS):
    return RequirementRead(tuple(StoredRequirement(s, status) for s in specs), ())


def test_a_requirement_is_held_as_one_error_check_on_its_material_thickness_pairs():
    (spec,) = requirement_to_specs(requirement("B1005010-2", 0.04, AT40, AT0625))
    assert spec.aspect == SheetMetalRequirementNames.PAIR_ASPECT
    assert spec.value_type is RequirementValueType.STRING
    assert isinstance(spec.rule, SetOfValuesRule)
    assert spec.rule.values == ('"2024-O@0.0400"', '"5052-O@0.0400"', '"2024-O@0.0625"')
    assert spec.checked_formula == SheetMetalRequirementNames.PAIR_EXPRESSION
    assert spec.severity is RequirementSeverity.ERROR
    assert spec.key == "XX_Standard|B1005010-2"
    assert spec.associated_object_keys == ("101",)


def test_the_pair_expression_writes_the_thickness_as_the_pair_keys_do():
    assert SheetMetalRequirementNames.PAIR_EXPRESSION_FORMULA == (
        'BA_SM_Grade + "@" + format("%.4f", Sheet_Metal_Material_Thickness)'
    )
    assert MaterialThicknessPair("2024-O", 0.0598).key == "2024-O@0.0598"


def test_what_is_written_reads_back_as_the_same_requirement():
    original = requirement("B1005010-2", 0.04, AT40, AT0625)
    requirements, errors = requirements_from_read(stored(requirement_to_specs(original)), "Bead SPEC")
    assert not errors
    (read,) = requirements
    assert (read.domain, read.standard_id, read.spec_id) == (original.domain, "XX_Standard", "B1005010-2")
    assert read.allowance.built_thickness == 0.04
    assert read.allowance.pairs == original.allowance.pairs
    assert [u.feature_key for u in read.users] == ["101"]
    assert read.source == "B1005010"


def legacy(aspect: str, rule) -> RequirementSpec:
    return RequirementSpec(
        domain="SHEETMETAL.BEAD",
        key="XX_Standard|B1005010-2",
        aspect=aspect,
        value_type=RequirementValueType.STRING,
        rule=rule,
        severity=RequirementSeverity.WARNING,
        checked_formula="expr",
        description=("Source: B1005010",),
        associated_object_keys=("101",),
    )


def test_a_legacy_grade_and_thickness_requirement_reads_as_its_grades_at_the_built_thickness():
    specs = [
        legacy(SheetMetalRequirementNames.LEGACY_GRADE_ASPECT, SetOfValuesRule(("2024-O", "5052-O"))),
        legacy(SheetMetalRequirementNames.LEGACY_THICKNESS_ASPECT, EqualsValueRule(0.04, TOLERANCE_INCHES)),
    ]
    requirements, errors = requirements_from_read(stored(specs), "Bead SPEC")
    assert not errors
    (read,) = requirements
    assert read.allowance.built_thickness == 0.04
    assert [p.key for p in read.allowance.pairs] == ["2024-O@0.0400", "5052-O@0.0400"]
    assert read.source == "B1005010"


def without_built_at(spec: RequirementSpec, **changes) -> RequirementSpec:
    return dataclasses.replace(
        spec, description=tuple(d for d in spec.description if not d.startswith("Built at")), **changes
    )


def test_a_requirement_that_is_not_what_was_written_is_an_error_not_an_absence():
    (written,) = requirement_to_specs(requirement("B1005010-2", 0.04, "2024-O"))
    requirements, errors = requirements_from_read(stored([without_built_at(written)]), "Bead SPEC")
    assert not requirements
    (error,) = errors
    assert "B1005010-2" in error


def test_an_unquoted_requirement_still_reads_but_is_replaced_by_the_next_refresh():
    (wanted,) = requirement_to_specs(requirement("B1005010-2", 0.04, AT40))
    unquoted = dataclasses.replace(wanted, rule=SetOfValuesRule(("2024-O@0.0400", "5052-O@0.0400")))
    requirements, errors = requirements_from_read(stored([unquoted], RequirementStatus.FAILED), "Bead SPEC")
    assert not errors
    assert [p.key for p in requirements[0].allowance.pairs] == ["2024-O@0.0400", "5052-O@0.0400"]
    assert len(RequirementDiff.compute([unquoted], [wanted]).to_replace) == 1


def test_a_requirement_no_feature_uses_is_read_but_not_in_use():
    orphan = dataclasses.replace(requirement("B1005010-2", 0.04, AT40), users=())
    requirements, errors = requirements_from_read(stored(requirement_to_specs(orphan), RequirementStatus.UNKNOWN), "x")
    assert not errors
    assert not requirements[0].is_in_use


def test_an_unreadable_requirement_no_feature_uses_is_skipped_without_an_error():
    (written,) = requirement_to_specs(requirement("B1005010-2", 0.04, "2024-O"))
    requirements, errors = requirements_from_read(
        stored([without_built_at(written, associated_object_keys=())]), "Bead SPEC"
    )
    assert not requirements
    assert not errors


def test_store_read_errors_are_passed_through():
    _, errors = requirements_from_read(RequirementRead((), ("NX 1: broken",)), "Bead SPEC")
    assert errors == ("NX 1: broken",)


@pytest.mark.parametrize(
    ("thickness", "grade", "expected"),
    [
        (0.04, "2024-O", True),
        (0.0404, "2024-o", True),  # within tolerance, grade case ignored
        (0.04, "7075-T6", False),
        (0.0625, "2024-O", True),  # another thickness of the family, with a grade it allows there
        (0.0625, "5052-O", False),
        (0.05, "2024-O", False),  # a thickness the family does not cover
    ],
)
def test_an_allowance_allows_the_pairs_of_its_spec_family(thickness: float, grade: str, expected: bool):
    assert SheetMetalAllowance.from_family([AT40, AT0625], 0.04).is_satisfied_by(thickness, grade) is expected


# ---- the factory ----

F20 = spec_row("B1005010-2", 0.02, "2024-O", "5052-O")
F40 = spec_row("B1005010-2", 0.04, "2024-O")


def test_beads_on_one_spec_share_one_requirement_naming_all_of_them():
    beads = [stamped("101", "BEAD(1)", "B1005010-2", F20, F40), stamped("102", "BEAD(2)", "B1005010-2", F20, F40)]
    (req,) = create_bead_requirements(beads, (), NOTHING_REBUILT, 0.02).requirements
    assert [u.feature_key for u in req.users] == ["101", "102"]
    assert req.spec_id == "B1005010-2"


def test_an_applied_row_sets_the_built_thickness_and_allows_its_whole_family():
    beads = [stamped("101", "BEAD(1)", "B1005010-2", F20, F40)]
    previous = [requirement("B1005010-2", 0.02, "2024-O", "5052-O")]
    (req,) = create_bead_requirements(beads, previous, {(STANDARD, "B1005010-2"): F40}, 0.04).requirements
    assert req.allowance.built_thickness == 0.04
    assert [p.key for p in req.allowance.pairs] == ["2024-O@0.0200", "5052-O@0.0200", "2024-O@0.0400"]


def test_an_existing_requirement_is_kept_even_after_the_sheet_changed_thickness():
    beads = [stamped("101", "BEAD(1)", "B1005010-2", F20, F40)]
    previous = [requirement("B1005010-2", 0.02, "2024-O", "5052-O")]
    (req,) = create_bead_requirements(beads, previous, NOTHING_REBUILT, 0.04).requirements
    assert req.allowance.built_thickness == 0.02
    assert [p.key for p in req.allowance.pairs] == ["2024-O@0.0200", "5052-O@0.0200"]


def test_a_spec_without_a_requirement_is_recorded_at_the_current_sheet_with_a_note():
    result = create_bead_requirements([stamped("101", "BEAD(1)", "B1005010-2", F20, F40)], (), NOTHING_REBUILT, 0.04)
    (req,) = result.requirements
    assert req.allowance.built_thickness == 0.04
    assert [p.key for p in req.allowance.pairs] == ["2024-O@0.0200", "5052-O@0.0200", "2024-O@0.0400"]
    assert len(result.notes) == 1


def test_a_spec_not_driven_for_the_current_sheet_gets_no_requirement_and_says_why():
    result = create_bead_requirements([stamped("101", "BEAD(1)", "B1005010-2", F20)], (), NOTHING_REBUILT, 0.063)
    assert not result.requirements
    (note,) = result.notes
    assert "not driven" in note


def test_unidentified_beads_belong_to_no_requirement_and_matched_ones_are_labelled():
    beads = [unmatched("100", "BEAD(0)"), matched("101", "BEAD(1)", "B1005010-2", F20)]
    (req,) = create_bead_requirements(beads, (), NOTHING_REBUILT, 0.02).requirements
    (user,) = req.users
    assert user.feature_key == "101"
    assert "identified by geometry" in user.label


def test_a_spec_whose_beads_are_all_gone_drops_out():
    previous = [requirement("B1005010-2", 0.02, "2024-O")]
    assert not create_bead_requirements((), previous, NOTHING_REBUILT, 0.02).requirements


# ---- the requirement constraint and the picked row ----


def requirement_gate(reader, material: str, read: SheetMetalPreferenceRead | None = None):
    provider = SheetMetalRequirementConstraintProvider(
        reader, FixedPreferenceReader(read or sheet_metal_read(TABLE)), SheetMetalStandardSelection(TABLE, STANDARD)
    )
    return gate(provider, material)


def pick(reader, material: str, row: str, body_thickness: float = 0.02):
    rule = SheetMetalRowGateRule(
        FixedPreferenceReader(sheet_metal_read(TABLE, body_thickness=body_thickness)),
        SheetMetalStandardSelection(TABLE, STANDARD),
        reader,
    )
    return rule.evaluate(context(material, row))


def test_a_material_with_a_compliant_row_is_allowed():
    assert requirement_gate(FakeRequirementReader([requirement("B1", 0.02, "2024-O")]), "Aluminum 2024-O").decision is (
        RuleDecision.ALLOW
    )


def test_a_grade_the_requirement_does_not_allow_is_warned_not_blocked():
    outcome = requirement_gate(FakeRequirementReader([requirement("B1", 0.02, "2024-O")]), "Aluminum 5052-O")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_REQUIREMENT
    assert "will need to be re-applied" in (outcome.message or "")


def test_a_material_with_no_row_the_family_allows_is_warned_even_if_its_grade_is_allowed_elsewhere():
    outcome = requirement_gate(FakeRequirementReader([requirement("B1", 0.032, "5052-O")]), "Aluminum 5052-O")
    assert outcome.decision is RuleDecision.WARN
    assert "0.032" in (outcome.message or "")


def test_a_material_allowed_at_another_thickness_of_the_family_is_allowed():
    req = requirement("B1", 0.02, spec_row("B1", 0.02, "5052-O"), spec_row("B1", 0.032, "2024-O"))
    assert requirement_gate(FakeRequirementReader([req]), "Aluminum 2024-O").decision is RuleDecision.ALLOW


def test_unreadable_requirements_warn_rather_than_block():
    outcome = requirement_gate(FakeRequirementReader((), "NX 1: broken"), "Aluminum 2024-O")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == FoundationCodes.REQUIREMENTS_UNREADABLE


def test_a_requirement_no_feature_uses_is_not_validated():
    orphan = FakeRequirementReader([dataclasses.replace(requirement("B1", 0.02, "2024-O"), users=())])
    assert requirement_gate(orphan, "Aluminum 5052-O").decision is RuleDecision.ALLOW
    assert pick(orphan, "Aluminum 5052-O", "5052-O_0.020").decision is RuleDecision.ALLOW


def test_a_body_that_is_not_sheet_metal_is_not_constrained():
    reader = FakeRequirementReader([requirement("B1", 0.02, "2024-O")])
    assert requirement_gate(reader, "Aluminum 5052-O", SheetMetalPreferenceRead.NOT_SHEET_METAL).decision is (
        RuleDecision.ALLOW
    )


def test_a_row_whose_pair_the_family_does_not_allow_is_warned_not_blocked():
    outcome = pick(FakeRequirementReader([requirement("B1", 0.02, "2024-O")]), "Aluminum 5052-O", "5052-O_0.020")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.ROW_REFUSED_BY_REQUIREMENT
    assert outcome.args["refusal"] == "pairNotAllowed"
    assert "will need to be re-applied" in (outcome.message or "")


def test_a_row_at_a_thickness_the_family_does_not_cover_is_warned():
    reader = FakeRequirementReader([requirement("B1", 0.02, "2024-O")])
    outcome = pick(reader, "Aluminum 2024-O", "2024-O_0.032", body_thickness=0.032)
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.ROW_REFUSED_BY_REQUIREMENT


def test_a_row_allowed_at_another_thickness_than_built_warns_to_reapply():
    reader = FakeRequirementReader(
        [requirement("B1", 0.02, spec_row("B1", 0.02, "2024-O"), spec_row("B1", 0.032, "2024-O"))]
    )
    assert pick(reader, "Aluminum 2024-O", "2024-O_0.020").decision is RuleDecision.ALLOW
    outcome = pick(reader, "Aluminum 2024-O", "2024-O_0.032", body_thickness=0.032)
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.ROW_NEEDS_REAPPLY
    assert "built at 0.02" in (outcome.message or "")


def test_a_body_thickness_confirmation_carries_the_requirement_warning():
    outcome = pick(FakeRequirementReader([requirement("B1", 0.02, "2024-O")]), "Aluminum 2024-O", "2024-O_0.032")
    assert outcome.decision is RuleDecision.REQUIRE_CONFIRMATION
    assert outcome.reason_code == SheetMetalCodes.ROW_THICKNESS_MISMATCH
    assert "will need to be re-applied" in (outcome.message or "")


def test_unreadable_requirements_warn_on_every_row_and_none_warn_on_nothing():
    outcome = pick(FakeRequirementReader((), "NX 1: broken"), "Aluminum 2024-O", "2024-O_0.020")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.ROW_REFUSED_BY_REQUIREMENT
    assert pick(FakeRequirementReader(), "Aluminum 2024-O", "2024-O_0.020").decision is RuleDecision.ALLOW


# ---- the bead provider once the part holds requirements ----

ONE_STAMP = BodyFeatureInventory((FeatureSpecStamp(STANDARD, "B1005010-2", "101", "BEAD(1)"),))
AT20 = spec_row("B1005010-2", 0.02, "2024-O")


def covered_provider(reader, *workbook):
    return BeadMaterialConstraintProvider(
        FixedInventory(ONE_STAMP), SpecLookup(workbook), TABLE, BeadSettings.DEFAULT, reader
    )


def codes(provider):
    return [c.reason_code for c in provider.constraints_for(BODY_ID)]


def test_a_covered_spec_is_left_to_the_requirement():
    found = codes(covered_provider(FakeRequirementReader([requirement("B1005010-2", 0.02, "2024-O")]), AT20))
    assert SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD not in found
    assert SheetMetalCodes.MATERIAL_GRADE_UNRECOGNIZED not in found


def test_a_workbook_that_now_allows_other_grades_is_reported_as_out_of_date():
    provider = covered_provider(FakeRequirementReader([requirement("B1005010-2", 0.02, "2024-O", "5052-O")]), AT20)
    (drift,) = [
        c for c in provider.constraints_for(BODY_ID) if c.reason_code == SheetMetalCodes.REQUIREMENT_OUT_OF_DATE
    ]
    assert drift.severity is ConstraintSeverity.WARN


def test_drift_compares_every_pair_of_the_family_not_only_the_built_row():
    at40 = spec_row("B1005010-2", 0.04, "2024-O", "5052-O")
    recorded = FakeRequirementReader([requirement("B1005010-2", 0.02, AT20, at40)])
    assert SheetMetalCodes.REQUIREMENT_OUT_OF_DATE not in codes(covered_provider(recorded, AT20, at40))
    changed = spec_row("B1005010-2", 0.04, "2024-O")
    assert SheetMetalCodes.REQUIREMENT_OUT_OF_DATE in codes(covered_provider(recorded, AT20, changed))


def test_an_uncovered_spec_and_unreadable_requirements_keep_the_workbook_check():
    assert SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD in codes(covered_provider(FakeRequirementReader(), AT20))
    broken = FakeRequirementReader((), "broken")
    assert SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD in codes(covered_provider(broken, AT20))


def test_the_bead_domain_is_the_requirement_domain():
    assert requirement("B1", 0.02, "2024-O").domain == BEAD_DOMAIN
