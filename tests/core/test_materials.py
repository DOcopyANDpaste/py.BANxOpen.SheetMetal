"""Ports of Materials/*Tests.cs: the standards table and its parser, the row filter, the constraint providers, the row
gate, the sync effect and which SPEC each bead is built to."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from banxopen.foundation import BodyId, BodyKind, RuleDecision
from banxopen.sheetmetal import (
    NO_FILTER,
    TOLERANCE_INCHES,
    BeadGeometry,
    BeadMaterialConstraintProvider,
    BeadSettings,
    BeadSpecStatus,
    BodyFeatureInventory,
    FeatureSpecStamp,
    MaterialAllowedRule,
    SheetMetalCodes,
    SheetMetalMaterialTable,
    SheetMetalPartPreference,
    SheetMetalPreferenceConstraintProvider,
    SheetMetalPreferenceRead,
    SheetMetalProfile,
    SheetMetalRowFilter,
    SheetMetalRowGateRule,
    SheetMetalStandardSelection,
    SheetMetalText,
    SpecDataError,
    SyncSheetMetalPreferenceEffectRule,
    UnstampedFeature,
    parse_material_table,
    resolve_beads_on_body,
)
from banxopen.sheetmetal.core.beads.rules import BeadValidationContext

from builders import (
    BODY_ID,
    FakeRequirementReader,
    FixedInventory,
    FixedPreferenceReader,
    SpecLookup,
    aluminum_table,
    context,
    gate,
    spec_row,
    table_row,
)

TABLE = aluminum_table()

# ---- the standards file parser ----

SAMPLE_HEADER = [
    "Material_Name",
    "SheetMetal_Standard",
    "Standard",
    "PHYSICAL_MATERIAL_NAME",
    "SheetMetal_Material",
    "  Cat",
    "Type",
    "THICKNESS",
    "BEND_RADIUS",
    "NEUTRAL_FACTOR",
    "RELIEF_DEPTH",
    "RELIEF_WIDTH",
    "JOGGLE_RADIUS",
    "JOGGLE_LENGTH",
    "Lightning_Hole_Standards_HolesValues",
    "LightningHole_Stds_File",
    "Lightning_Oblong_Standards_HolesValues",
    "LightningOblong_Std_File",
    "BEAD_Values",
    "BEAD_Std_File",
    "DRG_NAME",
    "THICKNESS_SELECTED_STANDARD",
    "MIN_BEND_RADIUS",
]
SAMPLE_PROPERTIES = [
    "unique",
    "attribute",
    "show",
    "show",
    "attribute",
    "hide",
    "hide",
    "show",
    "show",
    "show",
    "hide",
    "hide",
    "hide",
    "hide",
    "attribute",
    "attribute",
    "attribute",
    "attribute",
    "attribute",
    "attribute",
    "hide",
    "attribute",
    "attribute",
]
NO_STD = r"\BA_Standard\Settings\SheetMetalStandards\No_Standards.xls"


def sample_row(thickness: str, standard: str) -> str:
    return "\t".join(
        [
            f"17-7PH ANNEALED BENT COLD 90°_{thickness}",
            standard,
            standard,
            "17-7PH ANNEALED",
            "17-7PH ANNEALED",
            "XX",
            "XX",
            thickness,
            "0.03",
            "@NF_DIN_FORMULA_1",
            "1",
            "1",
            "1",
            "1",
            "0",
            NO_STD,
            "0",
            NO_STD,
            "0",
            NO_STD,
            "0",
            thickness,
            "0.03",
        ]
    )


def sample(units: str = "UNITS\tENGLISH") -> str:
    return "\r\n".join(
        [
            units,
            "",
            "MATERIAL_TABLE" + "\t" * 22,
            "VERSION\t2" + "\t" * 21,
            "\t".join(SAMPLE_HEADER),
            "\t".join(SAMPLE_PROPERTIES),
            sample_row("0.01", "XX_Standards"),
            sample_row("0.012", "YY_Standards"),
            sample_row("0.016", "XX_Standards"),
            "END_OF_MATERIAL_TABLE",
        ]
    )


NX_STYLE = """# a comment, with, commas
VERSION,2
UNITS,ENGLISH
ANGLE_REFERENCE,OUTSIDE
PARAMETERS
FEATURE,ANGLE
END_OF_PARAMETERS
MATERIAL_TABLE
VERSION,2
Material_Name,   \tStandard,  \tPHYSICAL_MATERIAL_NAME,  \tSheetMetal_Material, THICKNESS, BEND_RADIUS,
unique,          \tshow,      \tshow,                    \tattribute,           show,      show,
#2024-O_0.010,   \tXX_Standard,\tAluminum 2024-O,         \t2024-O,              0.010,     0.03
2024-O_0.016,    \tXX_Standard,\tAluminum 2024-O,         \t2024-O,              0.016,     @Die1_Punch4_TABLE
7075-T6_0.020,   \tYY_Standard,\tAluminum 7075-T6,        \t7075-T6,             0.020,     0.09,

END_OF_MATERIAL_TABLE
TABLE
NAME,Die1_Punch4_TABLE
EMPTY,\t1.2,\t1.2
END_OF_TABLE
"""


def test_reads_the_business_sample_table():
    rows = parse_material_table(sample())
    assert len(rows) == 3
    first = rows[0]
    assert first.name == "17-7PH ANNEALED BENT COLD 90°_0.01"
    assert (first.standard, first.sheet_metal_standard) == ("XX_Standards", "XX_Standards")
    assert (first.physical_material_name, first.grade) == ("17-7PH ANNEALED", "17-7PH ANNEALED")
    assert (first.thickness, first.bend_radius, first.line_number) == (0.01, "0.03", 7)
    assert first.value_of("neutral_factor") == "@NF_DIN_FORMULA_1"
    assert first.value_of("Cat") == "XX"
    assert first.value_of("NOT_A_COLUMN") is None


def test_reads_nx_style_commas_skipping_comments_and_other_blocks():
    rows = parse_material_table(NX_STYLE)
    assert [r.name for r in rows] == ["2024-O_0.016", "7075-T6_0.020"]
    assert rows[0].bend_radius == "@Die1_Punch4_TABLE"
    assert rows[1].physical_material_name == "Aluminum 7075-T6"


def test_a_tab_separated_cell_may_contain_a_comma():
    content = "\n".join(
        [
            "UNITS,ENGLISH",
            "MATERIAL_TABLE",
            "Material_Name\tStandard\tPHYSICAL_MATERIAL_NAME\tSheetMetal_Material\tTHICKNESS",
            "unique\tshow\tshow\tattribute\tshow",
            "2024-O_0.020\tXX\tAluminum 2024, Temper O\t2024-O\t0.02",
            "END_OF_MATERIAL_TABLE",
        ]
    )
    (row,) = parse_material_table(content)
    assert row.physical_material_name == "Aluminum 2024, Temper O"


@pytest.mark.parametrize("encoded", [sample().encode("latin-1"), b"\xef\xbb\xbf" + sample().encode("utf-8")])
def test_decodes_latin1_and_utf8_with_a_bom_so_a_degree_sign_survives(encoded: bytes):
    assert parse_material_table(encoded)[0].name == "17-7PH ANNEALED BENT COLD 90°_0.01"


def test_refuses_a_metric_file():
    with pytest.raises(SpecDataError, match="METRIC") as error:
        parse_material_table(sample("UNITS,METRIC"))
    assert "ENGLISH" in str(error.value)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (sample("# no units here"), "UNITS"),
        ("UNITS,ENGLISH\nTABLE\nEND_OF_TABLE", "MATERIAL_TABLE"),
        (sample().replace("END_OF_MATERIAL_TABLE", ""), "END_OF_MATERIAL_TABLE"),
        (NX_STYLE.replace("attribute,           show", "visible,             show"), "visible"),
        (NX_STYLE.replace("0.09,", "0.09, extra"), "values"),
        (
            NX_STYLE.replace("show,      \tshow,                    \tattribute", "unique,    \tshow,  \tattribute"),
            "unique",
        ),
    ],
)
def test_refuses_an_invalid_table(content: str, expected: str):
    with pytest.raises(SpecDataError, match=expected):
        parse_material_table(content)


def test_refuses_a_repeated_unique_value_naming_both_lines():
    content = sample().replace(sample_row("0.016", "XX_Standards"), sample_row("0.01", "XX_Standards"))
    with pytest.raises(SpecDataError, match="line 9") as error:
        parse_material_table(content)
    assert "line 7" in str(error.value)


@pytest.mark.parametrize("column", ["Standard", "PHYSICAL_MATERIAL_NAME", "SheetMetal_Material", "THICKNESS"])
def test_refuses_a_table_missing_a_required_column(column: str):
    with pytest.raises(SpecDataError, match="line 5") as error:
        parse_material_table(sample().replace(f"\t{column}\t", f"\tRenamed_{column}\t"))
    assert column in str(error.value)


def test_refuses_a_row_with_an_empty_required_value():
    content = sample().replace(
        "\t17-7PH ANNEALED\t17-7PH ANNEALED\tXX\tXX\t0.012", "\t17-7PH ANNEALED\t\tXX\tXX\t0.012"
    )
    with pytest.raises(SpecDataError, match="line 8") as error:
        parse_material_table(content)
    assert "SheetMetal_Material" in str(error.value)


@pytest.mark.parametrize("thickness", ["thin", "@THICKNESS_TABLE", "0"])
def test_refuses_a_thickness_that_is_not_a_positive_number(thickness: str):
    with pytest.raises(SpecDataError, match="THICKNESS") as error:
        parse_material_table(NX_STYLE.replace("0.016,     @Die1", f"{thickness},     @Die1"))
    assert thickness in str(error.value)


# ---- the table ----


def test_standards_are_distinct_in_the_order_they_first_appear():
    table = SheetMetalMaterialTable(
        [
            table_row("a", "M", "G", standard="YY_Standard"),
            table_row("b", "M", "G", standard="XX_Standard"),
            table_row("c", "M", "G", standard="yy_standard"),
        ]
    )
    assert table.standards == ("YY_Standard", "XX_Standard")


def test_rows_for_a_standard_keep_file_order_and_ignore_case():
    assert [r.name for r in TABLE.rows_for("xx_standard")] == ["2024-O_0.020", "5052-O_0.020", "2024-O_0.032"]


def test_finds_a_row_by_name_ignoring_case():
    assert TABLE.find("2024-o_0.032").name == "2024-O_0.032"  # type: ignore[union-attr]
    assert TABLE.find("Retired") is None
    assert TABLE.find(None) is None


def test_the_first_row_for_a_physical_material_is_its_first_in_file_order():
    assert TABLE.first_row_for_physical_material("Aluminum 2024-O").name == "2024-O_0.020"  # type: ignore[union-attr]
    assert TABLE.grade_for_physical_material("aluminum 2024-o") == "2024-O"
    assert TABLE.first_row_for_physical_material("Titanium Grade 5") is None
    assert TABLE.grade_for_physical_material("Titanium Grade 5") is None


def test_load_names_the_file_in_a_parse_error(tmp_path: Path):
    path = tmp_path / "smt.csv"
    path.write_text("UNITS,METRIC\nMATERIAL_TABLE\n", encoding="utf-8")
    with pytest.raises(SpecDataError, match=r"smt\.csv"):
        SheetMetalMaterialTable.load(path)


def test_load_of_a_missing_file_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        SheetMetalMaterialTable.load(tmp_path / "missing.csv")


def test_describe_lists_standard_grade_thickness_and_bend_radius():
    assert table_row("5052-O_0.063", "Aluminum 5052-O", "5052-O", thickness=0.063).describe() == (
        "XX_Standard · 5052-O · THK 0.063 · BR 0.06"
    )


def test_describe_leaves_out_blank_columns():
    assert table_row("r", "Aluminum 5052-O", "", standard="", bend_radius=" ").describe() == "THK 0.02"


# ---- the row filter ----

ROWS = TABLE.rows


def names(rows):
    return [r.name for r in rows]


def test_no_filter_lets_every_row_through_in_file_order():
    assert NO_FILTER.is_empty
    assert names(NO_FILTER.apply(ROWS)) == names(ROWS)


def test_each_filter_narrows_on_its_own_column_ignoring_case():
    assert names(SheetMetalRowFilter("yy_standard").apply(ROWS)) == ["7075-T6_0.020"]
    assert names(SheetMetalRowFilter(None, "aluminum 2024-o").apply(ROWS)) == ["2024-O_0.020", "2024-O_0.032"]
    assert names(SheetMetalRowFilter(None, None, 0.032).apply(ROWS)) == ["2024-O_0.032"]
    assert not SheetMetalRowFilter(None, None, None, "@BEND_TABLE_1").apply(ROWS)


def test_filters_combine():
    f = SheetMetalRowFilter("XX_Standard", "Aluminum 2024-O", 0.02, "0.06")
    assert not f.is_empty
    assert names(f.apply(ROWS)) == ["2024-O_0.020"]


def test_thickness_matches_within_tolerance():
    assert names(SheetMetalRowFilter(None, None, 0.0324).apply(ROWS)) == ["2024-O_0.032"]
    assert not SheetMetalRowFilter(None, None, 0.0330).apply(ROWS)


def test_options_cascade_from_the_other_filters_but_not_their_own():
    f = SheetMetalRowFilter("XX_Standard", None, 0.032)
    assert f.physical_material_options(ROWS) == ("Aluminum 2024-O",)
    assert f.standard_options(ROWS) == ("XX_Standard",)
    assert f.thickness_options(ROWS) == (0.02, 0.032)


def test_unfiltered_options_list_every_value_once():
    assert NO_FILTER.standard_options(ROWS) == ("XX_Standard", "YY_Standard")
    assert NO_FILTER.physical_material_options(ROWS) == ("Aluminum 2024-O", "Aluminum 5052-O", "Aluminum 7075-T6")
    assert NO_FILTER.thickness_options(ROWS) == (0.02, 0.032)
    assert NO_FILTER.bend_radius_options(ROWS) == ("0.06",)


def test_thicknesses_within_tolerance_are_one_option():
    rows = [table_row("a", "M", "G", 0.0320), table_row("b", "M", "G", 0.0322), table_row("c", "M", "G", 0.0100)]
    assert NO_FILTER.thickness_options(rows) == (0.01, 0.032)


def test_numeric_bend_radii_sort_before_table_pointers():
    rows = [
        table_row(f"r{i}", "M", "G", bend_radius=r)
        for i, r in enumerate(["@BEND_TABLE_2", "0.12", "@bend_table_1", "0.06", ""])
    ]
    assert NO_FILTER.bend_radius_options(rows) == ("0.06", "0.12", "@bend_table_1", "@BEND_TABLE_2")


def test_for_row_leaves_only_that_row():
    (row,) = [r for r in ROWS if r.name == "2024-O_0.032"]
    f = SheetMetalRowFilter.for_row(row)
    assert f == SheetMetalRowFilter("XX_Standard", "Aluminum 2024-O", 0.032, "0.06")
    assert names(f.apply(ROWS)) == ["2024-O_0.032"]


def test_for_row_treats_a_blank_standard_or_bend_radius_as_any():
    f = SheetMetalRowFilter.for_row(table_row("r", "Aluminum 2024-O", "2024-O", standard=" ", bend_radius=""))
    assert f.standard is None
    assert f.bend_radius is None
    assert names(f.apply(ROWS)) == ["2024-O_0.020"]


# ---- the preference constraint ----


def preference_provider(read: SheetMetalPreferenceRead) -> SheetMetalPreferenceConstraintProvider:
    return SheetMetalPreferenceConstraintProvider(FixedPreferenceReader(read), TABLE)


def preference_read(bodies: int = 1) -> SheetMetalPreferenceRead:
    return SheetMetalPreferenceRead.of(
        SheetMetalPartPreference("2024-O_0.020", True, 0.02, bodies, TABLE.find("2024-O_0.020"))
    )


def test_a_body_that_is_not_sheet_metal_has_no_preference_constraints():
    assert not preference_provider(SheetMetalPreferenceRead.NOT_SHEET_METAL).constraints_for(BODY_ID)


@pytest.mark.parametrize("material", ["Aluminum 2024-O", "Aluminum 5052-O"])
def test_allows_a_material_with_a_row_even_if_it_differs_from_the_preferences(material: str):
    assert gate(preference_provider(preference_read()), material).decision is RuleDecision.ALLOW


def test_blocks_a_material_with_no_row():
    outcome = gate(preference_provider(preference_read()), "Titanium Grade 5")
    assert outcome.decision is RuleDecision.BLOCK
    assert outcome.reason_code == SheetMetalCodes.PREFERENCE_MATERIAL_NOT_IN_TABLE
    assert "PHYSICAL_MATERIAL_NAME = 'Titanium Grade 5'" in (outcome.message or "")


def test_a_second_sheet_metal_body_warns_and_never_masks_a_refusal():
    outcome = gate(preference_provider(preference_read(bodies=2)), "Aluminum 2024-O")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.PREFERENCE_SHARED_BY_BODIES
    assert "2 sheet metal bodies" in (outcome.message or "")
    assert gate(preference_provider(preference_read(bodies=2)), "Titanium Grade 5").decision is RuleDecision.BLOCK


def test_unreadable_preferences_block_rather_than_count_as_unrestricted():
    outcome = gate(preference_provider(SheetMetalPreferenceRead.unreadable("NX 12345: unavailable")), "Aluminum 2024-O")
    assert outcome.decision is RuleDecision.BLOCK
    assert outcome.reason_code == SheetMetalCodes.PREFERENCES_UNREADABLE
    assert "NX 12345" in (outcome.message or "")


# ---- the row gate ----

GATE_TABLE = aluminum_table(table_row("2024-O_0.020_YY", "Aluminum 2024-O", "2024-O", standard="YY_Standard"))


def pick(
    material: str,
    row: str | None,
    read: SheetMetalPreferenceRead | None = None,
    kind: BodyKind = BodyKind.SHEET_METAL,
    standard: str | None = "XX_Standard",
):
    read = read or SheetMetalPreferenceRead.of(SheetMetalPartPreference(None, True, 0.02, 1), 0.02)
    rule = SheetMetalRowGateRule(
        FixedPreferenceReader(read), SheetMetalStandardSelection(GATE_TABLE, standard), FakeRequirementReader()
    )
    return rule.evaluate(context(material, row, kind))


def test_the_row_gate_ignores_bodies_that_are_not_sheet_metal_and_unpicked_rows():
    assert pick("Aluminum 2024-O", "nonsense", kind=BodyKind.SOLID).decision is RuleDecision.ALLOW
    assert pick("Aluminum 2024-O", None).decision is RuleDecision.ALLOW


def test_a_row_matching_the_bodys_thickness_is_allowed():
    assert pick("Aluminum 2024-O", "2024-O_0.020").decision is RuleDecision.ALLOW


def test_a_row_at_a_different_thickness_from_the_body_needs_confirming():
    outcome = pick("Aluminum 2024-O", "2024-O_0.032")
    assert outcome.decision is RuleDecision.REQUIRE_CONFIRMATION
    assert outcome.reason_code == SheetMetalCodes.ROW_THICKNESS_MISMATCH
    assert "0.032" in (outcome.message or "")
    assert "0.02" in (outcome.message or "")
    assert outcome.args == {"row": "2024-O_0.032", "rowThickness": "0.032", "bodyThickness": "0.02"}


def test_a_row_not_offered_is_blocked_and_carries_row_material_and_standard():
    outcome = pick("Aluminum 2024-O", "5052-O_0.020")
    assert outcome.decision is RuleDecision.BLOCK
    assert outcome.reason_code == SheetMetalCodes.ROW_NOT_OFFERED
    assert outcome.args == {"row": "5052-O_0.020", "material": "Aluminum 2024-O", "standard": "XX_Standard"}


def test_unknown_thickness_and_unreadable_preferences_confirm_nothing():
    no_thickness = SheetMetalPreferenceRead.of(SheetMetalPartPreference(None, True, 0.02, 1), None)
    assert pick("Aluminum 2024-O", "2024-O_0.032", no_thickness).decision is RuleDecision.ALLOW
    unreadable = SheetMetalPreferenceRead.unreadable("NX 1: broken")
    assert pick("Aluminum 2024-O", "2024-O_0.032", unreadable).decision is RuleDecision.ALLOW


def test_a_row_the_table_does_not_have_or_of_another_standard_is_blocked():
    assert pick("Aluminum 2024-O", "2024-O_0.125").decision is RuleDecision.BLOCK
    outcome = pick("Aluminum 2024-O", "2024-O_0.020_YY")
    assert outcome.decision is RuleDecision.BLOCK
    assert "XX_Standard" in (outcome.message or "")
    assert pick("Aluminum 2024-O", "2024-O_0.020_YY", standard=None).decision is RuleDecision.ALLOW


# ---- the sync effect ----


def effects(kind: BodyKind, material: str, row: str | None = None):
    return SyncSheetMetalPreferenceEffectRule(TABLE).generate_effects(context(material, row, kind))


def test_a_sheet_metal_body_syncs_the_row_the_user_picked():
    (instruction,) = effects(BodyKind.SHEET_METAL, "Aluminum 2024-O", "2024-O_0.032")
    assert instruction.instruction_type == SyncSheetMetalPreferenceEffectRule.INSTRUCTION_TYPE
    assert instruction.body_id == BodyId("body-1")
    assert instruction.data[SyncSheetMetalPreferenceEffectRule.MATERIAL_NAME_DATA_KEY] == "2024-O_0.032"


@pytest.mark.parametrize("kind", [BodyKind.SOLID, BodyKind.SHEET, BodyKind.UNKNOWN])
def test_a_body_that_is_not_sheet_metal_syncs_nothing(kind: BodyKind):
    assert not effects(kind, "Aluminum 2024-O", "2024-O_0.020")


@pytest.mark.parametrize("row", [None, "2024-O_0.125", "5052-O_0.020"])
def test_no_unknown_or_foreign_row_syncs_nothing(row: str | None):
    assert not effects(BodyKind.SHEET_METAL, "Aluminum 2024-O", row)


def test_declares_the_instruction_type_its_executor_registers_under():
    assert SyncSheetMetalPreferenceEffectRule(TABLE).instruction_types == {"SYNC_SHEETMETAL_PREFERENCE_MATERIAL"}


# ---- which SPEC each bead is built to ----


def resolve(stamps=(), unstamped=(), *rows):
    return resolve_beads_on_body(
        BodyFeatureInventory(tuple(stamps), tuple(unstamped)), SpecLookup(rows), BeadSettings.DEFAULT
    )


def geometry_of(row):
    return BeadGeometry(row.thickness, row.height, row.radius_and_rad_s, row.die_radius_p)


def stamp(spec_id: str, key: str = "", name: str = "") -> FeatureSpecStamp:
    return FeatureSpecStamp("XX_Standard", spec_id, key, name)


def test_a_stamped_bead_carries_its_spec_and_feature_key():
    spec = spec_row("B1005010-1", 0.02, "2024-O")
    (bead,) = resolve([stamp("B1005010-1", "42", "Bead(4)")], (), spec)
    assert bead.status is BeadSpecStatus.STAMPED
    assert bead.spec_family == (spec,)
    assert bead.feature_key == "42"


def test_a_stamp_whose_spec_is_gone_is_flagged_not_dropped():
    (bead,) = resolve([stamp("RETIRED")])
    assert bead.status is BeadSpecStatus.STAMPED_SPEC_MISSING
    assert not bead.is_spec_known
    assert bead.is_stamped


def test_an_unstamped_bead_is_identified_by_its_geometry():
    spec = spec_row("B1005010-1", 0.02, "2024-O")
    (bead,) = resolve((), [UnstampedFeature("Bead(12)", geometry_of(spec), "7")], spec)
    assert bead.status is BeadSpecStatus.MATCHED
    assert bead.spec_family == (spec,)
    assert SheetMetalText.DEFAULT.bead_unidentified_reason(bead) is None


def test_an_unstamped_bead_matching_several_specs_is_ambiguous_and_says_which():
    a, b = spec_row("B1005010-1"), spec_row("B1005010-2")
    (bead,) = resolve((), [UnstampedFeature("Bead(12)", geometry_of(a))], a, b)
    assert bead.status is BeadSpecStatus.AMBIGUOUS
    assert not bead.is_spec_known
    assert "B1005010-2" in (SheetMetalText.DEFAULT.bead_unidentified_reason(bead) or "")


def test_unreadable_and_unmatched_beads_say_why():
    beads = resolve(
        (),
        [UnstampedFeature("Bead(1)", None), UnstampedFeature("Bead(2)", geometry_of(spec_row("X", height=9)))],
        spec_row("B1005010-1"),
    )
    assert [b.status for b in beads] == [BeadSpecStatus.GEOMETRY_UNREADABLE, BeadSpecStatus.UNMATCHED]
    assert all(SheetMetalText.DEFAULT.bead_unidentified_reason(b) for b in beads)


def family(spec_id: str, *rows: tuple[float, bool]):
    return [spec_row(spec_id, t, *(["2024-O"] if allows else [])) for t, allows in rows]


def test_a_stamped_bead_carries_every_thickness_its_spec_is_driven_for():
    (bead,) = resolve([stamp("B1005010-2")], (), *family("B1005010-2", (0.02, True), (0.05, True), (0.063, True)))
    assert [r.thickness for r in bead.spec_family] == [0.02, 0.05, 0.063]


def test_spec_at_picks_the_row_driven_for_that_thickness_not_the_first():
    (bead,) = resolve([stamp("B1005010-2")], (), *family("B1005010-2", (0.02, True), (0.05, False)))
    at_05, at_02 = bead.spec_at(0.05), bead.spec_at(0.02)
    assert at_05 is not None and at_02 is not None
    assert at_05.thickness == 0.05
    assert not at_05.is_allowed_for("2024-O")
    assert at_02.is_allowed_for("2024-O")


def test_spec_at_uses_the_validation_tolerance_and_is_none_outside_the_family():
    (bead,) = resolve([stamp("B1005010-2")], (), *family("B1005010-2", (0.02, True), (0.063, True)))
    assert bead.spec_at(0.063 + TOLERANCE_INCHES / 2) is not None
    assert bead.spec_at(0.09) is None
    assert bead.thickness_coverage == "0.02 - 0.063"


def test_an_unstamped_bead_is_widened_from_the_matched_row_to_its_whole_family():
    rows = family("B1005010-2", (0.02, True), (0.05, True))
    (bead,) = resolve((), [UnstampedFeature("Bead(12)", geometry_of(rows[0]))], *rows)
    assert bead.status is BeadSpecStatus.MATCHED
    assert [r.thickness for r in bead.spec_family] == [0.02, 0.05]


# ---- the bead constraint ----


def bead_spec(spec_id: str, *grades: str, thickness: float = 0.02, width: float = 0.625):
    return spec_row(spec_id, thickness, *grades, width=width)


def bead_provider(stamps=(), unstamped=(), *workbook):
    inventory = FixedInventory(BodyFeatureInventory(tuple(stamps), tuple(unstamped)), BODY_ID)
    return BeadMaterialConstraintProvider(
        inventory, SpecLookup(workbook), TABLE, BeadSettings.DEFAULT, FakeRequirementReader()
    )


def test_a_body_without_beads_has_no_constraints_and_accepts_anything():
    assert not bead_provider((), (), bead_spec("B1005010-1", "2024-O")).constraints_for(BODY_ID)
    assert gate(bead_provider(), "Titanium Grade 5").decision is RuleDecision.ALLOW


def test_allows_a_material_whose_grade_the_spec_permits():
    assert (
        gate(bead_provider([stamp("B1005010-1")], (), bead_spec("B1005010-1", "2024-O")), "Aluminum 2024-O").decision
        is RuleDecision.ALLOW
    )


def test_warns_on_a_material_whose_grade_the_spec_forbids_naming_material_grade_and_spec():
    outcome = gate(bead_provider([stamp("B1005010-1")], (), bead_spec("B1005010-1", "2024-O")), "Aluminum 7075-T6")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD
    for part in ("Aluminum 7075-T6", "7075-T6", "B1005010-1"):
        assert part in (outcome.message or "")


def test_allows_a_grade_some_thickness_of_the_spec_permits():
    provider = bead_provider(
        [stamp("B1005010-1T")],
        (),
        bead_spec("B1005010-1T", "2024-O", "7075-T6", thickness=0.02),
        bead_spec("B1005010-1T", "2024-O", thickness=0.05),
    )
    assert gate(provider, "Aluminum 7075-T6").decision is RuleDecision.ALLOW


def test_warns_on_a_grade_no_thickness_of_the_spec_permits_with_one_constraint_per_family():
    provider = bead_provider(
        [stamp("B1005010-2")],
        (),
        *(bead_spec("B1005010-2", "2024-O", thickness=t) for t in (0.02, 0.05, 0.063)),
    )
    outcome = gate(provider, "Aluminum 7075-T6")
    assert outcome.decision is RuleDecision.WARN
    assert "at any thickness" in (outcome.message or "")
    assert len(provider.constraints_for(BODY_ID)) == 2  # grade recognition + one per SPEC


def test_an_unmapped_material_is_reported_as_unmapped_not_refused_by_a_spec():
    provider = bead_provider(
        [stamp("B1005010-1"), stamp("B1005010-2")],
        (),
        bead_spec("B1005010-1", "2024-O"),
        bead_spec("B1005010-2", "2024-O"),
    )
    outcome = gate(provider, "Titanium Grade 5")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.MATERIAL_GRADE_UNRECOGNIZED
    assert "sheet metal material standards file" in (outcome.message or "")


def test_a_spec_missing_from_its_workbook_warns_on_every_material():
    outcome = gate(bead_provider([stamp("B1005010-RETIRED")]), "Aluminum 2024-O")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.BEAD_SPEC_NOT_FOUND
    assert "B1005010-RETIRED" in (outcome.message or "")


def test_a_material_must_satisfy_every_spec_on_the_body():
    provider = bead_provider(
        [stamp("B1005010-1"), stamp("B1005010-2")],
        (),
        bead_spec("B1005010-1", "2024-O", "5052-O"),
        bead_spec("B1005010-2", "2024-O"),
    )
    assert gate(provider, "Aluminum 2024-O").decision is RuleDecision.ALLOW
    refused = gate(provider, "Aluminum 5052-O")
    assert refused.decision is RuleDecision.WARN
    assert "B1005010-2" in (refused.message or "")


def test_repeated_stamps_and_a_matching_unstamped_bead_produce_one_constraint_per_spec():
    spec = bead_spec("B1005010-1", "2024-O")
    provider = bead_provider(
        [stamp("B1005010-1"), stamp("B1005010-1")], [UnstampedFeature("Bead(12)", geometry_of(spec))], spec
    )
    codes = [c.reason_code for c in provider.constraints_for(BODY_ID)]
    assert codes.count(SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD) == 1


def test_an_unstamped_bead_matching_one_spec_is_enforced_and_named_as_unstamped():
    spec = bead_spec("B1005010-1", "2024-O")
    provider = bead_provider((), [UnstampedFeature("Bead(12)", geometry_of(spec))], spec)
    assert gate(provider, "Aluminum 2024-O").decision is RuleDecision.ALLOW
    refused = gate(provider, "Aluminum 7075-T6")
    assert refused.reason_code == SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD
    assert "Bead(12)" in (refused.message or "")
    assert "unstamped" in (refused.message or "")


def test_an_unstamped_bead_matching_no_spec_warns_and_allows():
    spec = bead_spec("B1005010-1", "2024-O")
    unlike = dataclasses.replace(geometry_of(spec), height=spec.height + 1.0)
    outcome = gate(bead_provider((), [UnstampedFeature("Bead(12)", unlike)], spec), "Aluminum 7075-T6")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.BEAD_SPEC_UNMATCHED
    assert "matches no SPEC" in (outcome.message or "")


def test_an_ambiguous_or_unreadable_unstamped_bead_warns_rather_than_guessing():
    narrow, wide = bead_spec("B1005010-1", "2024-O", width=0.5), bead_spec("B1005010-2", "7075-T6", width=0.75)
    outcome = gate(
        bead_provider((), [UnstampedFeature("Bead(12)", geometry_of(narrow))], narrow, wide), "Aluminum 7075-T6"
    )
    assert outcome.decision is RuleDecision.WARN
    assert "B1005010-1" in (outcome.message or "") and "B1005010-2" in (outcome.message or "")
    unreadable = gate(bead_provider((), [UnstampedFeature("Bead(12)", None)], narrow), "Aluminum 7075-T6")
    assert "could not be read" in (unreadable.message or "")


def test_an_unmatched_bead_warning_never_masks_a_stamped_bead_violation():
    spec = bead_spec("B1005010-1", "2024-O")
    unlike = dataclasses.replace(geometry_of(spec), radius=9.0)
    outcome = gate(
        bead_provider([stamp("B1005010-1")], [UnstampedFeature("Bead(12)", unlike)], spec), "Aluminum 7075-T6"
    )
    assert outcome.decision is RuleDecision.WARN
    assert any(o.reason_code == SheetMetalCodes.MATERIAL_NOT_ALLOWED_BY_BEAD for o in outcome.inner)


def test_features_that_could_not_be_read_warn_rather_than_count_as_no_beads():
    provider = BeadMaterialConstraintProvider(
        FixedInventory(BodyFeatureInventory.unreadable("NX 12345: feature list unavailable")),
        SpecLookup(),
        TABLE,
        BeadSettings.DEFAULT,
        FakeRequirementReader(),
    )
    outcome = gate(provider, "Aluminum 2024-O")
    assert outcome.decision is RuleDecision.WARN
    assert outcome.reason_code == SheetMetalCodes.BEAD_FEATURES_UNREADABLE
    assert "NX 12345" in (outcome.message or "")


def test_only_materials_every_spec_permits_are_allowed():
    provider = bead_provider(
        [stamp("B1005010-1"), stamp("B1005010-2")],
        (),
        bead_spec("B1005010-1", "2024-O", "5052-O"),
        bead_spec("B1005010-2", "2024-O", "7075-T6"),
    )
    candidates = ["Aluminum 2024-O", "Aluminum 5052-O", "Aluminum 7075-T6", "Titanium Grade 5"]
    assert [m for m in candidates if gate(provider, m).decision is RuleDecision.ALLOW] == ["Aluminum 2024-O"]


@pytest.mark.parametrize("material", ["Aluminum 2024-O", "Aluminum 5052-O", "Aluminum 7075-T6"])
def test_agrees_with_material_allowed_rule_about_what_a_spec_permits(material: str):
    spec = bead_spec("B1005010-1", "2024-O", "7075-T6")
    grade = TABLE.grade_for_physical_material(material)
    spec_direction = MaterialAllowedRule().evaluate(BeadValidationContext(SheetMetalProfile(0.02, grade), spec))
    material_direction = gate(bead_provider([stamp("B1005010-1")], (), spec), material)
    assert (spec_direction.decision is RuleDecision.ALLOW) == (material_direction.decision is RuleDecision.ALLOW)
