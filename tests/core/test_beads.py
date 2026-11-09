"""Ports of Beads/*Tests.cs: allowed grades, settings, finder, matcher, validator, chains."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from banxopen.sheetmetal import (
    TOLERANCE_INCHES,
    BeadFeatureParameter,
    BeadGeometry,
    BeadSettings,
    BeadSpecColumns,
    BeadSpecValidator,
    CurveEnds,
    SheetMetalProfile,
    SpecDataError,
    allowed_grades_at,
    grades_allowed_by_all,
    group_curve_chains,
    match_bead_spec,
)

from builders import spec_row

SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "config-samples"


def geometry_of(row):
    return BeadGeometry(row.thickness, row.height, row.radius_and_rad_s, row.die_radius_p)


# ---- AllowedGradesAt ----


def test_unions_the_grades_allowed_by_every_spec_at_the_thickness():
    grades = allowed_grades_at([spec_row("A", 0.02, "2024-O"), spec_row("B", 0.02, "5052-O")], 0.02)
    assert sorted(grades) == ["2024-O", "5052-O"]


def test_ignores_specs_for_another_thickness():
    assert allowed_grades_at([spec_row("A", 0.02, "2024-O"), spec_row("B", 0.05, "7075-T6")], 0.02) == {"2024-O"}


def test_uses_the_same_thickness_tolerance_as_spec_validation():
    assert "2024-O" in allowed_grades_at([spec_row("A", 0.02, "2024-O")], 0.02 + TOLERANCE_INCHES / 2)


def test_excludes_grade_columns_marked_not_allowed():
    assert not allowed_grades_at([spec_row("A", 0.02)], 0.02)


def test_grades_allowed_by_all_is_unrestricted_with_no_specs():
    assert grades_allowed_by_all([], 0.02) is None


def test_grades_allowed_by_all_keeps_only_grades_every_spec_allows():
    specs = [spec_row("A", 0.02, "2024-O", "5052-O"), spec_row("B", 0.02, "5052-O", "7075-T6")]
    assert grades_allowed_by_all(specs, 0.02) == {"5052-O"}


def test_grades_allowed_by_all_allows_nothing_when_a_spec_is_for_another_thickness():
    grades = grades_allowed_by_all([spec_row("A", 0.02, "2024-O"), spec_row("B", 0.05, "2024-O")], 0.02)
    assert grades is not None
    assert not grades


# ---- BeadSettings ----


def write(tmp_path: Path, document: str) -> Path:
    path = tmp_path / "bead-settings.json"
    path.write_text(document, encoding="utf-8")
    return path


def test_a_missing_settings_file_yields_the_defaults(tmp_path: Path):
    settings = BeadSettings.load(tmp_path / "absent.json")
    assert settings.geometry_match_tolerance == BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE
    assert settings.parameter_mapping == BeadSettings.DEFAULT_PARAMETER_MAPPING


def test_the_shipped_config_file_loads_and_matches_the_defaults():
    settings = BeadSettings.load(SHIPPED_CONFIG / "bead-settings.json")
    assert (SHIPPED_CONFIG / "bead-settings.json").is_file()
    assert settings.geometry_match_tolerance == BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE
    assert settings.parameter_mapping == BeadSettings.DEFAULT_PARAMETER_MAPPING


def test_reads_tolerance_and_mapping_ignoring_case_and_spaces(tmp_path: Path):
    document = {
        "geometryMatchTolerance": 0.002,
        "parameterMapping": [
            {"feature": "height", "specColumn": "h"},
            {"feature": "Radius", "specColumn": "R & RADS"},
            {"feature": "DieRadius", "specColumn": "RAD P"},
        ],
    }
    settings = BeadSettings.load(write(tmp_path, json.dumps(document)))
    assert settings.geometry_match_tolerance == 0.002
    assert len(settings.parameter_mapping) == 3
    assert all(m.feature is not BeadFeatureParameter.THICKNESS for m in settings.parameter_mapping)


def test_an_omitted_section_takes_its_default(tmp_path: Path):
    settings = BeadSettings.load(write(tmp_path, '{ "geometryMatchTolerance": 0.001 }'))
    assert settings.geometry_match_tolerance == 0.001
    assert settings.parameter_mapping == BeadSettings.DEFAULT_PARAMETER_MAPPING


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ('{ "geometryMatchTolerance": 0 }', "greater than zero"),
        ('{ "geometryMatchTolerance": -1 }', "greater than zero"),
        ('{ "parameterMapping": [ { "feature": "Depth", "specColumn": "H" } ] }', "not a bead parameter"),
        (
            '{ "parameterMapping": [ { "feature": "Height", "specColumn": "HEIGHT" }, { "feature": "Radius", '
            '"specColumn": "R&RADS" }, { "feature": "DieRadius", "specColumn": "RADP" } ] }',
            "not a SPEC workbook column",
        ),
        (
            '{ "parameterMapping": [ { "feature": "Height", "specColumn": "H" }, { "feature": "Radius", '
            '"specColumn": "R&RADS" } ] }',
            "must map DieRadius",
        ),
        (
            '{ "parameterMapping": [ { "feature": "Height", "specColumn": "H" }, { "feature": "Height", '
            '"specColumn": "W" }, { "feature": "Radius", "specColumn": "R&RADS" }, { "feature": "DieRadius", '
            '"specColumn": "RADP" } ] }',
            "more than once",
        ),
        ("not json", "not valid JSON"),
    ],
)
def test_an_invalid_settings_file_is_rejected_saying_why(tmp_path: Path, document: str, expected: str):
    with pytest.raises(SpecDataError, match=expected):
        BeadSettings.load(write(tmp_path, document))


def test_spec_value_for_reads_the_configured_column():
    row = spec_row("S-1", 0.02, height=0.6, radius=0.2, die_radius=0.1, width=0.9)
    assert BeadSettings.DEFAULT.spec_value_for(row, BeadFeatureParameter.HEIGHT) == 0.6
    assert BeadSettings.DEFAULT.spec_value_for(row, BeadFeatureParameter.RADIUS) == 0.2
    assert BeadSettings.DEFAULT.spec_value_for(row, BeadFeatureParameter.DIE_RADIUS) == 0.1


# ---- BeadSpecValidator ----

VALID_ROW = spec_row("B1005010-1", 0.02, "2024-O")
VALIDATOR = BeadSpecValidator.standard()


def test_find_valid_returns_only_rows_that_pass_validation():
    rows = [spec_row("B1005010-1", 0.02, "2024-O"), spec_row("B1005010-2", 0.04, "2024-O"), spec_row("B1005010-3")]
    valid = VALIDATOR.find_valid(SheetMetalProfile(0.02, "2024-O"), rows)
    assert [r.spec_id for r in valid] == ["B1005010-1"]


def test_matching_thickness_and_allowed_grade_is_valid():
    result = VALIDATOR.validate(SheetMetalProfile(0.02, "2024-O"), VALID_ROW)
    assert result.is_valid
    assert result.blocking_message is None


def test_a_thickness_mismatch_blocks_before_checking_material():
    result = VALIDATOR.validate(SheetMetalProfile(0.05, "5052-O"), VALID_ROW)
    assert not result.is_valid
    assert [(o.rule_id, o.reason_code) for o in result.outcomes] == [("THICKNESS_MATCH", "THICKNESS_MISMATCH")]


@pytest.mark.parametrize(
    ("grade", "code"),
    [("5052-O", "MATERIAL_NOT_ALLOWED"), ("6061-T6", "MATERIAL_GRADE_UNRECOGNIZED"), (None, "MATERIAL_MISSING")],
)
def test_a_grade_problem_blocks_with_its_own_code(grade: str | None, code: str):
    result = VALIDATOR.validate(SheetMetalProfile(0.02, grade), VALID_ROW)
    assert not result.is_valid
    assert result.outcomes[-1].reason_code == code


# ---- matching ----


def test_identifies_the_spec_a_bead_was_built_to():
    target = spec_row("B1005010-1")
    match = match_bead_spec(geometry_of(target), [target, spec_row("B1005010-2", height=0.5)], BeadSettings.DEFAULT)
    assert match.single is target


@pytest.mark.parametrize("parameter", ["height", "radius", "die_radius", "thickness"])
def test_a_difference_beyond_tolerance_on_any_mapped_parameter_rules_a_spec_out(parameter: str):
    row = spec_row("B1005010-1")
    g = geometry_of(row)
    off = dataclasses.replace(
        g, **{parameter: getattr(g, parameter) + BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE * 2}
    )
    assert match_bead_spec(off, [row], BeadSettings.DEFAULT).is_none


def test_a_difference_within_tolerance_still_matches():
    row = spec_row("B1005010-1")
    near = dataclasses.replace(geometry_of(row), radius=row.radius_and_rad_s - TOLERANCE_INCHES / 2)
    assert match_bead_spec(near, [row], BeadSettings.DEFAULT).single is row


def test_specs_that_differ_only_in_width_are_ambiguous():
    narrow, wide = spec_row("B1005010-1", width=0.5), spec_row("B1005010-2", width=0.75)
    match = match_bead_spec(geometry_of(narrow), [narrow, wide], BeadSettings.DEFAULT)
    assert match.is_ambiguous
    assert match.single is None


def test_the_same_spec_listed_twice_is_one_match_not_an_ambiguity():
    row = spec_row("B1005010-1")
    assert match_bead_spec(geometry_of(row), [row, dataclasses.replace(row)], BeadSettings.DEFAULT).single is row


def test_only_mapped_parameters_are_compared():
    settings = BeadSettings(
        BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE,
        [m for m in BeadSettings.DEFAULT_PARAMETER_MAPPING if m.feature is not BeadFeatureParameter.THICKNESS],
    )
    row = spec_row("B1005010-1")
    assert match_bead_spec(dataclasses.replace(geometry_of(row), thickness=1.0), [row], settings).single is row


def test_a_remapped_parameter_is_compared_against_its_configured_column():
    settings = BeadSettings(
        BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE,
        [
            dataclasses.replace(m, spec_column=BeadSpecColumns.WIDTH) if m.feature is BeadFeatureParameter.HEIGHT else m
            for m in BeadSettings.DEFAULT_PARAMETER_MAPPING
        ],
    )
    row = spec_row("B1005010-1", height=0.1, width=0.9)
    assert match_bead_spec(dataclasses.replace(geometry_of(row), height=0.9), [row], settings).single is row


def test_the_same_row_through_two_standards_is_two_specs():
    row = spec_row("B1005010-1")
    other = dataclasses.replace(row, standard_id="YY_Standard")
    assert match_bead_spec(geometry_of(row), [row, other], BeadSettings.DEFAULT).is_ambiguous


def test_two_workbooks_sharing_a_spec_id_are_ambiguous_not_silently_merged():
    first, second = spec_row("B1005010-1", workbook="B1005010"), spec_row("B1005010-1", workbook="S5010")
    match = match_bead_spec(geometry_of(first), [first, second], BeadSettings.DEFAULT)
    assert match.is_ambiguous
    assert sorted(c.workbook_name for c in match.candidates) == ["B1005010", "S5010"]


def test_nothing_matches_an_empty_workbook():
    assert match_bead_spec(BeadGeometry(0.02, 0.625, 0.245, 0.188), [], BeadSettings.DEFAULT).is_none


# ---- chains ----

TOLERANCE = 0.001


def line(curve_id: int, x1: float, y1: float, x2: float, y2: float) -> CurveEnds:
    return CurveEnds(curve_id, (x1, y1, 0.0), (x2, y2, 0.0))


def test_separate_lines_are_separate_chains():
    assert group_curve_chains([line(1, 0, 0, 10, 0), line(2, 0, 5, 10, 5)], TOLERANCE) == ((1,), (2,))


def test_touching_lines_are_one_chain():
    assert group_curve_chains([line(1, 0, 0, 10, 0), line(2, 10, 0, 10, 10)], TOLERANCE) == ((1, 2),)


def test_chains_through_a_curve_picked_later():
    curves = [line(1, 0, 0, 10, 0), line(3, 20, 0, 30, 0), line(2, 10, 0, 20, 0)]
    assert group_curve_chains(curves, TOLERANCE) == ((1, 3, 2),)


def test_ends_within_tolerance_touch_and_beyond_do_not():
    assert len(group_curve_chains([line(1, 0, 0, 10, 0), line(2, 10.0005, 0, 20, 0)], TOLERANCE)) == 1
    assert len(group_curve_chains([line(1, 0, 0, 10, 0), line(2, 10.01, 0, 20, 0)], TOLERANCE)) == 2


def test_keeps_the_order_the_curves_were_picked_in():
    curves = [line(5, 0, 5, 10, 5), line(1, 0, 0, 10, 0), line(6, 10, 5, 10, 8)]
    assert group_curve_chains(curves, TOLERANCE) == ((5, 6), (1,))


def test_no_curves_is_no_chains():
    assert group_curve_chains([], TOLERANCE) == ()
