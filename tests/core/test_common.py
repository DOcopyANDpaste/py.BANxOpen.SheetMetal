"""Ports of Common/*Tests.cs and SheetMetalTextTests.cs: the config locator, the settings, the preference check, and
the describer reaching every message."""

from __future__ import annotations

from pathlib import Path

import pytest

from banxopen.foundation import (
    FoundationCodes,
    RequirementRead,
    RequirementSeverity,
    RequirementSpec,
    RequirementStatus,
    RequirementValueType,
    SetOfValuesRule,
    StoredRequirement,
)
from banxopen.sheetmetal import (
    BEAD_DOMAIN,
    NOTHING_REBUILT,
    PREFERENCE_THICKNESS_TOLERANCE,
    MaterialAllowedRule,
    SheetMetalCodes,
    SheetMetalConfigLocator,
    SheetMetalMaterialTable,
    SheetMetalPartPreference,
    SheetMetalPreferenceConstraintProvider,
    SheetMetalPreferenceRead,
    SheetMetalPreferenceStatus,
    SheetMetalProfile,
    SheetMetalRequirementNames,
    SheetMetalSettings,
    SheetMetalText,
    SpecDataError,
    ThicknessMatchRule,
    check_preference,
    create_bead_requirements,
    requirements_from_read,
)
from banxopen.sheetmetal.core.beads.rules import BeadValidationContext

from builders import BODY_ID, FixedPreferenceReader, aluminum_table, gate, spec_row, stamped

TABLE = aluminum_table()

# ---- the config locator ----


@pytest.fixture
def ugii(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("user", "group", "site"):
        (tmp_path / name).mkdir()
    monkeypatch.setenv("UGII_USER_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("UGII_GROUP_DIR", str(tmp_path / "group"))
    monkeypatch.setenv("UGII_SITE_DIR", str(tmp_path / "site"))
    return tmp_path


def root_in(ugii_dir: Path, settings_json: str = "{}") -> Path:
    settings = ugii_dir / "SHEETMETAL" / "BA_NXOPEN_settings"
    settings.mkdir(parents=True)
    (settings / "sheetmetal-settings.json").write_text(settings_json, encoding="utf-8")
    return ugii_dir / "SHEETMETAL"


def test_user_wins_over_group_and_site(ugii: Path):
    user = root_in(ugii / "user")
    root_in(ugii / "group")
    root_in(ugii / "site")
    assert SheetMetalConfigLocator().root_directory == user


def test_group_wins_over_site(ugii: Path):
    group = root_in(ugii / "group")
    root_in(ugii / "site")
    assert SheetMetalConfigLocator().root_directory == group


def test_ignores_a_sheetmetal_folder_without_the_settings_file(ugii: Path):
    (ugii / "user" / "SHEETMETAL").mkdir()
    site = root_in(ugii / "site")
    assert SheetMetalConfigLocator().root_directory == site


def test_skips_unset_variables(ugii: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("UGII_USER_DIR")
    site = root_in(ugii / "site")
    assert SheetMetalConfigLocator().root_directory == site


def test_a_given_root_wins_over_anything_found_by_searching(ugii: Path):
    root_in(ugii / "user")
    configured = ugii / "shared" / "SHEETMETAL"
    assert SheetMetalConfigLocator(configured).root_directory == configured


def test_the_material_table_is_in_the_root_not_the_settings_folder(ugii: Path):
    root = root_in(ugii / "site", '{ "materialTable": "sheet_metal_material_table.xml" }')
    (root / "sheet_metal_material_table.xml").write_text("", encoding="utf-8")
    assert SheetMetalConfigLocator().material_table_path == root / "sheet_metal_material_table.xml"


def test_an_unset_or_missing_material_table_is_reported(ugii: Path):
    root_in(ugii / "site")
    with pytest.raises(FileNotFoundError, match="materialTable is not set"):
        _ = SheetMetalConfigLocator().material_table_path


def test_reports_everywhere_it_looked_when_nothing_is_found(ugii: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("UGII_GROUP_DIR")
    with pytest.raises(FileNotFoundError) as error:
        _ = SheetMetalConfigLocator().root_directory
    message = str(error.value)
    assert str(ugii / "user" / "SHEETMETAL") in message
    assert "UGII_GROUP_DIR" in message
    assert str(ugii / "site" / "SHEETMETAL") in message


def test_the_cache_directory_is_created_under_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    cache = SheetMetalConfigLocator(tmp_path).cache_directory
    assert cache == tmp_path / "BANxOpen.SheetMetal" / "cache"
    assert cache.is_dir()


# ---- the settings ----


def write_settings(tmp_path: Path, document: str) -> Path:
    path = tmp_path / "sheetmetal-settings.json"
    path.write_text(document, encoding="utf-8")
    return path


def test_a_missing_or_null_table_is_unset(tmp_path: Path):
    assert SheetMetalSettings.load(tmp_path / "absent.json").material_table is None
    assert SheetMetalSettings.load(write_settings(tmp_path, '{ "materialTable": null }')).material_table is None


def test_the_table_file_name_is_read_as_given(tmp_path: Path):
    settings = SheetMetalSettings.load(
        write_settings(tmp_path, '{ "materialTable": "sheet_metal_material_table.xml" }')
    )
    assert settings.material_table == "sheet_metal_material_table.xml"


def test_invalid_json_is_an_error_not_a_silent_default(tmp_path: Path):
    with pytest.raises(SpecDataError):
        SheetMetalSettings.load(write_settings(tmp_path, "{ not json"))


def test_the_shipped_settings_sample_loads():
    shipped = Path(__file__).resolve().parents[2] / "config-samples" / "sheetmetal-settings.json"
    assert SheetMetalSettings.load(shipped).material_table is None


# ---- the preference check ----


def preference(material: str | None = "2024-O_0.020", material_table: bool = True, thickness: float = 0.02):
    return SheetMetalPartPreference(material, material_table, thickness, 1, TABLE.find(material))


def check(body_material: str | None = "Aluminum 2024-O", body_thickness: float = 0.02, pref=None):
    return check_preference(body_material, body_thickness, pref or preference())


def test_a_row_of_the_bodys_material_at_its_thickness_is_in_sync():
    result = check()
    assert result.status is SheetMetalPreferenceStatus.IN_SYNC
    assert result.message is None


def test_a_row_made_of_a_different_material_is_out_of_sync_naming_both():
    result = check(pref=preference("5052-O_0.020"))
    assert result.status is SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC
    assert "Aluminum 5052-O" in (result.message or "") and "Aluminum 2024-O" in (result.message or "")


def test_the_right_material_outside_material_table_entry_is_not_set():
    result = check(pref=preference(material_table=False))
    assert result.status is SheetMetalPreferenceStatus.MATERIAL_NOT_SET
    assert "Material Table" in (result.message or "")


def test_preferences_with_no_material_are_not_set():
    assert check(pref=preference(material=None)).status is SheetMetalPreferenceStatus.MATERIAL_NOT_SET


def test_a_material_the_standards_file_does_not_list_is_reported_by_name():
    result = check(pref=preference(material="Retired_Material"))
    assert result.status is SheetMetalPreferenceStatus.MATERIAL_NOT_IN_TABLE
    assert "Retired_Material" in (result.message or "")


def test_physical_material_names_are_compared_without_regard_to_case():
    assert check(body_material="ALUMINUM 2024-o").status is SheetMetalPreferenceStatus.IN_SYNC


def test_thickness_beyond_tolerance_mismatches_and_within_is_in_sync():
    result = check(pref=preference(thickness=0.02 + PREFERENCE_THICKNESS_TOLERANCE * 2))
    assert result.status is SheetMetalPreferenceStatus.THICKNESS_MISMATCH
    assert "0.02" in (result.message or "")
    assert check(pref=preference(thickness=0.02 + PREFERENCE_THICKNESS_TOLERANCE / 2)).status is (
        SheetMetalPreferenceStatus.IN_SYNC
    )


def test_material_is_reported_before_thickness():
    assert (
        check(pref=preference("5052-O_0.020", thickness=0.05)).status is SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC
    )


def test_a_body_without_material_is_checked_on_thickness_only():
    assert check(body_material=None, pref=preference("5052-O_0.020")).status is SheetMetalPreferenceStatus.IN_SYNC
    assert check(body_material=None, pref=preference("5052-O_0.020", thickness=0.05)).status is (
        SheetMetalPreferenceStatus.THICKNESS_MISMATCH
    )


# ---- the describer ----


class CustomText(SheetMetalText):
    def bead_thickness_mismatch(self, spec: str, spec_thickness: str, sheet_thickness: str) -> str:
        return f"custom thickness {spec} {spec_thickness}/{sheet_thickness}"

    def bead_spec_not_allowed_on_grade(self, spec: str, grade: str) -> str:
        return f"custom grade {spec} {grade}"

    preferences_have_no_material = "custom no material"

    def preferences_shared_by_bodies(self, count: int) -> str:
        return f"custom shared {count}"

    source_preferences = "custom source"

    def requirement_key_malformed(self, key: str) -> str:
        return f"custom key {key}"

    def requirement_not_recorded_thickness_unknown(self, feature_noun: str, spec: str, standard: str) -> str:
        return f"custom unknown {spec}"

    def material_table_not_found(self, path: str) -> str:
        return "custom table missing"


ROW = spec_row("B1005010-1", 0.02, "2024-O")


def test_bead_rules_word_their_outcomes_with_the_describer_and_carry_their_values():
    thickness = ThicknessMatchRule(CustomText()).evaluate(
        BeadValidationContext(SheetMetalProfile(0.032, "2024-O"), ROW)
    )
    grade = MaterialAllowedRule(CustomText()).evaluate(BeadValidationContext(SheetMetalProfile(0.02, "5052-O"), ROW))
    assert thickness.message == "custom thickness B1005010-1 0.02/0.032"
    assert thickness.reason_code == SheetMetalCodes.THICKNESS_MISMATCH
    assert thickness.args["sheetThickness"] == "0.032"
    assert grade.message == "custom grade B1005010-1 5052-O"
    assert grade.reason_code == SheetMetalCodes.MATERIAL_NOT_ALLOWED
    assert grade.args["grade"] == "5052-O"


def test_the_preference_check_words_its_result_with_the_describer():
    result = check_preference("Aluminum 2024-O", 0.02, SheetMetalPartPreference(None, True, 0.02, 1), CustomText())
    assert result.status is SheetMetalPreferenceStatus.MATERIAL_NOT_SET
    assert result.message == "custom no material"


def test_a_constraint_provider_words_its_violation_and_its_source_with_the_describer():
    pref = SheetMetalPartPreference("2024-O_0.020", True, 0.02, 2, TABLE.find("2024-O_0.020"))
    provider = SheetMetalPreferenceConstraintProvider(
        FixedPreferenceReader(SheetMetalPreferenceRead.of(pref)), TABLE, CustomText()
    )
    assert provider.constraints_for(BODY_ID)
    assert gate(provider, "Aluminum 2024-O").message == "custom shared 2 (required by custom source)"


def test_the_requirement_mapper_words_read_errors_with_the_describer():
    spec = RequirementSpec(
        domain=BEAD_DOMAIN,
        key="no-pipe",
        aspect=SheetMetalRequirementNames.PAIR_ASPECT,
        value_type=RequirementValueType.STRING,
        rule=SetOfValuesRule(("2024-O",)),
        severity=RequirementSeverity.ERROR,
        checked_formula="expr",
        description=("line",),
        associated_object_keys=(),
    )
    _, errors = requirements_from_read(
        RequirementRead((StoredRequirement(spec, RequirementStatus.UNKNOWN),), ()), "Bead SPEC", CustomText()
    )
    assert errors == ("custom key no-pipe",)


def test_the_requirement_factory_words_its_notes_with_the_describer():
    result = create_bead_requirements(
        [stamped("101", "BEAD(1)", "B1005010-1", ROW)], (), NOTHING_REBUILT, None, CustomText()
    )
    assert result.notes == ("custom unknown B1005010-1",)


def test_a_config_loader_words_its_error_with_the_describer(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="custom table missing"):
        SheetMetalMaterialTable.load(tmp_path / "absent.txt", CustomText())


def test_the_dialog_rule_for_a_body_with_no_material_is_worded_here():
    assert SheetMetalText.DEFAULT.preference_body_has_no_material("2024-O_0.020", "Aluminum 2024-O") == (
        "Sheet Metal Preferences have material '2024-O_0.020', made of 'Aluminum 2024-O', "
        "but this body has no material."
    )


def test_unreadable_requirements_use_foundations_code():
    assert FoundationCodes.REQUIREMENTS_UNREADABLE
    assert SheetMetalCodes.ROW_THICKNESS_MISMATCH == "SHEETMETAL_ROW_THICKNESS_MISMATCH"
