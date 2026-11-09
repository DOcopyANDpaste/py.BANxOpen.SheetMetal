"""The material half of the work part's NX Sheet Metal Preferences: read, set, and kept in step with an assignment.

NX keeps one set per part; a body only decides whether they apply to it. Uses the NX 2312+ SheetMetalPreferencesManager
and Builder (the older PartSheetmetal material calls are deprecated). No undo handling here: callers own the mark.
"""

# NX 2412's Python stubs declare instance methods without `self`, type enum members as int and give collections
# no __iter__; these rules would only report those stub defects here. Everything else is still checked.
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportReturnType=false
# pyright: reportCallIssue=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false

from __future__ import annotations

import logging
from dataclasses import dataclass

import NXOpen
import NXOpen.Preferences

from banxopen.foundation import BodyId, OperationResult
from banxopen.foundation.nx import BodyResolver, NxSessionContext, nx_error_code, nx_error_detail
from banxopen.materials import MaterialsCodes, SideEffectInstruction
from banxopen.sheetmetal import (
    SheetMetalCodes,
    SheetMetalMaterialRow,
    SheetMetalMaterialTable,
    SheetMetalPartPreference,
    SheetMetalPreferenceRead,
    SheetMetalText,
    SyncSheetMetalPreferenceEffectRule,
)

_log = logging.getLogger(__name__)


def _material_table_entry() -> object:
    return NXOpen.Preferences.SheetMetalPreferencesBuilder.ParameterEntryTypes.MaterialTable


class NxSheetMetalPreferences:
    """Reads and sets the preferences. Also the `SheetMetalPreferenceReader` the constraints use."""

    def __init__(
        self, context: NxSessionContext, table: SheetMetalMaterialTable, text: SheetMetalText | None = None
    ) -> None:
        self._context = context
        self._table = table
        self._text = text or SheetMetalText.DEFAULT

    def read_for(self, body_id: BodyId) -> SheetMetalPreferenceRead:
        try:
            body = next((b for b in self._context.work_part.Bodies if BodyResolver.body_id(b) == body_id), None)
        except NXOpen.NXException as ex:
            _log.error("Could not scan the work part's bodies for '%s': %s", body_id, nx_error_detail(ex))
            return SheetMetalPreferenceRead.unreadable(nx_error_detail(ex))
        if body is None:
            return SheetMetalPreferenceRead.unreadable(self._text.preferences_body_gone(str(body_id)))
        return self.read_for_body(body)

    def read_for_body(self, body: NXOpen.Body) -> SheetMetalPreferenceRead:
        if not self.is_sheet_metal(body):
            return SheetMetalPreferenceRead.NOT_SHEET_METAL
        # A thickness NX will not report is not a failure: the preferences are still readable.
        thickness = self.body_thickness(body)
        read = self.read_for_part()
        return SheetMetalPreferenceRead.of(read.preference, thickness) if read.preference is not None else read

    def read_for_part(self) -> SheetMetalPreferenceRead:
        """The work part's preferences with no body involved: they belong to the part."""
        try:
            preferences = self._context.work_part.Preferences.SheetMetalPreferences
            # VERIFY: that the thickness expression is in the units GetBodyThickness reports (part units).
            thickness = preferences.GetMaterialThickness()
            if thickness is None:
                return SheetMetalPreferenceRead.unreadable(self._text.preferences_no_thickness)
            name = preferences.GetMaterialName() or None
            name = name if name and name.strip() else None
            preference = SheetMetalPartPreference(
                name,
                preferences.GetParameterEntryType() == _material_table_entry(),
                float(thickness.Value),
                self.count_sheet_metal_bodies(),
                self._table.find(name),
            )
            return SheetMetalPreferenceRead.of(preference)
        except NXOpen.NXException as ex:
            _log.error("Reading Sheet Metal Preferences failed: %s", nx_error_detail(ex))
            return SheetMetalPreferenceRead.unreadable(nx_error_detail(ex))

    def set_material(self, row: SheetMetalMaterialRow) -> OperationResult[None]:
        """Sets the preferences to the row, switching Parameter Entry and the bend definition to Material Table (NX
        ignores the material in any other mode), in the order NX's dialog journals it. NX fills thickness, bend radius,
        reliefs and neutral factor from the row. Read back afterwards: that stops a caller asking again for a change NX
        quietly ignored. NX's own GetMaterialNames is not used: it fails with internal error 11."""
        material = row.name
        preferences = self._context.work_part.Preferences.SheetMetalPreferences
        builder = None
        try:
            builder = preferences.CreateSheetMetalPreferencesBuilder()
            builder_type = NXOpen.Preferences.SheetMetalPreferencesBuilder
            builder.ParameterEntryType = builder_type.ParameterEntryTypes.MaterialTable
            builder.SetBendDefinitionMethod(builder_type.BendDefinitionMethodOptions.MaterialTable)
            builder.SetMaterial(material)
            builder.Commit()
        except NXOpen.NXException as ex:
            _log.error("Setting Sheet Metal Preferences material to '%s' failed: %s", material, nx_error_detail(ex))
            return OperationResult.fail(
                nx_error_code(ex), self._text.preferences_set_failed(material, nx_error_detail(ex))
            )
        finally:
            if builder is not None:
                builder.Destroy()

        try:
            applied = (
                preferences.GetParameterEntryType() == _material_table_entry()
                and (preferences.GetMaterialName() or "").casefold() == material.casefold()
            )
        except NXOpen.NXException as ex:
            _log.error("Reading back Sheet Metal Preferences failed: %s", nx_error_detail(ex))
            return OperationResult.fail(nx_error_code(ex), self._text.preferences_read_back_failed(nx_error_detail(ex)))
        if not applied:
            _log.error(
                "Sheet Metal Preferences committed, but do not read back as '%s' with Material Table entry.", material
            )
            return OperationResult.fail(
                SheetMetalCodes.PREFERENCE_SYNC_NOT_APPLIED, self._text.preferences_not_applied(material)
            )
        _log.info("Sheet Metal Preferences material set to '%s' (Material Table entry).", material)
        return OperationResult.success()

    def is_sheet_metal(self, body: NXOpen.Body) -> bool:
        try:
            return bool(self._context.work_part.Features.SheetmetalManager.IsSheetmetalBody(body))
        except NXOpen.NXException:
            return False  # not answerable: treated as not sheet metal, as the part material service does

    def body_thickness(self, body: NXOpen.Body) -> float | None:
        try:
            return float(self._context.work_part.Features.SheetmetalManager.GetBodyThickness(body))
        except NXOpen.NXException as ex:
            _log.warning("GetBodyThickness failed for '%s': %s", body.Name, nx_error_detail(ex))
            return None

    def sheet_metal_bodies(self) -> list[NXOpen.Body]:
        try:
            return [b for b in self._context.work_part.Bodies if self.is_sheet_metal(b)]
        except NXOpen.NXException as ex:
            _log.error("Could not list the part's bodies: %s", nx_error_detail(ex))
            return []

    def count_sheet_metal_bodies(self) -> int:
        return len(self.sheet_metal_bodies())


@dataclass(frozen=True, slots=True)
class BodyProfile:
    """The sheet metal facts a SPEC is validated against, for one body."""

    body_id: BodyId
    name: str
    thickness: float
    physical_material: str | None
    preference: SheetMetalPartPreference


def read_body_profile(
    context: NxSessionContext, preferences: NxSheetMetalPreferences, body: NXOpen.Body, text: SheetMetalText
) -> OperationResult[BodyProfile]:
    """Thickness, physical material and the part's preferences. Unreadable preferences fail the read, as the material
    engine refuses an assignment it cannot check them for."""
    name = body.Name or body.JournalIdentifier
    if not preferences.is_sheet_metal(body):
        return OperationResult.fail(SheetMetalCodes.NOT_SHEET_METAL, text.profile_not_sheet_metal(name))
    thickness = preferences.body_thickness(body)
    if thickness is None:
        return OperationResult.fail(SheetMetalCodes.THICKNESS_READ_FAILED, text.thickness_unreadable)
    read = preferences.read_for_part()
    if read.preference is None:
        return OperationResult.fail(
            SheetMetalCodes.PREFERENCES_UNREADABLE,
            text.profile_preferences_unreadable(read.read_error or text.no_preferences_returned),
        )
    material: str | None = None
    try:
        physical = context.work_part.MaterialManager.PhysicalMaterials.AskMaterialOfObject(body)
        material = physical.Name if physical is not None else None
    except NXOpen.NXException:
        pass  # a body with no physical material throws rather than returning None: the normal "unassigned" path
    return OperationResult.success(
        BodyProfile(BodyResolver.body_id(body), name, thickness, material or None, read.preference)
    )


class PreferenceSyncExecutor:
    """Executes SYNC_SHEETMETAL_PREFERENCE_MATERIAL inside the part material service's per-body undo mark, so one
    Ctrl+Z takes back the material and the preferences together. The body is not used: the preferences belong to the
    part."""

    instruction_type = SyncSheetMetalPreferenceEffectRule.INSTRUCTION_TYPE

    def __init__(
        self, preferences: NxSheetMetalPreferences, table: SheetMetalMaterialTable, text: SheetMetalText
    ) -> None:
        self._preferences = preferences
        self._table = table
        self._text = text

    def execute(self, instruction: SideEffectInstruction, body: NXOpen.Body) -> OperationResult[None]:
        if instruction.instruction_type != self.instruction_type:
            return OperationResult.fail(
                MaterialsCodes.UNSUPPORTED_INSTRUCTION,
                f"PreferenceSyncExecutor cannot execute instruction type '{instruction.instruction_type}'.",
            )
        name = instruction.data.get(SyncSheetMetalPreferenceEffectRule.MATERIAL_NAME_DATA_KEY)
        if not isinstance(name, str) or not name.strip():
            return OperationResult.fail(
                MaterialsCodes.MALFORMED_INSTRUCTION,
                f"'{SyncSheetMetalPreferenceEffectRule.MATERIAL_NAME_DATA_KEY}' is missing or not a non-empty string.",
            )
        row = self._table.find(name)
        if row is None:  # the rule read the row from this same table: the instruction came from elsewhere
            return OperationResult.fail(
                SheetMetalCodes.PREFERENCE_MATERIAL_NOT_IN_TABLE, self._text.preference_row_not_in_table(name)
            )
        return self._preferences.set_material(row)
