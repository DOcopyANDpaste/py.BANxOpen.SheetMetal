"""NX's sheet metal material standards file: which materials the Sheet Metal Preferences can be set to, what each is
made of, its grade and its Standard. The single source, so the tool and NX cannot disagree."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from banxopen.sheetmetal.core.errors import SpecDataError
from banxopen.sheetmetal.model.table import SheetMetalMaterialColumns, SheetMetalMaterialRow
from banxopen.sheetmetal.model.thickness import thickness_matches
from banxopen.sheetmetal.text import SheetMetalText


def _same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.casefold() == b.casefold()


class SheetMetalMaterialTable:
    """Material, Standard and physical material names compare ignoring case."""

    def __init__(self, rows: Iterable[SheetMetalMaterialRow], source_path: str | None = None) -> None:
        """`source_path` is the file the table was read from; None for a table built in memory (tests)."""
        self.rows = tuple(rows)
        """In file order."""
        self.source_path = source_path
        standards: dict[str, str] = {}
        for row in self.rows:
            standards.setdefault(row.standard.casefold(), row.standard)
        self.standards = tuple(standards.values())
        """Every Standard with a row, in the order each first appears."""

    @staticmethod
    def load(path: str | Path, text: SheetMetalText | None = None) -> SheetMetalMaterialTable:
        """Raises FileNotFoundError, or `SpecDataError` naming the file and the line."""
        text = text or SheetMetalText.DEFAULT
        file = Path(path)
        if not file.is_file():
            raise FileNotFoundError(text.material_table_not_found(str(path)))
        try:
            return SheetMetalMaterialTable(parse_material_table(file.read_bytes(), text), str(path))
        except SpecDataError as ex:
            raise SpecDataError(text.material_table_invalid(str(path), str(ex))) from ex

    def rows_for(self, standard: str) -> tuple[SheetMetalMaterialRow, ...]:
        return tuple(r for r in self.rows if _same(r.standard, standard))

    def find(self, material_name: str | None) -> SheetMetalMaterialRow | None:
        """The row named `material_name`, or None: as for preferences set to a material the table no longer lists."""
        if material_name is None or not material_name.strip():
            return None
        return next((r for r in self.rows if _same(r.name, material_name)), None)

    def rows_for_physical_material(self, physical_material_name: str) -> tuple[SheetMetalMaterialRow, ...]:
        """Every row made of the material, in file order: the rows the preferences could be set to."""
        return tuple(r for r in self.rows if _same(r.physical_material_name, physical_material_name))

    def first_row_for_physical_material(self, physical_material_name: str) -> SheetMetalMaterialRow | None:
        """The "is this material settable in the preferences at all" test; not how the row that gets set is chosen."""
        return next((r for r in self.rows if _same(r.physical_material_name, physical_material_name)), None)

    def grade_for_physical_material(self, physical_material_name: str) -> str | None:
        row = self.first_row_for_physical_material(physical_material_name)
        return row.grade if row is not None else None


class SheetMetalStandardSelection:
    """The Standard a dialog has chosen for the Sheet Metal Preferences, shared between that dialog and the row gate.
    Mutable on purpose: the Standard is a sheet metal concept the material engine has no reason to know about. None
    means every Standard's rows are offered (a tool with no Standard picker)."""

    def __init__(self, table: SheetMetalMaterialTable, selected: str | None = None) -> None:
        self._table = table
        self.selected = selected

    @property
    def standards(self) -> tuple[str, ...]:
        return self._table.standards

    def rows_for(self, physical_material_name: str) -> tuple[SheetMetalMaterialRow, ...]:
        """The rows made of the material under `selected`, in file order, or under every Standard."""
        rows = self._table.rows_for_physical_material(physical_material_name)
        if self.selected is None:
            return rows
        return tuple(r for r in rows if _same(r.standard, self.selected))


@dataclass(frozen=True, slots=True)
class SheetMetalRowFilter:
    """The Material Assignment dialog's filters: Standard, physical material, thickness, bend radius. None means "any".
    Each dropdown's options cascade: the distinct values of its column among the rows the OTHER three let through, so a
    dropdown never offers a value that would leave nothing, and its own current value stays listed."""

    standard: str | None = None
    physical_material_name: str | None = None
    thickness: float | None = None
    bend_radius: str | None = None

    @staticmethod
    def for_row(row: SheetMetalMaterialRow) -> SheetMetalRowFilter:
        """The filters that leave `row`, e.g. a dialog opening on the part's preferences row. Blank columns are
        "any"."""
        return SheetMetalRowFilter(
            row.standard if row.standard.strip() else None,
            row.physical_material_name,
            row.thickness,
            row.bend_radius if row.bend_radius.strip() else None,
        )

    @property
    def is_empty(self) -> bool:
        return self == NO_FILTER

    def matches(self, row: SheetMetalMaterialRow) -> bool:
        return self._standard(row) and self._material(row) and self._thickness(row) and self._bend(row)

    def apply(self, rows: Iterable[SheetMetalMaterialRow]) -> tuple[SheetMetalMaterialRow, ...]:
        return tuple(r for r in rows if self.matches(r))

    def standard_options(self, rows: Iterable[SheetMetalMaterialRow]) -> tuple[str, ...]:
        """In the order each first appears."""
        return _distinct(r.standard for r in rows if self._material(r) and self._thickness(r) and self._bend(r))

    def physical_material_options(self, rows: Iterable[SheetMetalMaterialRow]) -> tuple[str, ...]:
        """Alphabetical."""
        values = _distinct(
            r.physical_material_name for r in rows if self._standard(r) and self._thickness(r) and self._bend(r)
        )
        return tuple(sorted(values, key=str.casefold))

    def thickness_options(self, rows: Iterable[SheetMetalMaterialRow]) -> tuple[float, ...]:
        """Ascending. Thicknesses within tolerance of one another are one option, the first met."""
        options: list[float] = []
        for row in rows:
            fits = self._standard(row) and self._material(row) and self._bend(row)
            if fits and not any(thickness_matches(t, row.thickness) for t in options):
                options.append(row.thickness)
        return tuple(sorted(options))

    def bend_radius_options(self, rows: Iterable[SheetMetalMaterialRow]) -> tuple[str, ...]:
        """Numeric radii ascending, then table pointers (`@BEND_TABLE_1`) alphabetically."""
        values = _distinct(
            r.bend_radius for r in rows if self._standard(r) and self._material(r) and self._thickness(r)
        )

        def key(value: str) -> tuple[int, float, str]:
            number = _try_number(value)
            return (1, 0.0, value.casefold()) if number is None else (0, number, value.casefold())

        return tuple(sorted(values, key=key))

    def _standard(self, row: SheetMetalMaterialRow) -> bool:
        return self.standard is None or _same(row.standard, self.standard)

    def _material(self, row: SheetMetalMaterialRow) -> bool:
        return self.physical_material_name is None or _same(row.physical_material_name, self.physical_material_name)

    def _thickness(self, row: SheetMetalMaterialRow) -> bool:
        return self.thickness is None or thickness_matches(row.thickness, self.thickness)

    def _bend(self, row: SheetMetalMaterialRow) -> bool:
        return self.bend_radius is None or _same(row.bend_radius, self.bend_radius)


NO_FILTER: Final = SheetMetalRowFilter()


def _distinct(values: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for value in values:
        if value.strip():
            seen.setdefault(value.casefold(), value)
    return tuple(seen.values())


def _try_number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


# ---- The standards file format ----

_UNITS: Final = "UNITS"
_SUPPORTED_UNITS: Final = "ENGLISH"
_TABLE_START: Final = "MATERIAL_TABLE"
_TABLE_END: Final = "END_OF_MATERIAL_TABLE"
_VERSION: Final = "VERSION"
_UNIQUE: Final = "unique"
_KNOWN_PROPERTIES: Final = (_UNIQUE, "show", "hide", "attribute")


def parse_material_table(content: bytes | str, text: SheetMetalText | None = None) -> tuple[SheetMetalMaterialRow, ...]:
    """Reads the MATERIAL_TABLE block of an NX standards file (.txt or .csv), the format of NX's own
    `UGII/materials/sheet_metal_material_table.txt`:

        UNITS,ENGLISH
        MATERIAL_TABLE
        VERSION,2
        Material_Name,Standard,PHYSICAL_MATERIAL_NAME,SheetMetal_Material,THICKNESS,BEND_RADIUS
        unique,show,show,attribute,show,show
        2024-O_0.016,XX_Standard,Aluminum 2024,2024-O,0.016,@BEND_TABLE_1
        END_OF_MATERIAL_TABLE

    Only UNITS and the material table are read; other blocks and `#` comments are skipped. Cells are separated by
    commas (NX's format) or tabs (a table pasted from a spreadsheet), decided once from the header row: NX's sample
    pads its commas with tabs, and a tab-separated cell can hold a comma. Only ENGLISH files are accepted: bead SPECs
    and the thickness tolerance are in inches. Raises `SpecDataError` naming the line.
    """
    text = text or SheetMetalText.DEFAULT
    lines = (_decode(content) if isinstance(content, bytes) else content).replace("\r\n", "\n").replace("\r", "\n")
    lines = lines.split("\n")

    units: str | None = None
    index = 0
    while index < len(lines):
        if not _skippable(lines[index]):
            keyword = _keyword_cells(lines[index])
            if _is(keyword[0], _UNITS):
                units = keyword[1] if len(keyword) > 1 else ""
            elif _is(keyword[0], _TABLE_START):
                break
        index += 1

    if index == len(lines):
        raise SpecDataError(text.table_no_block(_TABLE_START))
    if units is None:
        raise SpecDataError(text.table_no_units(_UNITS, _TABLE_START, index + 1, _SUPPORTED_UNITS))
    if not _is(units, _SUPPORTED_UNITS):
        raise SpecDataError(text.table_units_unsupported(_UNITS, units, _SUPPORTED_UNITS))
    return _read_table(lines, index + 1, text)


def _read_table(lines: Sequence[str], index: int, text: SheetMetalText) -> tuple[SheetMetalMaterialRow, ...]:
    index = _next_content(lines, index, text.table_expected_header_row, text)
    if _is(_keyword_cells(lines[index])[0], _VERSION):
        index = _next_content(lines, index + 1, text.table_expected_header_row, text)

    header_line = index
    delimiter = "," if "," in lines[header_line] else "\t"
    headers = _trim_trailing(_cells(lines[header_line], delimiter))

    index = _next_content(lines, index + 1, text.table_expected_property_row, text)
    property_line = index
    properties = _trim_trailing(_cells(lines[property_line], delimiter))
    unique_column = _validate_columns(headers, properties, header_line + 1, property_line + 1, text)

    rows: list[SheetMetalMaterialRow] = []
    names: dict[str, int] = {}
    for line_index in range(index + 1, len(lines)):
        if _skippable(lines[line_index]):
            continue
        line_number = line_index + 1
        cells = _trim_trailing(_cells(lines[line_index], delimiter))
        if len(cells) == 1 and _is(cells[0], _TABLE_END):
            return tuple(rows)
        if len(cells) > len(headers):
            raise SpecDataError(text.table_row_width(line_number, len(cells), header_line + 1, len(headers)))
        row = _read_row(headers, cells, unique_column, line_number, text)
        first = names.get(row.name.casefold())
        if first is not None:
            raise SpecDataError(text.table_duplicate_name(line_number, row.name, first, headers[unique_column]))
        names[row.name.casefold()] = line_number
        rows.append(row)
    raise SpecDataError(text.table_no_end(_TABLE_START, _TABLE_END))


def _validate_columns(
    headers: list[str], properties: list[str], header_line: int, property_line: int, text: SheetMetalText
) -> int:
    if not headers:
        raise SpecDataError(text.table_header_empty(header_line))
    blank = next((i for i, h in enumerate(headers) if not h), None)
    if blank is not None:
        raise SpecDataError(text.table_column_unnamed(header_line, blank + 1))
    seen: set[str] = set()
    for header in headers:
        if header.casefold() in seen:
            raise SpecDataError(text.table_column_repeated(header_line, header))
        seen.add(header.casefold())
    if len(properties) != len(headers):
        raise SpecDataError(text.table_property_row_width(property_line, len(properties), len(headers)))
    for header, prop in zip(headers, properties, strict=True):
        if not any(_is(prop, known) for known in _KNOWN_PROPERTIES):
            raise SpecDataError(text.table_property_unknown(property_line, header, prop, _KNOWN_PROPERTIES))
    unique = [i for i, p in enumerate(properties) if _is(p, _UNIQUE)]
    if len(unique) != 1:
        raise SpecDataError(text.table_unique_count(property_line, _UNIQUE, len(unique)))
    missing = [r for r in SheetMetalMaterialColumns.REQUIRED if r.casefold() not in seen]
    if missing:
        raise SpecDataError(text.table_columns_missing(header_line, missing))
    return unique[0]


def _read_row(
    headers: list[str], cells: list[str], unique_column: int, line_number: int, text: SheetMetalText
) -> SheetMetalMaterialRow:
    columns = {h.upper(): (cells[i] if i < len(cells) else "") for i, h in enumerate(headers)}
    name = columns[headers[unique_column].upper()]
    if not name:
        raise SpecDataError(text.table_unique_empty(line_number, headers[unique_column]))
    for required in SheetMetalMaterialColumns.REQUIRED:
        if not columns[required.upper()]:
            raise SpecDataError(text.table_value_empty(line_number, name, required))

    thickness_text = columns[SheetMetalMaterialColumns.THICKNESS.upper()]
    thickness = _try_number(thickness_text)
    if thickness is None or not thickness > 0:
        raise SpecDataError(
            text.table_thickness_invalid(line_number, name, SheetMetalMaterialColumns.THICKNESS, thickness_text)
        )
    return SheetMetalMaterialRow(
        name=name,
        standard=columns[SheetMetalMaterialColumns.STANDARD.upper()],
        sheet_metal_standard=columns.get(SheetMetalMaterialColumns.SHEET_METAL_STANDARD.upper(), ""),
        physical_material_name=columns[SheetMetalMaterialColumns.PHYSICAL_MATERIAL_NAME.upper()],
        grade=columns[SheetMetalMaterialColumns.SHEET_METAL_MATERIAL.upper()],
        thickness=thickness,
        bend_radius=columns.get(SheetMetalMaterialColumns.BEND_RADIUS.upper(), ""),
        line_number=line_number,
        columns=columns,
    )


def _decode(content: bytes) -> str:
    """UTF-16 with a byte order mark, then UTF-8 (with or without one), else Latin-1: the nearest to the Windows code
    page a spreadsheet saves a .csv in, so a degree sign survives. VERIFY: which encoding NX itself expects."""
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16")
    body = content[3:] if content[:3] == b"\xef\xbb\xbf" else content
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def _next_content(lines: Sequence[str], index: int, expected: str, text: SheetMetalText) -> int:
    while index < len(lines):
        if not _skippable(lines[index]):
            return index
        index += 1
    raise SpecDataError(text.table_ends_before(_TABLE_START, expected))


def _skippable(line: str) -> bool:
    trimmed = line.strip()
    return not trimmed or trimmed[0] == "#" or all(c == "," or c.isspace() for c in trimmed)


def _keyword_cells(line: str) -> list[str]:
    """Keyword lines come before the delimiter is known and hold no cell that could contain one: split on both."""
    return [c.strip() for c in line.replace("\t", ",").split(",") if c.strip()] or [""]


def _cells(line: str, delimiter: str) -> list[str]:
    return [c.strip() for c in line.split(delimiter)]


def _trim_trailing(cells: list[str]) -> list[str]:
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _is(value: str, keyword: str) -> bool:
    return value.casefold() == keyword.casefold()
