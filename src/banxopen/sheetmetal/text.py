"""The wording of every message the sheet metal libraries put in front of a user, as default text a tool may override:
one method or attribute per message, and a `DEFAULT` instance. Code that produces a message takes `text:
SheetMetalText | None` and falls back to `DEFAULT`; the codes are `SheetMetalCodes`. Log lines are not user text, and
the requirement text stored in the part (the requirement mapper) is data, so neither is here.

Thicknesses arrive already formatted (`format_thickness`); NX error details arrive already formatted
(`NxDialogText.nx_error`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from banxopen.sheetmetal.model.beads import BeadOnBody, BeadSpecStatus

_PARA = "\n\n"


class SheetMetalText:
    DEFAULT: ClassVar[SheetMetalText]

    # ---- Bead SPEC validation ----

    def bead_thickness_mismatch(self, spec: str, spec_thickness: str, sheet_thickness: str) -> str:
        return (
            f"SPEC '{spec}' is driven for {spec_thickness} in material, but this sheet metal is {sheet_thickness} in."
        )

    bead_material_missing = (
        "This sheet metal has no material assigned; a material must be assigned before a SPEC can be validated."
    )

    def bead_grade_not_a_column(self, grade: str, standard: str) -> str:
        return (
            f"Material grade '{grade}' is not available in Standard '{standard}'s workbook — "
            "Please Contact Admin before assigning this material to the sheet metal."
        )

    def bead_spec_not_allowed_on_grade(self, spec: str, grade: str) -> str:
        return f"SPEC '{spec}' is not allowed on material grade '{grade}'."

    def bead_unidentified_reason(self, bead: BeadOnBody) -> str | None:
        """Why an unstamped bead's SPEC is not known, as a clause; None when it is known or the bead is stamped."""
        match bead.status:
            case BeadSpecStatus.GEOMETRY_UNREADABLE:
                return "its geometry could not be read"
            case BeadSpecStatus.AMBIGUOUS:
                # Named by workbook and SPEC: two workbooks of a Standard could carry the same SpecId.
                names = ", ".join(f"{c.workbook_name}/{c.spec_id}" for c in bead.candidates)
                return f"its geometry matches several SPECs ({names})"
            case BeadSpecStatus.UNMATCHED:
                return "its geometry matches no SPEC in any Standard"
            case _:
                return None

    # ---- Sheet Metal Preferences check ----

    preferences_have_no_material = "Sheet Metal Preferences have no material."
    preferences_not_material_table = (
        "Sheet Metal Preferences are not set to Material Table entry, so NX is not using a material from the "
        "sheet metal material standards file."
    )

    def preference_material_not_in_table(self, material: str) -> str:
        return (
            f"Sheet Metal Preferences have material '{material}', and it is not in the BA sheet metal material "
            "standards file."
        )

    def preference_material_out_of_sync(self, row: str, physical_material: str, body_material: str) -> str:
        return (
            f"Sheet Metal Preferences have material '{row}', made of '{physical_material}', but this body's material "
            f"is '{body_material}'."
        )

    def preference_body_has_no_material(self, row: str, physical_material: str) -> str:
        """A body with no material, against preferences that name one: out of sync for a tool that assigns it."""
        return (
            f"Sheet Metal Preferences have material '{row}', made of '{physical_material}', but this body has no "
            "material."
        )

    def preference_thickness_mismatch(self, body_thickness: str, preference_thickness: str) -> str:
        return (
            f"This sheet metal is {body_thickness} thick, but Sheet Metal Preferences specify {preference_thickness}. "
            "Correct the body's thickness or the preferences before validating a SPEC."
        )

    # Short forms, for a sentence saying why the preferences cannot be used.
    preferences_unreadable_reason = "Preferences could not be read"
    preferences_not_material_table_reason = "Preference Entry is not in Material Table"
    preferences_no_material_reason = "Preference has no material"

    def preferences_material_not_in_table_reason(self, material: str) -> str:
        return f"material '{material}' is not in the sheet metal material standards file"

    def preferences_standard_not_listed_reason(self, standard: str) -> str:
        return f"Standard '{standard}' is not one the standards file lists"

    preferences_no_row_reason = "Preference has no row in the standards file"

    # Labels for the state the preferences are in.
    preference_state_not_material_table = "Preference Entry is not in Material Table"
    preference_state_no_material = "Preference has no material"

    def preference_state_not_in_table(self, material: str) -> str:
        return f"'{material}' (not in the standards file)"

    def preference_state_material(self, material: str) -> str:
        return f"'{material}'"

    preference_state_unreadable = "Preferences could not be read"

    def preferences_body_gone(self, body_id: str) -> str:
        return f"body '{body_id}' is no longer in the work part"

    preferences_no_thickness = "Sheet Metal Preferences have no material thickness"
    no_preferences_returned = "no preferences were returned"

    # ---- Constraints on a body's material ----

    source_body_features = "this body's features"

    def source_stamped_bead(self, spec: str, standard: str) -> str:
        return f"Bead SPEC {spec} (Standard {standard})"

    def source_matched_bead(self, name: str, spec: str, standard: str) -> str:
        return f"bead feature '{name}' (unstamped; geometry matches SPEC {spec}, Standard {standard})"

    def source_unidentified_bead(self, name: str) -> str:
        return f"bead feature '{name}'"

    def source_bead_spec_count(self, count: int) -> str:
        return f"{count} bead SPECs on this body"

    source_preferences = "Sheet Metal Preferences"
    source_part_requirements = "this part's sheet metal requirements"

    def bead_features_unreadable(self, error: str) -> str:
        return f"The features on this body could not be read, so its bead SPEC restrictions cannot be checked: {error}"

    def bead_grade_unrecognized(self, material: str) -> str:
        return (
            f"Material '{material}' has no row in the sheet metal material standards file, so it cannot be "
            "checked against the bead SPECs on this body. Add a row for it before assigning."
        )

    def bead_geometry_unreadable(self, name: str) -> str:
        return (
            f"Bead feature '{name}' was not created by the bead tool and its geometry could not be "
            "read, so its SPEC material restriction is not enforced. Re-apply it with the bead dialog to "
            "record its SPEC."
        )

    def bead_ambiguous(self, name: str, candidates: str) -> str:
        return (
            f"Bead feature '{name}' was not created by the bead tool and its geometry matches more "
            f"than one SPEC ({candidates}), so no SPEC material "
            "restriction is enforced for it. Re-apply it with the bead dialog to record which SPEC it is."
        )

    def bead_unmatched(self, name: str) -> str:
        return (
            f"Bead feature '{name}' was not created by the bead tool and its geometry matches no SPEC "
            "in any Standard, so no SPEC material restriction is enforced for it. Re-apply it with the bead "
            "dialog using a valid SPEC."
        )

    def bead_spec_not_found(self, spec: str, standard: str) -> str:
        return (
            f"SPEC '{spec}' is no longer in Standard '{standard}', so no material can be "
            "verified against the bead built to it. Check the Standard's workbook, or re-apply the bead "
            "with a current SPEC."
        )

    def bead_material_not_allowed(self, material: str, grade: str | None, spec: str) -> str:
        return (
            f"Material '{material}' (grade '{grade or ''}') is not allowed by SPEC '{spec}' at any thickness it is "
            "driven for. Re-apply the bead with a SPEC that allows it."
        )

    def requirement_pairs_changed(self, requirement: str, workbook_pairs: str, recorded_pairs: str) -> str:
        return (
            f"{requirement}: its workbook now allows {workbook_pairs}, but the part's requirement records "
            f"{recorded_pairs}. The requirement still applies; re-apply the beads with the Bead tool to bring it up "
            "to date."
        )

    def preferences_unreadable_for_assignment(self, error: str) -> str:
        return (
            "This part's Sheet Metal Preferences could not be read, so they cannot be updated to match the assigned "
            f"material: {error}"
        )

    def preference_material_has_no_row(self, material: str, column: str) -> str:
        return (
            f"Material '{material}' has no row in the sheet metal material standards file "
            f"({column} = '{material}'), so Sheet Metal Preferences "
            "cannot be updated to match it. Add a row for it before assigning."
        )

    def preferences_shared_by_bodies(self, count: int) -> str:
        return (
            f"This part has {count} sheet metal bodies. Sheet Metal Preferences "
            "belong to the part, so this assignment sets the preferences' material for all of them."
        )

    def requirements_unreadable_for_assignment(self, errors: str) -> str:
        return (
            "This part's sheet metal requirements could not be read, so the material was not checked against them: "
            f"{errors} The assignment goes ahead; re-open the Bead tool to rewrite them."
        )

    def requirement_material_not_allowed(self, material: str, requirement: str, allowed_pairs: str) -> str:
        return (
            f"Material '{material}' has no row {requirement} allows (allowed: {allowed_pairs}). "
            f"{requirement} will need to be re-applied; its NX requirement check reports it until then."
        )

    # ---- The picked Sheet Metal Preferences row ----

    def row_not_offered(self, row: str, material: str, standard: str | None) -> str:
        where = "in the sheet metal material standards file" if standard is None else f"under Standard '{standard}'"
        return (
            f"'{row}' is not one of the Sheet Metal Preferences materials that can be set for '{material}' {where}. "
            "Pick another row."
        )

    row_refused_requirements_unreadable = (
        "This part's sheet metal requirements could not be read, so this row was not checked against them. "
        "The assignment goes ahead; re-open the Bead tool to rewrite them."
    )

    def row_pair_not_allowed(
        self, row: str, requirement: str, grade: str, row_thickness: str, allowed_pairs: str
    ) -> str:
        return (
            f"'{row}' ({grade} at {row_thickness} in) is not allowed by {requirement} (allowed: {allowed_pairs}). "
            f"{requirement} will need to be re-applied; its NX requirement check reports it until then."
        )

    def row_needs_reapply(self, row: str, requirement: str, built_thickness: str, row_thickness: str) -> str:
        return (
            f"'{row}' is allowed by {requirement}, but its features were built at {built_thickness} in. "
            f"{requirement} will need to be re-applied at {row_thickness} in to rebuild their geometry."
        )

    def row_thickness_mismatch(self, row: str, row_thickness: str, body_thickness: str) -> str:
        return (
            f"'{row}' is {row_thickness} thick but this body is {body_thickness} thick; the part's "
            "material and geometry will disagree about thickness."
        )

    # ---- Requirements held in the part ----

    def requirement_key_malformed(self, key: str) -> str:
        return f"Requirement key '{key}' is not a Standard|SPEC pair."

    def requirement_incomplete(self, feature_noun: str, spec: str, standard: str) -> str:
        return (
            f"The requirement for {feature_noun} {spec} (Standard {standard}) is incomplete or was "
            "edited by hand; re-apply its features to rewrite it."
        )

    def requirement_rewritten(self, error: str) -> str:
        return f"{error} It was rewritten from its SPEC workbook."

    def requirement_recorded_at_current_thickness(
        self, feature_noun: str, spec: str, standard: str, thickness: str
    ) -> str:
        return (
            f"{feature_noun} {spec} (Standard {standard}) had no requirement; it was recorded at the sheet's current "
            f"{thickness} in."
        )

    def requirement_not_recorded_not_driven(self, feature_noun: str, spec: str, standard: str, thickness: str) -> str:
        return (
            f"{feature_noun} {spec} (Standard {standard}) has no requirement, and none could be "
            f"recorded: the SPEC is not driven for the sheet's {thickness} in. Re-apply its beads at a thickness it "
            "covers."
        )

    def requirement_not_recorded_thickness_unknown(self, feature_noun: str, spec: str, standard: str) -> str:
        return (
            f"{feature_noun} {spec} (Standard {standard}) has no requirement, and none could be "
            "recorded: the sheet's thickness is unknown."
        )

    def thickness_expression_missing(self, expression: str) -> str:
        return (
            f"This part has no '{expression}' expression, so a bead requirement has nothing to check the thickness "
            "against. Set the Sheet Metal Preferences to a Material Table entry first."
        )

    def beads_unreadable_for_requirements(self, error: str) -> str:
        return f"The beads on this body could not be read, so their requirements were not updated: {error}"

    def requirements_unreadable_for_refresh(self, errors: str) -> str:
        return f"This part's requirements could not be read, so they were not updated: {errors}"

    def grade_expression_failed(self, expression: str, nx_detail: str) -> str:
        return f"The '{expression}' expression could not be written: {nx_detail}"

    def grade_attribute_failed(self, attribute: str, nx_detail: str) -> str:
        return f"Part attribute '{attribute}' could not be set: {nx_detail}"

    # ---- Part profile and preference service ----

    def profile_not_sheet_metal(self, body: str) -> str:
        return f"'{body}' is not a sheet metal body."

    def profile_not_checkable(self, body: str) -> str:
        return f"'{body}' could not be checked as sheet metal."

    thickness_unreadable = "Could not read this sheet metal's thickness."

    def profile_preferences_unreadable(self, error: str) -> str:
        return f"Could not read this part's Sheet Metal Preferences: {error}."

    def preferences_set_failed(self, material: str, detail: str) -> str:
        return f"Sheet Metal Preferences could not be set to '{material}': {detail}"

    def preferences_not_applied(self, material: str) -> str:
        return (
            "Sheet Metal Preferences were updated, but NX does not show material "
            f"'{material}' with Material Table entry afterwards."
        )

    def preferences_read_back_failed(self, detail: str) -> str:
        return f"Sheet Metal Preferences could not be read back: {detail}"

    def preference_row_not_in_table(self, material: str) -> str:
        return (
            f"Material '{material}' is not in the sheet metal material standards file, so Sheet Metal Preferences "
            "cannot be set to it."
        )

    def preferences_set(self, row: str, row_description: str) -> str:
        return f"Sheet Metal Preferences set to '{row}' ({row_description})."

    # ---- Configuration (shown at start-up) ----

    def config_unavailable(self, detail: str) -> str:
        return f"Sheet metal configuration could not be loaded: {detail}"

    def config_directory_not_found(self, settings_file: str, attempted: Iterable[str]) -> str:
        return (
            f"Could not locate the sheet metal root directory (no {settings_file} under UGII_USER_DIR, UGII_GROUP_DIR "
            f"or UGII_SITE_DIR). Tried: {'; '.join(attempted)}."
        )

    def material_table_not_set(self, settings_path: str) -> str:
        return (
            f"materialTable is not set in {settings_path}. Set it to the file name of the sheet metal material "
            "standards file in the sheet metal root."
        )

    def material_table_file_missing(self, settings_path: str, path: str) -> str:
        return f"The sheet metal material standards file set in {settings_path} (materialTable) does not exist: {path}"

    def settings_invalid_json(self, path: str, detail: str) -> str:
        return f"Sheet metal settings at '{path}' are not valid JSON: {detail}"

    def settings_empty(self, path: str) -> str:
        return f"Sheet metal settings at '{path}' are empty."

    def bead_settings_invalid_json(self, path: str, detail: str) -> str:
        return f"Bead settings at '{path}' are not valid JSON: {detail}"

    def bead_settings_empty(self, path: str) -> str:
        return f"Bead settings at '{path}' are empty."

    def bead_settings_invalid(self, path: str, detail: str) -> str:
        return f"Bead settings at '{path}': {detail}"

    bead_tolerance_not_positive = "geometryMatchTolerance must be greater than zero."

    def mapping_feature_unknown(self, feature: str | None, known: Iterable[str]) -> str:
        return f"parameterMapping feature '{feature or ''}' is not a bead parameter. Known: {', '.join(known)}."

    def mapping_column_unknown(self, column: str, feature: str, known: Iterable[str]) -> str:
        return (
            f"parameterMapping column '{column}' for {feature} is not a SPEC workbook column. "
            f"Known: {', '.join(known)}."
        )

    def mapping_duplicated(self, features: Iterable[str]) -> str:
        return f"parameterMapping lists {', '.join(features)} more than once."

    def mapping_missing(self, parameters: Iterable[str]) -> str:
        return f"parameterMapping must map {', '.join(parameters)}: the tool writes it into every bead."

    def material_table_not_found(self, path: str) -> str:
        return f"Sheet metal material standards file not found: {path}"

    def material_table_invalid(self, path: str, detail: str) -> str:
        return f"Sheet metal material standards file '{path}': {detail}"

    # The standards file's own format errors (the line is named; the file by material_table_invalid).

    def table_no_block(self, table_start: str) -> str:
        return f"no {table_start} block was found."

    def table_no_units(self, units_keyword: str, table_start: str, line: int, supported_units: str) -> str:
        return (
            f"no {units_keyword} line was found before {table_start} (line {line}). "
            f"Add '{units_keyword},{supported_units}'."
        )

    def table_units_unsupported(self, units_keyword: str, units: str, supported_units: str) -> str:
        return (
            f"{units_keyword} is '{units}', but only {supported_units} is supported: bead SPECs and thickness checks "
            "are in inches."
        )

    def table_row_width(self, line: int, values: int, header_line: int, columns: int) -> str:
        return f"line {line} has {values} values but the header row (line {header_line}) has {columns} columns."

    def table_duplicate_name(self, line: int, name: str, first_line: int, unique_column: str) -> str:
        return (
            f"line {line}: '{name}' is already used on line {first_line}. Values in the unique column "
            f"'{unique_column}' must not repeat."
        )

    def table_no_end(self, table_start: str, table_end: str) -> str:
        return f"the {table_start} block has no {table_end} line."

    def table_header_empty(self, line: int) -> str:
        return f"line {line}: the header row is empty."

    def table_column_unnamed(self, line: int, column: int) -> str:
        return f"line {line}: column {column} has no name."

    def table_column_repeated(self, line: int, column: str) -> str:
        return f"line {line}: column '{column}' appears more than once."

    def table_property_row_width(self, line: int, values: int, columns: int) -> str:
        return f"line {line}: the property row has {values} values but the header row has {columns} columns."

    def table_property_unknown(self, line: int, column: str, prop: str, known: Iterable[str]) -> str:
        return f"line {line}: column '{column}' has property '{prop}'. Expected one of: {', '.join(known)}."

    def table_unique_count(self, line: int, unique_property: str, count: int) -> str:
        return f"line {line}: exactly one column must be '{unique_property}', but {count} are."

    def table_columns_missing(self, line: int, missing: Iterable[str]) -> str:
        return f"line {line}: required column(s) missing: {', '.join(missing)}."

    def table_unique_empty(self, line: int, unique_column: str) -> str:
        return f"line {line}: the unique column '{unique_column}' is empty."

    def table_value_empty(self, line: int, name: str, column: str) -> str:
        return f"line {line} ('{name}'): {column} is empty."

    def table_thickness_invalid(self, line: int, name: str, column: str, text: str) -> str:
        return f"line {line} ('{name}'): {column} '{text}' is not a positive number."

    def table_ends_before(self, table_start: str, expected: str) -> str:
        return f"the {table_start} block ends before {expected}."

    table_expected_header_row = "the header row"
    table_expected_property_row = "the column property row (unique/show/hide/attribute)"

    # ---- SPEC workbooks and Standard folders ----

    standards_not_from_file = (
        "The sheet metal material table was not read from a file, so its Standard folders cannot be found."
    )

    def standard_not_a_folder_name(self, standard: str) -> str:
        return f"Standard '{standard}' cannot be used as a folder name."

    def bead_spec_matches_several_workbooks(self, spec: str, count: int, folder: str, files: Iterable[str]) -> str:
        return (
            f"Bead SPEC '{spec}' matches {count} workbooks in '{folder}' "
            f"({', '.join(files)}). Exactly one file name may contain that SPEC name."
        )

    workbook_header_row_empty = "SPEC workbook header row has no columns."

    def workbook_unreadable(self, detail: str) -> str:
        return f"Not a readable .xlsx workbook: {detail}"

    workbook_no_workbook_part = "The workbook has no workbook part."
    workbook_no_worksheets = "The workbook has no worksheets."

    def workbook_cell_not_a_number(self, cell: str, text: str) -> str:
        return f"SPEC workbook cell {cell} should be a number but reads '{text}'."

    workbook_no_header_row = "Could not find the header row (a cell reading 'SPEC') in the workbook."

    def workbook_column_missing(self, column: str, aliases: Iterable[str]) -> str:
        return (
            f"SPEC workbook is missing the '{column}' column (looked for header text matching: {', '.join(aliases)})."
        )

    # ---- The bead tool (what the dialog shows; moved here from the C# presenter so every bead tool says it alike) ----

    def standards_unlistable(self, detail: str) -> str:
        return f"Could not list the Standards in the sheet metal material standards file: {detail}"

    def not_exactly_one_body(self, count: int) -> str:
        return f"This part has {count} sheet metal bodies; the Bead tool needs exactly one."

    def body_unreadable(self, body: str) -> str:
        return f"Could not read the sheet metal of body '{body}'."

    def preferences_unusable(self, problem: str) -> str:
        return (
            f"This part's Sheet Metal Preferences cannot be used: {problem}. "
            "Set them to a Material Table entry from the sheet metal material standards file in NX, then reopen the "
            "Bead dialog."
        )

    def no_spec_allowed_under_standard(self, standard: str, reason: str) -> str:
        return f"No bead SPEC is allowed under Standard '{standard}': {reason}."

    def cannot_be_one_bead(self, target: str, error: str | None) -> str:
        return f"'{target}' cannot be one bead: {error or ''}."

    def with_alternatives(self, blocking_message: str | None, alternatives: Iterable[str]) -> str:
        names = list(alternatives)
        tail = (
            f" Valid alternatives under this bead SPEC: {', '.join(names)}."
            if names
            else " No SPECs under this bead SPEC currently validate against this sheet metal."
        )
        return (blocking_message or "") + tail

    _refusal_fix = (
        "Select those beads so Apply rebuilds them to the chosen SPEC, or change the Sheet Metal Preferences in NX."
    )

    def uncovered_item(self, spec: str, bead: str, coverage: str) -> str:
        return f"SPEC {spec} (bead '{bead}') covers {coverage}"

    def thickness_not_covered(self, thickness: str, items: Iterable[str]) -> str:
        return (
            f"This sheet is {thickness} in thick, which the bead(s) already on this body are not driven for: "
            f"{', '.join(items)}. {self._refusal_fix}"
        )

    def refusing_item(self, bead: str, spec: str) -> str:
        return f"'{bead}' (SPEC {spec})"

    def grade_refused_by_beads(self, row: str, grade: str, thickness: str, items: Iterable[str]) -> str:
        return (
            f"Sheet metal material '{row}' (grade '{grade}') from the Sheet Metal Preferences is not allowed at "
            f"{thickness} in by the bead(s) already on this body: {', '.join(items)}. {self._refusal_fix}"
        )

    choose_spec_first = "Choose a SPEC from the bead options first."
    nothing_selected = (
        "Nothing is selected to build. Select curves or a sketch for new beads, or existing bead feature(s)."
    )
    build_failed = "One or more beads failed — see the listing window for details. No changes were committed."

    def requirement_write_failed(self, detail: str | None) -> str:
        return (
            "The beads were built, but their requirement could not be written, so nothing was committed: "
            f"{detail or ''}"
        )

    def no_specs(self, bead_spec: str, reason: str) -> str:
        return f"Bead SPEC '{bead_spec}' offers no SPECs: {reason}."

    def no_row_matches(self, bead_spec: str, thickness: str, grade: str | None, covered: Iterable[str]) -> str:
        return (
            f"No row of bead SPEC '{bead_spec}' matches the Sheet Metal Preferences: thickness {thickness} in, grade "
            f"'{grade or ''}'.{_PARA}Thicknesses this bead SPEC covers: {', '.join(covered)} in."
            f"{_PARA}Change the Sheet Metal Preferences in NX, or choose another bead SPEC."
        )

    applied = "Bead(s) created/updated."

    def applied_with_notes(self, notes: Iterable[str]) -> str:
        return f"{self.applied}{_PARA}{chr(10).join(notes)}"

    no_bead_spec = "(none)"
    no_bead_spec_selected = "no bead SPEC is selected"

    def no_workbook_named(self, folder: str, bead_spec: str) -> str:
        return f"no workbook in '{folder}' is named for bead SPEC '{bead_spec}'"

    def workbook_lists_no_specs(self, bead_spec: str) -> str:
        return f"bead SPEC workbook:'{bead_spec}' lists no SPECs"

    def workbook_unreadable_reason(self, detail: str) -> str:
        return f"its bead SPEC workbook could not be read: {detail}"

    # Mode.
    mode_begin = "Select curves or a sketch, or bead feature(s), to begin."
    mode_choose_spec = "Choose a SPEC from the bead options."

    def mode_new(self, chains: int) -> str:
        return f"New bead(s): {chains} chain(s) selected."

    def mode_editing(self, beads: int) -> str:
        return f"Editing existing bead(s): {beads}."

    def mode_mixed(self, new: int, existing: int) -> str:
        return f"Mixed: {new} new, {existing} existing bead(s) to update."

    # The selection list.
    def selection_line(self, label: str, status: str) -> str:
        return f"{label} — {status}"

    def selection_error(self, error: str) -> str:
        return f"ERROR: {error}"

    def selection_unstamped_matched(self, spec: str) -> str:
        return f"Not created by this tool; geometry matches SPEC {spec} — Apply records it"

    def selection_unstamped_unmatched(self, reason: str | None) -> str:
        return f"WARNING: not created by this tool, {reason or ''} — choose a SPEC and Apply to update it"

    def selection_editing_via_pattern(self, spec: str | None) -> str:
        return f"Editing SPEC {spec or ''} (via pattern)"

    def selection_editing(self, spec: str | None) -> str:
        return f"Editing SPEC {spec or ''}"

    selection_new_bead = "New bead"
    selection_via_pattern_suffix = " (via pattern)"

    def selection_unnamed(self, kind: str, journal_id: str) -> str:
        return f"{kind} {journal_id}"

    def selection_more_curves(self, count: int) -> str:
        return f" +{count} curve(s)"

    def selection_from_sketch(self, label: str, sketch: str) -> str:
        return f"{label} (from {sketch})"

    def selection_disambiguated(self, label: str, journal_id: str) -> str:
        return f"{label} [{journal_id}]"

    selection_not_among_body_beads = "it is not among the beads read from this part's body"

    def selection_mixed_with_new_curves(self, bead: str, new_curves: int) -> str:
        return (
            f"mixes curves of bead {bead} with {new_curves} curve(s) of no bead — select one bead's curves, or only "
            "new curves"
        )

    def selection_spans_beads(self, beads: Iterable[str]) -> str:
        return f"its curves belong to beads {', '.join(beads)} — select one bead's curves"

    # The preferences label.
    def preference_summary(self, state: str, thickness: str, grade: str | None, standard: str | None) -> str:
        tail = "" if grade is None else f", grade {grade} (Standard {standard or ''})"
        return f"Sheet Metal Preferences: {state}, t {thickness}{tail}"

    def preference_summary_unreadable(self, state: str) -> str:
        return f"Sheet Metal Preferences: {state}"

    def preference_summary_not_usable(self, summary: str, problem: str) -> str:
        return f"{summary}\nNot usable: {problem} — fix them in NX"

    # Advisories (do not stop Apply).
    def warning_body_has_no_material(self, body: str, physical_material: str) -> str:
        return (
            f"Body '{body}' has no physical material; the Sheet Metal Preferences name '{physical_material}'. This "
            "dialog does not assign materials."
        )

    def warning_body_material_differs(self, body: str, body_material: str, physical_material: str) -> str:
        return (
            f"Body '{body}' is '{body_material}', but the Sheet Metal Preferences name '{physical_material}'. This "
            "dialog does not change the material."
        )

    def warning_unmatched_selected(self, count: int) -> str:
        return (
            f"{count} selected bead(s) were not created by this tool and could not be matched to a SPEC (see the "
            "selection list). Choose a SPEC and Apply to update them to it; the SPEC is then recorded on the bead."
        )

    def warning_other_standards(self, standards: Iterable[str], standard: str) -> str:
        return (
            f"Selected bead(s) were built to Standard {', '.join(standards)}, not '{standard}'. Apply updates them to "
            "the chosen SPEC in this Standard."
        )

    def warning_unresolved_bead_specs(self, specs: Iterable[str]) -> str:
        return (
            f"SPEC {', '.join(specs)} could not be matched to one of this dialog's bead SPECs — the bead was stamped "
            "before the bead SPEC was recorded, and its SPEC id is no longer in exactly one workbook. Choose the bead "
            "SPEC yourself; Apply then records it."
        )

    def warning_unknown_beads(self, beads: Iterable[str]) -> str:
        return (
            f"The SPEC of bead(s) {', '.join(beads)} on this body is not known, so they do not restrict the material. "
            "Select them and Apply to record a SPEC."
        )

    def warning_requirements_unreadable(self, errors: str) -> str:
        return f"This part's bead requirements could not be read: {errors}"

    def warning_requirement_refuses(self, requirement: str, grade: str, sheet: str, allowed: str) -> str:
        return (
            f"{requirement} does not allow '{grade}' at {sheet} in from the Sheet Metal Preferences (allowed: "
            f"{allowed}); its NX requirement check fails until its beads are selected and re-applied with a SPEC that "
            "allows it."
        )

    def warning_requirement_rebuild(self, requirement: str, built: str, sheet: str) -> str:
        return (
            f"Beads of {requirement} were built at {built} in, but the sheet is now {sheet} in — select them and Apply "
            "to rebuild their geometry at the current sheet."
        )

    def warning_requirement_workbook_changed(self, requirement: str, recorded: str) -> str:
        return (
            f"{requirement}: its workbook has changed since the beads were applied; the part still records "
            f"{recorded}. Re-apply the beads to bring the requirement up to date."
        )

    # ---- The Material Assignment dialog's sheet metal support ----

    def material_with_row(self, material: str, row: str) -> str:
        return f"{material} [{row}]"

    def conflict(self, name: str, lines: Iterable[str]) -> str:
        return (
            f"{name} cannot be assigned to sheet metal bodies in this part.{_PARA}"
            "Sheet Metal Preferences belong to the part, so every sheet metal body in it must have the same material "
            f"and preferences row, and these already have a different one:\n{chr(10).join(lines)}{_PARA}"
            f"Nothing was assigned. Assign {name} to all of the sheet metal bodies together, or leave the sheet metal "
            "bodies out of the selection."
        )

    def conflict_line(self, body: str, material: str, is_staged: bool) -> str:
        return f"  [{body}] {material}{' (pending)' if is_staged else ''}"

    def body_line(self, body: str, message: str) -> str:
        return f"  [{body}] {message}"

    def sync_hint(self, row: str, row_description: str, action: str) -> str:
        """`action` is a `SyncAction` value: "none", "selected" or "staged"."""
        match action:
            case "staged":
                return f"'{row}' ({row_description}) was added to pending for the body. Apply to sync them."
            case "selected":
                return (
                    f"The body was added to the selection. Pick '{row}' ({row_description}) in the material list and "
                    "Apply to sync them."
                )
            case _:
                return (
                    f"Select the bodies, pick '{row}' ({row_description}) in the material list and Apply to sync them."
                )

    def set_preferences_only(self, row: str, row_description: str) -> str:
        return (
            f"This part has no solid body, so only its Sheet Metal Preferences will be set to '{row}' "
            f"({row_description}).{_PARA}Continue?"
        )

    grade_not_set = "(not set)"

    def grade_material_mismatch(self, name: str) -> str:
        return f"{name} (material mismatch)"

    def grade_thickness_mismatch(self, name: str) -> str:
        return f"{name} (thickness mismatch)"

    def grade_not_in_table(self, name: str) -> str:
        return f"{name} (not in standards file)"


SheetMetalText.DEFAULT = SheetMetalText()
