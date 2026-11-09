"""Ports of SpecData/*Tests.cs: the cache and lookup, the workbook parser, the Standard folder layout."""

from __future__ import annotations

import io
import os
import time
from collections.abc import Sequence
from pathlib import Path

import openpyxl
import pytest
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from banxopen.sheetmetal import (
    BeadSpecCache,
    BeadSpecRow,
    BeadSpecWorkbookParser,
    CachedBeadSpecLookup,
    SheetMetalMaterialTable,
    SpecDataError,
    StandardInfo,
    find_bead_workbook,
    list_bead_spec_workbooks,
    list_standards,
    workbook_name_of,
)

from builders import table_row

BEAD_SPEC = "B1005010"
STANDARD = StandardInfo("B1005010", "B1005010", "unused")


class FakeSource:
    def __init__(self, workbook: str | None) -> None:
        self.read_count = 0
        self.workbook = workbook
        self.other_workbooks: list[str] = []
        self.unreadable: set[str] = set()
        self.rows_per_workbook = 1

    def list_standards(self) -> Sequence[StandardInfo]:
        return (STANDARD,)

    def find_workbook(self, standard: StandardInfo, bead_spec_name: str) -> str | None:
        return self.workbook if bead_spec_name == BEAD_SPEC else None

    def list_workbooks(self, standard: StandardInfo) -> Sequence[str]:
        return ([self.workbook] if self.workbook else []) + self.other_workbooks

    def read_workbook(self, standard: StandardInfo, workbook_path: str) -> Sequence[BeadSpecRow]:
        self.read_count += 1
        if workbook_path in self.unreadable:
            raise SpecDataError(f"'{workbook_path}' is not a valid bead SPEC workbook.")
        # One SPEC id per thickness, thickest first, to prove nothing depends on file order.
        return [
            BeadSpecRow(
                standard.id,
                Path(workbook_path).stem,
                "SPEC-1",
                0.245,
                0.625,
                0.625,
                0.188,
                0.02 + (self.rows_per_workbook - 1 - i) * 0.01,
                {"2024-O": True},
            )
            for i in range(self.rows_per_workbook)
        ]


@pytest.fixture
def workbook(tmp_path: Path) -> str:
    return touch(tmp_path, "wb.xlsx")


def touch(folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("placeholder", encoding="utf-8")
    return str(path)


def cache_for(source: FakeSource, tmp_path: Path, warnings: list[str] | None = None) -> BeadSpecCache:
    return BeadSpecCache(source, tmp_path / "cache", warnings.append if warnings is not None else (lambda _: None))


def test_an_unchanged_workbook_is_not_reimported(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    cache = cache_for(source, tmp_path)
    cache.get_specs(STANDARD, BEAD_SPEC)
    cache.get_specs(STANDARD, BEAD_SPEC)
    assert source.read_count == 1


def test_a_modified_workbook_is_reimported(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    cache = cache_for(source, tmp_path)
    cache.get_specs(STANDARD, BEAD_SPEC)
    later = time.time() + 300
    os.utime(workbook, (later, later))
    cache.get_specs(STANDARD, BEAD_SPEC)
    assert source.read_count == 2


def test_a_different_workbook_in_the_folder_is_reimported(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    cache = cache_for(source, tmp_path)
    cache.get_specs(STANDARD, BEAD_SPEC)
    replacement = touch(tmp_path / "other", "wb.xlsx")
    stat = Path(workbook).stat()
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    source.workbook = replacement
    cache.get_specs(STANDARD, BEAD_SPEC)
    assert source.read_count == 2


def test_no_workbook_or_an_unknown_bead_spec_is_empty_and_reads_nothing(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    cache = cache_for(source, tmp_path)
    assert not cache.get_specs(STANDARD, "S5010")
    source.workbook = None
    assert not cache.get_specs(STANDARD, BEAD_SPEC)
    assert source.read_count == 0


def test_get_all_specs_reads_and_caches_every_workbook_separately(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    source.other_workbooks.append(touch(tmp_path, "other.xlsx"))
    cache = cache_for(source, tmp_path)
    assert len(cache.get_all_specs(STANDARD)) == 2
    cache.get_all_specs(STANDARD)
    assert source.read_count == 2


def test_get_all_specs_warns_when_two_workbooks_share_a_spec_id(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    other = touch(tmp_path, "other.xlsx")
    source.other_workbooks.append(other)
    warnings: list[str] = []
    cache_for(source, tmp_path, warnings).get_all_specs(STANDARD)
    (warning,) = warnings
    assert "SPEC-1" in warning
    assert "wb" in warning and "other" in warning


def test_get_all_specs_does_not_warn_when_spec_ids_are_unique(tmp_path: Path, workbook: str):
    warnings: list[str] = []
    assert len(cache_for(FakeSource(workbook), tmp_path, warnings).get_all_specs(STANDARD)) == 1
    assert not warnings


def test_get_all_specs_skips_an_unreadable_workbook_and_keeps_the_rest(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    bad = touch(tmp_path, "bad.xlsx")
    source.other_workbooks.append(bad)
    source.unreadable.add(bad)
    (row,) = cache_for(source, tmp_path).get_all_specs(STANDARD)
    assert row.workbook_name == "wb"


def test_lookup_returns_every_row_of_a_family_ordered_by_thickness(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    source.rows_per_workbook = 3
    family = CachedBeadSpecLookup(cache_for(source, tmp_path)).find_all(STANDARD.id, "SPEC-1")
    assert len(family) == 3
    assert [r.thickness for r in family] == sorted(r.thickness for r in family)


def test_lookup_does_not_resolve_a_spec_claimed_by_two_workbooks(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    source.other_workbooks.append(touch(tmp_path, "other.xlsx"))
    assert not CachedBeadSpecLookup(cache_for(source, tmp_path), lambda _: None).find_all(STANDARD.id, "SPEC-1")


def test_lookup_still_finds_a_spec_when_another_workbook_is_unreadable(tmp_path: Path, workbook: str):
    source = FakeSource(workbook)
    bad = touch(tmp_path, "bad.xlsx")
    source.other_workbooks.append(bad)
    source.unreadable.add(bad)
    assert CachedBeadSpecLookup(cache_for(source, tmp_path), lambda _: None).find_all(STANDARD.id, "SPEC-1")


# ---- the workbook parser ----

HEADERS = ["SPEC", "(R) & RAD S", "W", "H", "RAD P", "THICKNESS", "2024-O"]


def save(book: openpyxl.Workbook) -> io.BytesIO:
    stream = io.BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream


def sample_workbook() -> io.BytesIO:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.cell(1, 3, "Driving per spec")
    sheet.cell(1, 7, "Allowed Material")
    for column, header in enumerate(["SPEC", "(R) & RAD S", "W", "H", "RAD P", "TICHKNESS", "2024-O", "5052-O"], 1):
        sheet.cell(2, column, header)
    for row, values in (
        (3, ["B1005010-1", 0.245, 0.625, 0.625, 0.188, 0.02, "YES", "-"]),
        (4, ["B1005010-2", 0.240, 0.625, 0.625, 0.188, 0.025, "YES", "YES"]),
    ):
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value)
    return save(book)


def test_reads_all_rows_with_fixed_and_dynamic_material_columns():
    rows = BeadSpecWorkbookParser().parse("B1005010", "B1005010", sample_workbook())
    assert len(rows) == 2
    first = rows[0]
    assert (first.standard_id, first.workbook_name, first.spec_id) == ("B1005010", "B1005010", "B1005010-1")
    assert (first.radius_and_rad_s, first.width, first.height, first.die_radius_p, first.thickness) == (
        0.245,
        0.625,
        0.625,
        0.188,
        0.02,
    )
    assert first.allowed_material_grades == {"2024-O": True, "5052-O": False}
    assert rows[1].allowed_material_grades["5052-O"]


def test_a_missing_required_column_is_named():
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for column, header in enumerate(HEADERS[:5], 1):
        sheet.cell(1, column, header)
    with pytest.raises(SpecDataError, match="THICKNESS"):
        BeadSpecWorkbookParser().parse("B1005010", "B1005010", save(book))


def header_book() -> tuple[openpyxl.Workbook, object]:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for column, header in enumerate(HEADERS, 1):
        sheet.cell(1, column, header)
    return book, sheet


def test_a_rich_text_header_is_read_as_its_joined_text():
    book, sheet = header_book()
    sheet.cell(1, 6).value = CellRichText([TextBlock(InlineFont(b=True), "TICH"), "KNESS"])  # type: ignore[attr-defined]
    sheet.cell(2, 1, "B1005010-1")  # type: ignore[attr-defined]
    for column in range(2, 7):
        sheet.cell(2, column, 0.5)  # type: ignore[attr-defined]
    sheet.cell(2, 7, "YES")  # type: ignore[attr-defined]
    (row,) = BeadSpecWorkbookParser().parse("STD", "B1005010", save(book))
    assert row.thickness == 0.5
    assert row.allowed_material_grades["2024-O"]


def test_text_in_a_number_column_names_the_cell():
    book, sheet = header_book()
    sheet.cell(2, 1, "B1005010-1")  # type: ignore[attr-defined]
    for column in range(2, 7):
        sheet.cell(2, column, 0.5)  # type: ignore[attr-defined]
    sheet.cell(2, 3, "wide")  # type: ignore[attr-defined]
    with pytest.raises(SpecDataError, match="C2"):
        BeadSpecWorkbookParser().parse("STD", "B1005010", save(book))


def test_reads_only_the_first_sheet_and_stops_at_the_first_empty_spec():
    book, sheet = header_book()
    sheet.title = "Specs"  # type: ignore[attr-defined]
    for row, spec in ((2, "B1005010-1"), (4, "B1005010-9")):
        sheet.cell(row, 1, spec)  # type: ignore[attr-defined]
        for column in range(2, 7):
            sheet.cell(row, column, 0.5)  # type: ignore[attr-defined]
    notes = book.create_sheet("Notes")
    for column, header in enumerate(HEADERS, 1):
        notes.cell(1, column, header)
    notes.cell(2, 1, "NOT-A-SPEC")
    assert [r.spec_id for r in BeadSpecWorkbookParser().parse("STD", "B1005010", save(book))] == ["B1005010-1"]


def test_a_file_that_is_not_a_workbook_is_refused():
    with pytest.raises(SpecDataError, match="readable"):
        BeadSpecWorkbookParser().parse("STD", "B1005010", io.BytesIO(b"placeholder"))


# ---- the Standard folder layout ----


def table(root: Path, *standards: str) -> SheetMetalMaterialTable:
    rows = [table_row(f"row{i}", "Aluminum 2024-O", "2024-O", standard=s) for i, s in enumerate(standards)]
    return SheetMetalMaterialTable(rows, str(root / "sheet_metal_material_table.csv"))


def standard(root: Path, standard_id: str = "XX_Standard") -> StandardInfo:
    (info,) = list_standards(table(root, standard_id))
    return info


def bead_folder(root: Path, standard_id: str = "XX_Standard") -> Path:
    return root / standard_id / "Features" / "BEAD"


def test_lists_each_standard_with_its_bead_folder_beside_the_table(tmp_path: Path):
    standards = list_standards(table(tmp_path, "XX_Standard", "YY_Standards"))
    assert [s.id for s in standards] == ["XX_Standard", "YY_Standards"]
    assert Path(standards[0].bead_spec_folder) == bead_folder(tmp_path.resolve())


@pytest.mark.parametrize("name", ["B1005010.xlsx", "BA_Bead_B1005010_revC.xlsx"])
def test_a_bead_spec_is_the_workbook_whose_name_contains_it(tmp_path: Path, name: str):
    workbook = touch(bead_folder(tmp_path), name)
    assert Path(find_bead_workbook(standard(tmp_path), "B1005010") or "") == Path(workbook).resolve()


def test_each_bead_spec_resolves_to_its_own_workbook(tmp_path: Path):
    first = touch(bead_folder(tmp_path), "B1005010.xlsx")
    second = touch(bead_folder(tmp_path), "S5010.xlsx")
    info = standard(tmp_path)
    assert Path(find_bead_workbook(info, "B1005010") or "") == Path(first).resolve()
    assert Path(find_bead_workbook(info, "S5010") or "") == Path(second).resolve()


def test_a_missing_or_empty_bead_folder_means_no_bead_specs(tmp_path: Path):
    assert find_bead_workbook(standard(tmp_path), "B1005010") is None
    assert not list_bead_spec_workbooks(standard(tmp_path))
    touch(bead_folder(tmp_path), "No-Standards.xls")
    touch(bead_folder(tmp_path), "~$B1005010.xlsx")
    assert find_bead_workbook(standard(tmp_path), "B1005010") is None
    assert not list_bead_spec_workbooks(standard(tmp_path))


def test_a_spec_no_workbook_is_named_for_has_none_and_the_lock_file_is_no_second_workbook(tmp_path: Path):
    workbook = touch(bead_folder(tmp_path), "B1005010.xlsx")
    touch(bead_folder(tmp_path), "~$B1005010.xlsx")
    info = standard(tmp_path)
    assert find_bead_workbook(info, "S5010") is None
    assert [Path(w) for w in list_bead_spec_workbooks(info)] == [Path(workbook).resolve()]


def test_a_spec_name_matching_two_workbooks_is_refused_naming_them(tmp_path: Path):
    touch(bead_folder(tmp_path), "B1005010_revB.xlsx")
    touch(bead_folder(tmp_path), "B1005010_revC.xlsx")
    with pytest.raises(SpecDataError, match=r"B1005010_revB\.xlsx") as error:
        find_bead_workbook(standard(tmp_path), "B1005010")
    assert "B1005010_revC.xlsx" in str(error.value)


def test_a_workbook_named_exactly_as_the_spec_wins(tmp_path: Path):
    exact = touch(bead_folder(tmp_path), "B100.xlsx")
    touch(bead_folder(tmp_path), "B1005010.xlsx")
    assert Path(find_bead_workbook(standard(tmp_path), "B100") or "") == Path(exact).resolve()


def test_every_workbook_in_the_folder_is_listed_by_name(tmp_path: Path):
    first = touch(bead_folder(tmp_path), "B1005010.xlsx")
    second = touch(bead_folder(tmp_path), "S5010.xlsx")
    assert [Path(w) for w in list_bead_spec_workbooks(standard(tmp_path))] == [
        Path(first).resolve(),
        Path(second).resolve(),
    ]


def test_a_workbook_is_named_by_its_file_stem():
    assert workbook_name_of(r"C:\specs\XX\Features\BEAD\B1005010.xlsx") == "B1005010"


@pytest.mark.parametrize("name", ["XX/Standard", ".."])
def test_a_standard_that_cannot_be_a_folder_name_is_refused(tmp_path: Path, name: str):
    with pytest.raises(SpecDataError):
        list_standards(table(tmp_path, name))


def test_a_table_not_read_from_a_file_has_no_folders():
    with pytest.raises(RuntimeError):
        list_standards(SheetMetalMaterialTable([table_row("a", "M", "G")]))
