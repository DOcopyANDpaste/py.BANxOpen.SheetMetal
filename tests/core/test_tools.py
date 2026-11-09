"""The tool facades: the bead dialog's decisions (moved from the C# presenter) and the Material Assignment dialog's
sheet metal support (moved from its SheetMetalSupport folder)."""

from __future__ import annotations

import dataclasses

import pytest

from banxopen.foundation import BodyId, BodyKind
from banxopen.materials import BodyMaterialAssignment
from banxopen.sheetmetal import (
    NO_BEAD,
    NO_FILTER,
    UNSTAMPED_BEAD,
    BeadControls,
    BeadSpecValidator,
    BeadTracebackResult,
    BodyCheck,
    PendingSheetMetal,
    SheetMetalAssignmentSupport,
    SheetMetalPartPreference,
    SheetMetalPreferenceStatus,
    SheetMetalRowFilter,
    StandardInfo,
    SyncAction,
)
from banxopen.sheetmetal.core.tools.bead_tool import BeadBody, BeadPart, BeadTarget, BeadToolLogic, chain_error

from builders import STANDARD, aluminum_table, body, requirement, spec_row, stamped, unmatched

TABLE = aluminum_table()
_ROW = TABLE.find("2024-O_0.020")
assert _ROW is not None
ROW = _ROW
STANDARDS = (StandardInfo(STANDARD, STANDARD, "folder"),)
S1 = spec_row("B1005010-1", 0.02, "2024-O")
S2 = spec_row("B1005010-2", 0.02, "5052-O")
S1_AT_032 = spec_row("B1005010-1", 0.032, "2024-O")
NAMES = ("B1005010", "S5010")


def part(row=ROW, thickness: float = 0.02, material: str | None = "Aluminum 2024-O", **changes) -> BeadPart:
    preference = SheetMetalPartPreference(row.name if row else None, True, 0.02, 1, row)
    base = BeadPart(preference, BeadBody(BodyId("b"), "SM_BODY", thickness, material), None, standards=STANDARDS)
    return dataclasses.replace(base, **changes)


LOADS: list[str] = []


def logic(rows=(S1, S2, S1_AT_032), reason: str | None = None, families=None) -> BeadToolLogic:
    loads = LOADS
    loads.clear()

    def load(standard, bead_spec):
        loads.append(bead_spec)
        return tuple(rows), reason

    return BeadToolLogic(
        BeadSpecValidator.standard(),
        lambda std, spec: (families or {}).get(spec, ()),
        load,
    )


def controls(**changes) -> BeadControls:
    return dataclasses.replace(BeadControls(NAMES, "B1005010"), **changes)


def new_chain(label: str = "Line 1", key: str = "C:1") -> BeadTarget:
    return BeadTarget(key, label, f"JID-{key}", NO_BEAD)


def existing(spec_id: str, key: str = "101", **changes) -> BeadTarget:
    stamp = BeadTracebackResult(True, False, STANDARD, spec_id, bead_spec="B1005010")
    return dataclasses.replace(BeadTarget(f"F:{key}", f"BEAD({key})", f"JID{key}", stamp, key), **changes)


def test_lists_the_specs_that_validate_against_the_preference_row():
    view = logic().view(part(), [], controls(picked_spec=S1))
    assert view.spec_rows == (S1,)
    assert view.mode == "Select curves or a sketch, or bead feature(s), to begin."
    assert view.blocking is None


def test_a_picked_spec_carries_across_while_listed_and_previews_its_values():
    view = logic().view(part(), [new_chain()], controls(picked_spec=S1))
    assert view.controls.picked_spec is S1
    assert view.spec_preview is not None
    assert view.spec_preview.height == S1.height
    assert view.mode == "New bead(s): 1 chain(s) selected."
    assert view.selection_lines == ("Line 1 — New bead",)


def test_without_a_pick_the_dialog_asks_for_one():
    view = logic().view(part(), [new_chain()], controls())
    assert view.mode == "Choose a SPEC from the bead options."
    assert view.blocking is None


@pytest.mark.parametrize(
    ("preference", "expected"),
    [
        (None, "Preferences could not be read"),
        (SheetMetalPartPreference("x", False, 0.02, 1), "Preference Entry is not in Material Table"),
        (SheetMetalPartPreference(None, True, 0.02, 1), "Preference has no material"),
        (SheetMetalPartPreference("Retired", True, 0.02, 1), "material 'Retired' is not in"),
    ],
)
def test_preferences_that_cannot_drive_the_dialog_block_and_say_why(preference, expected: str):
    view = logic().view(part(preference=preference), [new_chain()], controls(picked_spec=S1))
    assert view.blocking is not None
    assert expected in view.blocking
    assert not view.spec_rows


def test_a_standard_the_table_does_not_list_blocks():
    view = logic().view(part(standards=()), [], controls())
    assert "Standard 'XX_Standard' is not one the standards file lists" in (view.blocking or "")


def test_the_body_error_blocks_first():
    view = logic().view(part(body=None, body_error="This part has 2 sheet metal bodies"), [], controls())
    assert view.blocking == "This part has 2 sheet metal bodies"


def test_beads_on_the_body_that_refuse_the_grade_block_unless_selected():
    refusing = stamped("101", "BEAD(1)", "B1005010-2", S2)
    tool = logic()
    blocked = tool.view(part(beads=(refusing,)), [new_chain()], controls(picked_spec=S1))
    assert "is not allowed at 0.02 in" in (blocked.blocking or "")
    assert "'BEAD(1)' (SPEC B1005010-2)" in (blocked.blocking or "")
    selected = tool.view(part(beads=(refusing,)), [existing("B1005010-2")], controls(picked_spec=S1))
    assert selected.blocking is None


def test_a_bead_not_driven_for_the_sheet_is_reported_first_with_its_coverage():
    bead = stamped("101", "BEAD(1)", "B1005010-1", S1_AT_032)
    view = logic().view(part(beads=(bead,)), [], controls())
    assert "not driven for: SPEC B1005010-1 (bead 'BEAD(1)') covers 0.032" in (view.blocking or "")


def test_a_bead_spec_with_no_rows_blocks_with_its_reason_and_tells_once():
    tool = logic(rows=(), reason="no workbook in 'folder' is named for bead SPEC 'B1005010'")
    first = tool.view(part(), [], controls(), notify=True)
    assert "No bead SPEC is allowed under Standard 'XX_Standard'" in (first.blocking or "")
    assert (
        first.info == "Bead SPEC 'B1005010' offers no SPECs: no workbook in 'folder' is named for bead SPEC 'B1005010'."
    )
    assert tool.view(part(), [], controls(), notify=True).info is None


def test_no_row_matching_the_sheet_is_told_with_the_thicknesses_covered():
    tool = logic(rows=(S1_AT_032,))
    view = tool.view(part(), [], controls(), notify=True)
    assert "No row of bead SPEC 'B1005010' matches" in (view.info or "")
    assert "covers: 0.032 in" in (view.info or "")


def test_a_chain_that_cannot_be_one_bead_blocks_and_is_listed_as_an_error():
    error = chain_error(["BEAD(1)"], 2)
    broken = dataclasses.replace(new_chain(), chain_error=error)
    view = logic().view(part(), [broken], controls(picked_spec=S1))
    assert view.blocking == f"'Line 1' cannot be one bead: {error}."
    assert view.selection_lines[0].startswith("Line 1 — ERROR: mixes curves of bead BEAD(1) with 2 curve(s)")
    assert chain_error([], 3) is None
    assert chain_error(["BEAD(1)"], 0) is None
    assert "belong to beads BEAD(1), BEAD(2)" in (chain_error(["BEAD(1)", "BEAD(2)"], 0) or "")


def test_a_spec_that_does_not_validate_blocks_with_the_alternatives():
    view = logic().view(part(), [existing("B1005010-2")], controls(picked_spec=S2))
    assert "not allowed on material grade '2024-O'" in (view.blocking or "")
    assert "Valid alternatives under this bead SPEC: B1005010-1." in (view.blocking or "")
    assert S2 in view.kept_for_beads  # kept for the selected bead, coloured as a warning


def test_a_selected_stamped_bead_prefills_its_bead_spec_and_row():
    tool = logic()
    view = tool.view(part(), [existing("B1005010-1")], controls(bead_spec="S5010"), selection_changed=True)
    assert view.controls.bead_spec == "B1005010"
    assert view.controls.picked_spec == S1
    assert view.selection_lines == ("BEAD(101) — Editing SPEC B1005010-1",)
    assert view.mode == "Editing existing bead(s): 1."


def test_an_older_stamp_resolves_its_bead_spec_from_the_spec_id_or_warns():
    old = existing("B1005010-1", traceback=BeadTracebackResult(True, False, STANDARD, "B1005010-1"))
    resolved = logic(families={"B1005010-1": (S1,)}).view(
        part(), [old], controls(bead_spec=None), selection_changed=True
    )
    assert resolved.controls.bead_spec == "B1005010"
    unresolved = logic().view(part(), [old], controls(bead_spec=None), selection_changed=True)
    assert "could not be matched to one of this dialog's bead SPECs" in (unresolved.warnings or "")


def test_unstamped_and_unknown_beads_are_warned_about():
    unstamped = existing("x", traceback=UNSTAMPED_BEAD, unmatched_reason="its geometry matches no SPEC in any Standard")
    view = logic().view(part(beads=(unmatched("7", "BEAD(7)"),)), [unstamped], controls())
    assert view.selection_lines[0].startswith("BEAD(101) — WARNING: not created by this tool, its geometry")
    assert "1 selected bead(s) were not created by this tool" in (view.warnings or "")
    assert "The SPEC of bead(s) 'BEAD(7)' on this body is not known" in (view.warnings or "")


def test_body_material_disagreeing_with_the_preferences_is_an_advisory():
    assert "has no physical material" in (logic().view(part(material=None), [], controls()).warnings or "")
    differs = logic().view(part(material="Aluminum 5052-O"), [], controls()).warnings
    assert "is 'Aluminum 5052-O', but the Sheet Metal Preferences name 'Aluminum 2024-O'" in (differs or "")


def test_stale_requirements_are_warned_about():
    refusing = requirement("B1005010-2", 0.02, "5052-O")
    rebuild = requirement(
        "B1005010-3", 0.032, spec_row("B1005010-3", 0.02, "2024-O"), spec_row("B1005010-3", 0.032, "2024-O")
    )
    view = logic().view(part(requirements=(refusing, rebuild), requirement_errors=("NX 1",)), [], controls())
    warnings = view.warnings or ""
    assert "bead requirements could not be read: NX 1" in warnings
    assert (
        "Bead SPEC B1005010-2 (Standard XX) does not allow '2024-O'" in warnings
        or "does not allow '2024-O'" in warnings
    )
    assert "were built at 0.032 in, but the sheet is now 0.02 in" in warnings


def test_a_revised_workbook_is_reported_not_rewritten():
    bead = stamped("101", "BEAD(1)", "B1005010-1", S1)
    recorded = requirement("B1005010-1", 0.02, "2024-O", "5052-O")
    assert "its workbook has changed" in (
        logic().view(part(beads=(bead,), requirements=(recorded,)), [], controls()).warnings or ""
    )


def test_shared_labels_are_made_distinct_and_the_preference_summary_names_the_row():
    view = logic().view(part(), [new_chain("Line 1", "C:1"), new_chain("Line 1", "C:2")], controls())
    assert view.selection_lines == ("Line 1 [JID-C:1] — New bead", "Line 1 [JID-C:2] — New bead")
    assert (
        view.preference_summary
        == "Sheet Metal Preferences: '2024-O_0.020', t 0.02, grade 2024-O (Standard XX_Standard)"
    )
    assert "Body: SM_BODY" in view.status


def test_apply_is_refused_with_the_view_blocker_or_a_missing_pick_or_selection():
    tool = logic()
    assert tool.apply_blocker(part(), [new_chain()], controls()) == "Choose a SPEC from the bead options first."
    assert tool.apply_blocker(part(), [], controls(picked_spec=S1)) is not None
    assert tool.apply_blocker(part(), [new_chain()], controls(picked_spec=S1)) is None
    assert tool.stamped_bead_spec(controls(), dataclasses.replace(S1, workbook_name="BA_B1005010_revC")) == "B1005010"


def test_the_workbook_is_loaded_once_per_standard_and_bead_spec():
    tool = logic()
    tool.view(part(), [], controls())
    tool.view(part(), [], controls())
    tool.view(part(), [], controls(bead_spec="S5010"))
    assert LOADS == ["B1005010", "S5010"]


# ---- the Material Assignment dialog's sheet metal support ----

SUPPORT = SheetMetalAssignmentSupport(TABLE)
PREFERENCE = SheetMetalPartPreference("2024-O_0.020", True, 0.02, 2, ROW)
A, B = body(body_id=BodyId("a"), name="A"), body(body_id=BodyId("b"), name="B")


def assigned(body_id: str, material: str) -> BodyMaterialAssignment:
    return BodyMaterialAssignment(body_id=BodyId(body_id), material_name=material, resolved_material_id=None)


def test_a_body_with_no_material_is_out_of_sync_with_preferences_naming_a_row():
    result = SUPPORT.check_body(None, 0.02, PREFERENCE)
    assert result.status is SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC
    assert "this body has no material" in (result.message or "")
    assert SUPPORT.check_body("Aluminum 2024-O", None, PREFERENCE).status is SheetMetalPreferenceStatus.IN_SYNC


def test_the_grade_column_marks_how_the_body_stands():
    out_of_sync = SUPPORT.check_body("Aluminum 5052-O", 0.02, PREFERENCE)
    assert SUPPORT.grade_for_body(out_of_sync, PREFERENCE) == "2024-O_0.020 (material mismatch)"
    assert SUPPORT.grade_for_body(SUPPORT.check_body("Aluminum 2024-O", 0.02, PREFERENCE), PREFERENCE) == "2024-O_0.020"
    assert SUPPORT.grade_for_body(None, PREFERENCE) == ""
    assert SUPPORT.grade_for_material("aluminum 2024-o", PREFERENCE) == "2024-O_0.020"
    assert SUPPORT.grade_for_material("Aluminum 5052-O", PREFERENCE) == ""


def test_the_open_message_lists_bodies_and_the_sync_hint():
    check = BodyCheck(A, SUPPORT.check_body("Aluminum 5052-O", 0.02, PREFERENCE))
    message = SUPPORT.open_message(PREFERENCE, [check], SyncAction.STAGED)
    assert "  [A] Sheet Metal Preferences have material '2024-O_0.020'" in message
    assert "was added to pending for the body. Apply to sync them." in message
    assert SUPPORT.suggested_sync([A], [check], PREFERENCE) == (A, ROW)
    assert SUPPORT.suggested_sync([A, B], [check], PREFERENCE) is None


def test_rows_for_a_library_and_the_default_filter():
    rows = SUPPORT.rows_for_library(["Aluminum 2024-O"])
    assert [r.name for r in rows] == ["2024-O_0.020", "2024-O_0.032"]
    assert SUPPORT.default_filter(PREFERENCE, rows) == SheetMetalRowFilter.for_row(ROW)
    assert SUPPORT.default_filter(PREFERENCE, ()) is NO_FILTER


def conflict(material: str, row_name: str | None, targets, current=None, pending=(), part_row=ROW):
    reads: list[int] = []

    def read_part_row():
        reads.append(1)
        return part_row

    result = SUPPORT.find_conflict(
        material, TABLE.find(row_name), targets, [A, B], current or {}, pending, read_part_row
    )
    return result, len(reads)


def test_no_sheet_metal_target_never_conflicts():
    assert conflict("Aluminum 5052-O", None, [body(BodyKind.SOLID)])[0] is None


def test_another_sheet_metal_body_with_another_material_conflicts():
    message, reads = conflict("Aluminum 5052-O", "5052-O_0.020", [A], {B.id: assigned("b", "Aluminum 2024-O")})
    assert message is not None
    assert message.startswith("Aluminum 5052-O [5052-O_0.020] cannot be assigned to sheet metal bodies")
    assert "  [B] Aluminum 2024-O [2024-O_0.020]" in message
    assert reads == 1


def test_the_same_material_and_row_or_assigning_every_body_together_does_not_conflict():
    current = {B.id: assigned("b", "Aluminum 2024-O")}
    assert conflict("Aluminum 2024-O", "2024-O_0.020", [A], current)[0] is None
    assert conflict("Aluminum 5052-O", "5052-O_0.020", [A, B], current)[0] is None


def test_pending_entries_count_as_what_each_body_will_have():
    staged = (PendingSheetMetal((B.id,), "Aluminum 5052-O", TABLE.find("5052-O_0.020")),)
    message, reads = conflict("Aluminum 2024-O", "2024-O_0.020", [A], {B.id: assigned("b", "Aluminum 2024-O")}, staged)
    assert "  [B] Aluminum 5052-O [5052-O_0.020] (pending)" in (message or "")
    assert reads == 0
    removal = (PendingSheetMetal((B.id,), None),)
    assert conflict("Aluminum 5052-O", None, [A], {B.id: assigned("b", "Aluminum 2024-O")}, removal)[0] is None
