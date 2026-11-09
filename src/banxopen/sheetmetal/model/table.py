"""Rows of NX's sheet metal material standards file, and the part's Sheet Metal Preferences."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Final

from banxopen.sheetmetal.model.thickness import format_thickness


class SheetMetalMaterialColumns:
    """The MATERIAL_TABLE columns the tool reads by name. Every other column is kept verbatim in
    `SheetMetalMaterialRow.columns`. Names compare ignoring case."""

    STANDARD: Final = "Standard"
    """The Standard a row belongs to; names the Standard's folder beside the table."""
    SHEET_METAL_STANDARD: Final = "SheetMetal_Standard"
    """Written to the part as an attribute by NX. Not used by the tool."""
    PHYSICAL_MATERIAL_NAME: Final = "PHYSICAL_MATERIAL_NAME"
    """The NX keyword linking a row to the NX physical material library."""
    SHEET_METAL_MATERIAL: Final = "SheetMetal_Material"
    """The grade: matched against the "Allowed Material" headers of the bead SPEC workbooks."""
    THICKNESS: Final = "THICKNESS"
    BEND_RADIUS: Final = "BEND_RADIUS"
    REQUIRED: Final = (STANDARD, PHYSICAL_MATERIAL_NAME, SHEET_METAL_MATERIAL, THICKNESS)
    """Columns every row must fill: a row without them can be neither picked nor checked."""


@dataclass(frozen=True, slots=True)
class SheetMetalMaterialRow:
    """One row of the MATERIAL_TABLE: one material the Sheet Metal Preferences can be set to. A physical material
    usually has several rows (thicknesses, bend radii), so the row is what gets picked.

    `name` is the table's unique column, the value passed to `SetMaterial` and reported back by NX. `thickness` is in
    inches (the parser refuses METRIC). `bend_radius` is kept as written: a number or a pointer (`@NAME`).
    `columns` holds every cell by upper-cased column name; read it with `value_of`.
    """

    name: str
    standard: str
    sheet_metal_standard: str
    physical_material_name: str
    grade: str
    thickness: float
    bend_radius: str
    line_number: int = 0
    columns: Mapping[str, str] = field(default_factory=dict[str, str], compare=False)

    def __hash__(self) -> int:
        return hash((self.name, self.line_number))

    def value_of(self, column: str) -> str | None:
        """The cell under `column` (any case), or None when the table has no such column."""
        return self.columns.get(column.upper())

    def describe(self) -> str:
        """Standard, grade, thickness and bend radius: the columns a user filters rows on. Blank ones are left out."""
        parts = [self.standard, self.grade, f"THK {format_thickness(self.thickness)}"]
        if self.bend_radius.strip():
            parts.append(f"BR {self.bend_radius}")
        return " · ".join(p for p in parts if p.strip())


@dataclass(frozen=True, slots=True)
class SheetMetalPartPreference:
    """The material and thickness half of a part's NX Sheet Metal Preferences. NX keeps one set per PART, so every
    sheet metal body in it is governed by the same values; `sheet_metal_body_count` travels with them because the tool
    assumes one sheet metal body per part and warns otherwise.

    `material_name` is None when none is set. NX only uses it when `is_material_table_entry`. `thickness` is in part
    units. `row` is the standards file row named `material_name`, or None.
    """

    material_name: str | None
    is_material_table_entry: bool
    thickness: float
    sheet_metal_body_count: int
    row: SheetMetalMaterialRow | None = None

    def uses_material(self, material_name: str) -> bool:
        """Whether NX is actually using `material_name`: it is the saved material AND entry is Material Table."""
        return (
            self.is_material_table_entry
            and self.material_name is not None
            and self.material_name.casefold() == material_name.casefold()
        )


@dataclass(frozen=True, slots=True)
class SheetMetalPreferenceRead:
    """What could be learned about the preferences governing one body.

    `read_error` is set when the body is sheet metal but its preferences could not be read: distinct from "not sheet
    metal", or the restriction silently lifts. `body_thickness` is the body's own measured thickness (part units), None
    when not sheet metal or NX would not report it; it differs from `preference.thickness`, which is what the
    preferences SAY, and the two disagreeing is the mismatch the preference check reports.
    """

    NOT_SHEET_METAL: ClassVar[SheetMetalPreferenceRead]

    is_sheet_metal_body: bool
    preference: SheetMetalPartPreference | None
    read_error: str | None = None
    body_thickness: float | None = None

    @staticmethod
    def of(preference: SheetMetalPartPreference, body_thickness: float | None = None) -> SheetMetalPreferenceRead:
        return SheetMetalPreferenceRead(True, preference, None, body_thickness)

    @staticmethod
    def unreadable(error: str) -> SheetMetalPreferenceRead:
        return SheetMetalPreferenceRead(True, None, error)


SheetMetalPreferenceRead.NOT_SHEET_METAL = SheetMetalPreferenceRead(False, None)


class SheetMetalPreferenceStatus(Enum):
    """How a sheet metal body stands against its part's Sheet Metal Preferences."""

    IN_SYNC = "InSync"
    MATERIAL_NOT_SET = "MaterialNotSet"
    """No material is set, or Parameter Entry is not Material Table."""
    MATERIAL_NOT_IN_TABLE = "MaterialNotInTable"
    MATERIAL_OUT_OF_SYNC = "MaterialOutOfSync"
    """The preferences' material is made of a different physical material than the body."""
    THICKNESS_MISMATCH = "ThicknessMismatch"
    """Reported, never synced: which of the two is wrong is the user's call."""


@dataclass(frozen=True, slots=True)
class SheetMetalPreferenceCheckResult:
    """`message` says what is wrong, for the user; None when in sync."""

    IN_SYNC: ClassVar[SheetMetalPreferenceCheckResult]

    status: SheetMetalPreferenceStatus
    message: str | None


SheetMetalPreferenceCheckResult.IN_SYNC = SheetMetalPreferenceCheckResult(SheetMetalPreferenceStatus.IN_SYNC, None)


@dataclass(frozen=True, slots=True)
class SheetMetalProfile:
    """The sheet metal facts a SPEC is validated against: the thickness it is made to, and the grade of the material
    row it is made of (None when there is none yet)."""

    thickness: float
    material_grade_label: str | None
