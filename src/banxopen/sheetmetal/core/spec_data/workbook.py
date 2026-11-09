"""Parses one bead SPEC workbook (.xlsx) with the standard library only: NX's embedded Python has no openpyxl, and only
cell values are needed.

The sheet: a "Driving per spec" group (SPEC, (R) & RAD S, W, H, RAD P, THICKNESS) followed by an "Allowed Material"
group with one YES/-/blank column per grade. The six driving columns are matched by a small alias list, so harmless
header variation (the workbook's own "TICHKNESS" typo, an ampersand or extra space) does not break the import.
Everything else, read from the header row itself, is a grade column: a new grade can be added in Excel alone.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Sequence
from typing import IO, Final
from xml.etree import ElementTree

from banxopen.sheetmetal.core.errors import SpecDataError
from banxopen.sheetmetal.model.beads import BeadSpecRow
from banxopen.sheetmetal.text import SheetMetalText

_MAIN: Final = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL: Final = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL: Final = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_SPEC_ALIASES: Final = ("SPEC",)
_RADIUS_ALIASES: Final = ("R&RADS", "RRADS", "RANDRADS")
_WIDTH_ALIASES: Final = ("W",)
_HEIGHT_ALIASES: Final = ("H",)
_DIE_RADIUS_ALIASES: Final = ("RADP",)
_THICKNESS_ALIASES: Final = ("THICKNESS", "TICHKNESS")

Sheet = dict[int, dict[int, str]]
"""Row number -> column number -> cell text (1-based). Empty cells are absent."""


class BeadSpecWorkbookParser:
    def __init__(self, text: SheetMetalText | None = None) -> None:
        """`text` words the errors, which the bead tool shows."""
        self._text = text or SheetMetalText.DEFAULT

    def parse(self, standard_id: str, workbook_name: str, workbook: IO[bytes]) -> tuple[BeadSpecRow, ...]:
        """`workbook_name` is tagged onto every row, so a row says which workbook it came from. Raises
        `SpecDataError`."""
        sheet = self._read_first_sheet(workbook)
        header_row = self._find_header_row(sheet)
        header = sheet[header_row]
        last_column = max((c for c, v in header.items() if v), default=0)
        if last_column == 0:
            raise SpecDataError(self._text.workbook_header_row_empty)

        columns_by_alias: dict[str, int] = {}
        for column in range(1, last_column + 1):
            normalized = _normalize(header.get(column, ""))
            if normalized and normalized not in columns_by_alias:
                columns_by_alias[normalized] = column

        spec_col = self._require(columns_by_alias, _SPEC_ALIASES, "SPEC")
        radius_col = self._require(columns_by_alias, _RADIUS_ALIASES, "(R) & RAD S")
        width_col = self._require(columns_by_alias, _WIDTH_ALIASES, "W")
        height_col = self._require(columns_by_alias, _HEIGHT_ALIASES, "H")
        die_radius_col = self._require(columns_by_alias, _DIE_RADIUS_ALIASES, "RAD P")
        thickness_col = self._require(columns_by_alias, _THICKNESS_ALIASES, "THICKNESS")

        fixed = {spec_col, radius_col, width_col, height_col, die_radius_col, thickness_col}
        grade_columns = [
            (column, header.get(column, "").strip())
            for column in range(1, last_column + 1)
            if column not in fixed and header.get(column, "").strip()
        ]

        rows: list[BeadSpecRow] = []
        row_number = header_row + 1
        while row_number in sheet and sheet[row_number].get(spec_col, ""):
            cells = sheet[row_number]
            rows.append(
                BeadSpecRow(
                    standard_id,
                    workbook_name,
                    cells[spec_col].strip(),
                    self._number(cells, radius_col, row_number),
                    self._number(cells, width_col, row_number),
                    self._number(cells, height_col, row_number),
                    self._number(cells, die_radius_col, row_number),
                    self._number(cells, thickness_col, row_number),
                    {label: cells.get(column, "").strip().upper() == "YES" for column, label in grade_columns},
                )
            )
            row_number += 1
        return tuple(rows)

    def _read_first_sheet(self, workbook: IO[bytes]) -> Sheet:
        """The first worksheet in the workbook's own tab order."""
        try:
            with zipfile.ZipFile(workbook) as package:
                names = set(package.namelist())
                if "xl/workbook.xml" not in names:
                    raise SpecDataError(self._text.workbook_no_workbook_part)
                first_sheet = _parse(package.read("xl/workbook.xml")).find(f"{_MAIN}sheets/{_MAIN}sheet")
                if first_sheet is None:
                    raise SpecDataError(self._text.workbook_no_worksheets)
                sheet_path = _sheet_path(package, first_sheet.get(f"{_REL}id", ""))
                shared = (
                    [_item_text(item) for item in _parse(package.read("xl/sharedStrings.xml")).iter(f"{_MAIN}si")]
                    if "xl/sharedStrings.xml" in names
                    else []
                )
                return _read_cells(_parse(package.read(sheet_path)), shared)
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as ex:
            raise SpecDataError(self._text.workbook_unreadable(str(ex))) from ex

    def _find_header_row(self, sheet: Sheet) -> int:
        for row_number in sorted(sheet):
            if any(_normalize(text) == "SPEC" for text in sheet[row_number].values()):
                return row_number
        raise SpecDataError(self._text.workbook_no_header_row)

    def _require(self, columns_by_alias: dict[str, int], aliases: Sequence[str], display_name: str) -> int:
        for alias in aliases:
            if alias in columns_by_alias:
                return columns_by_alias[alias]
        raise SpecDataError(self._text.workbook_column_missing(display_name, aliases))

    def _number(self, cells: dict[int, str], column: int, row_number: int) -> float:
        text = cells.get(column, "").strip()
        try:
            return float(text)
        except ValueError:
            raise SpecDataError(
                self._text.workbook_cell_not_a_number(f"{_column_name(column)}{row_number}", text)
            ) from None


def _parse(data: bytes) -> ElementTree.Element:
    return ElementTree.fromstring(data)


def _sheet_path(package: zipfile.ZipFile, relationship_id: str) -> str:
    rels = _parse(package.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.iter(f"{_PKG_REL}Relationship"):
        if rel.get("Id") == relationship_id:
            target = rel.get("Target", "")
            return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
    raise KeyError(relationship_id)


def _item_text(item: ElementTree.Element) -> str:
    """A shared or inline string's plain text, or its rich-text runs joined; never the phonetic guide."""
    plain = item.find(f"{_MAIN}t")
    if plain is not None:
        return plain.text or ""
    return "".join(t.text or "" for run in item.findall(f"{_MAIN}r") for t in run.findall(f"{_MAIN}t"))


def _read_cells(worksheet: ElementTree.Element, shared: list[str]) -> Sheet:
    sheet: Sheet = {}
    next_row = 1
    for row in worksheet.iter(f"{_MAIN}row"):
        reference = row.get("r")
        row_number = int(reference) if reference else next_row
        next_row = row_number + 1
        cells: dict[int, str] = {}
        next_column = 1
        for cell in row.findall(f"{_MAIN}c"):
            cell_reference = cell.get("r")
            column = _column_of(cell_reference) if cell_reference else next_column
            next_column = column + 1
            text = _cell_text(cell, shared)
            if text:
                cells[column] = text
        sheet[row_number] = cells
    return sheet


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(f"{_MAIN}is")
        return _item_text(inline) if inline is not None else ""
    value = cell.find(f"{_MAIN}v")
    raw = value.text or "" if value is not None else ""
    if kind == "s":
        index = int(raw) if raw.isdigit() else -1
        return shared[index] if 0 <= index < len(shared) else ""
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw  # numbers (stored invariant), formula string results and errors: the stored value is the text


def _column_of(reference: str) -> int:
    """1-based column number of a cell reference such as "AB12"."""
    column = 0
    for char in reference:
        if not char.isalpha():
            break
        column = column * 26 + (ord(char.upper()) - ord("A") + 1)
    return column


def _column_name(column: int) -> str:
    name = ""
    while column > 0:
        column, remainder = divmod(column - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


_NOT_ALNUM: Final = re.compile(r"[\W_]+", re.UNICODE)


def _normalize(text: str) -> str:
    """Letters and digits only, upper case: "(R) & RAD S" -> "RRADS"."""
    return _NOT_ALNUM.sub("", text).upper()
