"""What the Material Assignment dialog decides about sheet metal, moved out of the dialog so a rule change needs no tool
change: the conflict between bodies sharing one set of preferences, how a body stands against them, the open message
and the SheetMetal Grade column."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from banxopen.foundation import BodyId, BodyInfo, BodyKind
from banxopen.materials import BodyMaterialAssignment
from banxopen.sheetmetal.core.common.config import check_preference
from banxopen.sheetmetal.core.materials.table import NO_FILTER, SheetMetalMaterialTable, SheetMetalRowFilter
from banxopen.sheetmetal.model.table import (
    SheetMetalMaterialRow,
    SheetMetalPartPreference,
    SheetMetalPreferenceCheckResult,
    SheetMetalPreferenceStatus,
)
from banxopen.sheetmetal.text import SheetMetalText


class SyncAction(Enum):
    """What the dialog did on open about a sheet metal body whose material disagrees with the preferences."""

    NONE = "none"
    """The user selects the bodies and picks the row."""
    SELECTED = "selected"
    """The one body was put in the selection; the user picks the row."""
    STAGED = "staged"
    """The preferences row was added to pending for the one body; the user only applies."""


@dataclass(frozen=True, slots=True)
class PendingSheetMetal:
    """One pending entry of the dialog, as far as sheet metal cares: an assignment (`material_name`, `row`) or a
    removal (`material_name` None) of `body_ids`."""

    body_ids: tuple[BodyId, ...]
    material_name: str | None
    row: SheetMetalMaterialRow | None = None


@dataclass(frozen=True, slots=True)
class BodyCheck:
    body: BodyInfo
    check: SheetMetalPreferenceCheckResult


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.casefold() == b.casefold()


class SheetMetalAssignmentSupport:
    def __init__(self, table: SheetMetalMaterialTable, text: SheetMetalText | None = None) -> None:
        self._table = table
        self._text = text or SheetMetalText.DEFAULT

    @property
    def table(self) -> SheetMetalMaterialTable:
        return self._table

    @staticmethod
    def is_solid(body: BodyInfo) -> bool:
        """Sheet metal bodies are solids in NX, so solid-body checks include them."""
        return body.kind in (BodyKind.SOLID, BodyKind.SHEET_METAL)

    def rows_for_library(self, material_names: Iterable[str]) -> tuple[SheetMetalMaterialRow, ...]:
        """The standards-file rows made of a sheet metal library's materials, in file order."""
        names = {n.casefold() for n in material_names}
        return tuple(r for r in self._table.rows if r.physical_material_name.casefold() in names)

    @staticmethod
    def default_filter(
        preference: SheetMetalPartPreference | None, library_rows: Sequence[SheetMetalMaterialRow]
    ) -> SheetMetalRowFilter:
        """Opens on the part's current preferences row when the library lists it, else no filter."""
        row = preference.row if preference is not None else None
        if row is not None and any(_same(r.name, row.name) for r in library_rows):
            return SheetMetalRowFilter.for_row(row)
        return NO_FILTER

    def check_body(
        self, body_material: str | None, body_thickness: float | None, preference: SheetMetalPartPreference
    ) -> SheetMetalPreferenceCheckResult:
        """The preference check, with this dialog's rule on top: a body with no material is out of sync with
        preferences that name a row, since the dialog offers to assign it. An unknown thickness counts as matching,
        as in the row gate."""
        row = preference.row
        if body_material is None and preference.is_material_table_entry and row is not None:
            return SheetMetalPreferenceCheckResult(
                SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC,
                self._text.preference_body_has_no_material(row.name, row.physical_material_name),
            )
        thickness = body_thickness if body_thickness is not None else preference.thickness
        return check_preference(body_material, thickness, preference, self._text)

    @staticmethod
    def suggested_sync(
        sheet_metal_bodies: Sequence[BodyInfo], not_in_sync: Sequence[BodyCheck], preference: SheetMetalPartPreference
    ) -> tuple[BodyInfo, SheetMetalMaterialRow] | None:
        """With exactly one sheet metal body, out of sync on material, the row to stage for it (or, when the row's
        material is not in the open library, the body to select). None otherwise."""
        if (
            len(sheet_metal_bodies) == 1
            and len(not_in_sync) == 1
            and not_in_sync[0].check.status is SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC
            and preference.row is not None
        ):
            return sheet_metal_bodies[0], preference.row
        return None

    def open_message(
        self, preference: SheetMetalPartPreference, not_in_sync: Sequence[BodyCheck], action: SyncAction
    ) -> str:
        """Shown on open when sheet metal bodies are not in sync. A part-level problem is said once; body-level ones
        are listed per body. Only a material mismatch is something this dialog fixes."""
        text = self._text
        sections: list[str] = []
        not_in_table = next(
            (c for c in not_in_sync if c.check.status is SheetMetalPreferenceStatus.MATERIAL_NOT_IN_TABLE), None
        )
        if not_in_table is not None:
            sections.append(not_in_table.check.message or "")
        body_lines = [
            text.body_line(c.body.name, c.check.message or "")
            for c in not_in_sync
            if c.check.status
            in (SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC, SheetMetalPreferenceStatus.THICKNESS_MISMATCH)
        ]
        if body_lines:
            sections.append("\n".join(body_lines))
        row = preference.row
        if row is not None and any(
            c.check.status is SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC for c in not_in_sync
        ):
            sections.append(text.sync_hint(row.name, row.describe(), action.value))
        return "\n\n".join(sections)

    def grade_for_body(
        self, check: SheetMetalPreferenceCheckResult | None, preference: SheetMetalPartPreference | None
    ) -> str:
        """The SheetMetal Grade column for a body: the preferences row's name, marked with how the body stands. Empty
        for a body that is not sheet metal."""
        if check is None or preference is None:
            return ""
        text = self._text
        if check.status is SheetMetalPreferenceStatus.MATERIAL_NOT_SET:
            return text.grade_not_set
        name = preference.row.name if preference.row is not None else preference.material_name or ""
        match check.status:
            case SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC:
                return text.grade_material_mismatch(name)
            case SheetMetalPreferenceStatus.THICKNESS_MISMATCH:
                return text.grade_thickness_mismatch(name)
            case SheetMetalPreferenceStatus.MATERIAL_NOT_IN_TABLE:
                return text.grade_not_in_table(name)
            case _:
                return name

    @staticmethod
    def grade_for_material(material_label: str, preference: SheetMetalPartPreference | None) -> str:
        """The row name on the material line the preferences row is made of; empty on any other."""
        row = preference.row if preference is not None else None
        return row.name if row is not None and _same(material_label, row.physical_material_name) else ""

    def label(self, material_name: str, row: SheetMetalMaterialRow | None) -> str:
        return material_name if row is None else self._text.material_with_row(material_name, row.name)

    def find_conflict(
        self,
        material_name: str,
        row: SheetMetalMaterialRow | None,
        targets: Sequence[BodyInfo],
        all_bodies: Sequence[BodyInfo],
        current_assignments: Mapping[BodyId, BodyMaterialAssignment],
        pending: Sequence[PendingSheetMetal],
        part_row: Callable[[], SheetMetalMaterialRow | None],
    ) -> str | None:
        """NX keeps one set of preferences per part, so every sheet metal body in it must share a material and a row:
        why `material_name` (as `row`) cannot go onto `targets`, or None.

        Only sheet metal bodies outside `targets` count. A body claimed by a pending assignment counts as that entry's
        material and row, one pending removal as having none. An assigned body counts as the part's preferences row
        when that row is made of its material. `part_row` is an NX query, called at most once and only when needed.
        """
        if not any(b.kind is BodyKind.SHEET_METAL for b in targets):
            return None
        target_ids = {b.id for b in targets}
        staged: dict[BodyId, PendingSheetMetal] = {}
        removed: set[BodyId] = set()
        for entry in pending:
            for body_id in entry.body_ids:
                if entry.material_name is None:
                    removed.add(body_id)
                else:
                    staged[body_id] = entry

        current_row: SheetMetalMaterialRow | None = None
        row_read = False
        conflicting: list[tuple[BodyInfo, str, bool]] = []
        for body in all_bodies:
            if body.kind is not BodyKind.SHEET_METAL or body.id in target_ids or body.id in removed:
                continue
            entry = staged.get(body.id)
            if entry is not None:
                other_material, other_row = entry.material_name or "", entry.row
            else:
                assignment = current_assignments.get(body.id)
                if assignment is None or assignment.material_name is None:
                    continue
                other_material = assignment.material_name
                if not row_read:
                    current_row, row_read = part_row(), True
                other_row = (
                    current_row
                    if current_row is not None and _same(current_row.physical_material_name, other_material)
                    else None
                )
            # An unknown row on either side cannot be told apart, so only the material is compared then.
            same_row = row is None or other_row is None or _same(other_row.name, row.name)
            if _same(other_material, material_name) and same_row:
                continue
            conflicting.append((body, self.label(other_material, other_row), entry is not None))

        if not conflicting:
            return None
        text = self._text
        return text.conflict(
            self.label(material_name, row), (text.conflict_line(b.name, m, s) for b, m, s in conflicting)
        )
