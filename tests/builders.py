"""Builders and fakes shared across the suite (ports of the C# TestFixtures and TableRows)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from banxopen.foundation import BodyId, BodyInfo, BodyKind, RuleOutcome
from banxopen.materials import (
    FeatureMaterialConstraintProvider,
    Material,
    MaterialAssignmentRuleContext,
    MaterialCategory,
    MaterialId,
    MaterialLibraryId,
)
from banxopen.materials.core.rules.feature_gate import FeatureConstraintGateRule
from banxopen.sheetmetal import (
    BEAD_DOMAIN,
    BEAD_FEATURE_NOUN,
    EMPTY_INVENTORY,
    BeadOnBody,
    BeadSpecRow,
    BeadSpecStatus,
    BodyFeatureInventory,
    RequirementUser,
    SheetMetalAllowance,
    SheetMetalMaterialColumns,
    SheetMetalMaterialRow,
    SheetMetalMaterialTable,
    SheetMetalPartPreference,
    SheetMetalPreferenceRead,
    SheetMetalRequirement,
)

STANDARD = "XX_Standard"
BODY_ID = BodyId("body-1")
CATEGORY = MaterialCategory("al", "Aluminum", ("Aluminum",))


def spec_row(
    spec_id: str,
    thickness: float = 0.02,
    *grades: str,
    height: float = 0.625,
    radius: float = 0.245,
    die_radius: float = 0.188,
    width: float = 0.625,
    standard: str = STANDARD,
    workbook: str = "B1005010",
) -> BeadSpecRow:
    """A SPEC row allowing `grades` among the workbook columns 2024-O, 5052-O, 7075-T6 (in that order)."""
    return BeadSpecRow(
        standard,
        workbook,
        spec_id,
        radius,
        width,
        height,
        die_radius,
        thickness,
        {g: g in grades for g in ("2024-O", "5052-O", "7075-T6")},
    )


def table_row(
    name: str,
    physical: str,
    grade: str,
    thickness: float = 0.02,
    standard: str = "XX_Standard",
    bend_radius: str = "0.06",
) -> SheetMetalMaterialRow:
    return SheetMetalMaterialRow(
        name,
        standard,
        standard,
        physical,
        grade,
        thickness,
        bend_radius,
        0,
        {
            SheetMetalMaterialColumns.STANDARD.upper(): standard,
            SheetMetalMaterialColumns.PHYSICAL_MATERIAL_NAME.upper(): physical,
            SheetMetalMaterialColumns.SHEET_METAL_MATERIAL.upper(): grade,
        },
    )


def aluminum_table(*extra: SheetMetalMaterialRow) -> SheetMetalMaterialTable:
    """Aluminum 2024-O (two rows: 0.020, then 0.032), 5052-O and 7075-T6 (YY_Standard) have rows; "Titanium Grade 5"
    has none."""
    return SheetMetalMaterialTable(
        (
            table_row("2024-O_0.020", "Aluminum 2024-O", "2024-O"),
            table_row("5052-O_0.020", "Aluminum 5052-O", "5052-O"),
            table_row("2024-O_0.032", "Aluminum 2024-O", "2024-O", thickness=0.032),
            table_row("7075-T6_0.020", "Aluminum 7075-T6", "7075-T6", standard="YY_Standard"),
            *extra,
        )
    )


def make_material(name: str) -> Material:
    return Material(
        id=MaterialId(name),
        library_id=MaterialLibraryId("Sheet Metal Materials"),
        name=name,
        category=CATEGORY,
        properties=(),
    )


def body(kind: BodyKind = BodyKind.SHEET_METAL, body_id: BodyId = BODY_ID, name: str = "SM_BODY") -> BodyInfo:
    return BodyInfo(body_id, name, kind, 0.0, {})


def context(
    material: str, variant: str | None = None, kind: BodyKind = BodyKind.SHEET_METAL
) -> MaterialAssignmentRuleContext:
    b = body(kind)
    return MaterialAssignmentRuleContext(
        requested_material=make_material(material),
        target_body=b,
        current_assignment=None,
        all_target_bodies_in_batch=(b,),
        requested_variant=variant,
    )


def gate(provider: FeatureMaterialConstraintProvider, material: str) -> RuleOutcome:
    return FeatureConstraintGateRule((provider,)).evaluate(context(material))


def stamped(key: str, name: str, spec_id: str, *family: BeadSpecRow) -> BeadOnBody:
    return BeadOnBody(key, name, BeadSpecStatus.STAMPED, STANDARD, spec_id, family)


def matched(key: str, name: str, spec_id: str, *family: BeadSpecRow) -> BeadOnBody:
    return BeadOnBody(key, name, BeadSpecStatus.MATCHED, STANDARD, spec_id, family)


def unmatched(key: str, name: str) -> BeadOnBody:
    return BeadOnBody(key, name, BeadSpecStatus.UNMATCHED, None, None)


def requirement(spec_id: str, built: float, *grades_or_family: str | BeadSpecRow) -> SheetMetalRequirement:
    """Allowing only `grades` at the built thickness, or what a `family` of rows marks YES."""
    rows = [g for g in grades_or_family if isinstance(g, BeadSpecRow)]
    allowance = (
        SheetMetalAllowance.from_family(rows, built)
        if rows
        else SheetMetalAllowance.at_built_thickness(built, (g for g in grades_or_family if isinstance(g, str)))
    )
    return SheetMetalRequirement(
        BEAD_DOMAIN, BEAD_FEATURE_NOUN, STANDARD, spec_id, allowance, (RequirementUser("101", "BEAD(1)"),), "B1005010"
    )


def sheet_metal_read(
    table: SheetMetalMaterialTable, row: str | None = None, body_thickness: float | None = 0.02, bodies: int = 1
) -> SheetMetalPreferenceRead:
    return SheetMetalPreferenceRead.of(
        SheetMetalPartPreference(row, True, 0.02, bodies, table.find(row)), body_thickness
    )


class FakeRequirementReader:
    def __init__(self, requirements: Iterable[SheetMetalRequirement] = (), *errors: str) -> None:
        self._requirements = tuple(requirements)
        self._errors = errors

    def read_all(self) -> tuple[Sequence[SheetMetalRequirement], Sequence[str]]:
        return self._requirements, self._errors


class FixedPreferenceReader:
    def __init__(self, read: SheetMetalPreferenceRead) -> None:
        self._read = read

    def read_for(self, body_id: BodyId) -> SheetMetalPreferenceRead:
        return self._read


class FixedInventory:
    """The same read for every body, or only for `only_for` (other bodies then have no features)."""

    def __init__(self, inventory: BodyFeatureInventory, only_for: BodyId | None = None) -> None:
        self._inventory = inventory
        self._only_for = only_for

    def read(self, body_id: BodyId) -> BodyFeatureInventory:
        return EMPTY_INVENTORY if self._only_for is not None and body_id != self._only_for else self._inventory


class SpecLookup:
    """Workbook rows in memory, each family ordered by thickness like the real cache."""

    def __init__(self, rows: Iterable[BeadSpecRow] = ()) -> None:
        self._rows = tuple(rows)

    def find_all(self, standard_id: str, spec_id: str) -> Sequence[BeadSpecRow]:
        return sorted(
            (r for r in self._rows if r.standard_id == standard_id and r.spec_id == spec_id), key=lambda r: r.thickness
        )

    def all_specs(self) -> Sequence[BeadSpecRow]:
        return self._rows
