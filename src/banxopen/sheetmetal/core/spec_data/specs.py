"""Where each Standard's bead SPEC workbooks live, the cache in front of them, and the lookups the rules use.

There is no mapping file: every Standard has a folder beside the standards file, named exactly as its Standard column:

    <folder holding the standards file>/
      sheet_metal_material_table.csv
      XX_Standard/Features/BEAD/<bead SPEC workbook>.xlsx

Adding a Standard is a new table row and a new folder, never a code or config change.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final

from banxopen.sheetmetal.core.errors import SpecDataError, is_spec_data_failure
from banxopen.sheetmetal.core.materials.table import SheetMetalMaterialTable
from banxopen.sheetmetal.core.spec_data.workbook import BeadSpecWorkbookParser
from banxopen.sheetmetal.model.beads import BeadSpecRow, StandardInfo
from banxopen.sheetmetal.text import SheetMetalText

_log = logging.getLogger(__name__)

FEATURES_FOLDER_NAME: Final = "Features"
BEAD_FOLDER_NAME: Final = "BEAD"
_EXCEL_LOCK_FILE_PREFIX: Final = "~$"
_INVALID_FILE_NAME_CHARS: Final = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

Warn = Callable[[str], None]


def list_standards(table: SheetMetalMaterialTable, text: SheetMetalText | None = None) -> tuple[StandardInfo, ...]:
    """Every Standard in the table, in file order. Raises RuntimeError for a table not read from a file, and
    `SpecDataError` for a Standard that cannot be a folder name."""
    text = text or SheetMetalText.DEFAULT
    if table.source_path is None:
        raise RuntimeError(text.standards_not_from_file)
    root = Path(table.source_path).resolve().parent
    return tuple(StandardInfo(s, s, str(bead_spec_folder(root, s, text))) for s in table.standards)


def bead_spec_folder(root_directory: str | Path, standard: str, text: SheetMetalText | None = None) -> Path:
    if _INVALID_FILE_NAME_CHARS.search(standard) or not standard.strip("."):
        raise SpecDataError((text or SheetMetalText.DEFAULT).standard_not_a_folder_name(standard))
    return Path(root_directory) / standard / FEATURES_FOLDER_NAME / BEAD_FOLDER_NAME


def list_bead_spec_workbooks(standard: StandardInfo) -> tuple[str, ...]:
    """Every bead SPEC workbook in the Standard's folder, by name. Empty when the folder is missing or holds none,
    which means no bead SPEC is allowed under that Standard."""
    folder = Path(standard.bead_spec_folder)
    if not folder.is_dir():
        return ()
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".xlsx" and not p.name.startswith(_EXCEL_LOCK_FILE_PREFIX)
    ]
    return tuple(str(p) for p in sorted(files, key=lambda p: str(p).casefold()))


def find_bead_workbook(standard: StandardInfo, bead_spec_name: str, text: SheetMetalText | None = None) -> str | None:
    """The one workbook whose name contains `bead_spec_name`, or None. "Contains" lets the business keep a revision
    suffix in the file name; a name matching two files is refused rather than resolved, since picking silently would
    build beads to the wrong SPEC. A file named exactly as the SPEC wins outright."""
    if not bead_spec_name.strip():
        return None
    workbooks = list_bead_spec_workbooks(standard)
    wanted = bead_spec_name.casefold()
    exact = next((w for w in workbooks if workbook_name_of(w).casefold() == wanted), None)
    if exact is not None:
        return exact
    matches = [w for w in workbooks if wanted in workbook_name_of(w).casefold()]
    if len(matches) > 1:
        raise SpecDataError(
            (text or SheetMetalText.DEFAULT).bead_spec_matches_several_workbooks(
                bead_spec_name, len(matches), standard.bead_spec_folder, (Path(m).name for m in matches)
            )
        )
    return matches[0] if matches else None


def workbook_name_of(workbook_path: str) -> str:
    """The name a workbook's rows are tagged with: its file stem."""
    return Path(workbook_path.replace("\\", "/")).stem


class FileSystemBeadSpecSource:
    """The real spec source: Standards from the standards file, workbooks from the Standard folders, rows from the
    workbook parser."""

    def __init__(
        self, table: SheetMetalMaterialTable, parser: BeadSpecWorkbookParser, text: SheetMetalText | None = None
    ) -> None:
        self._table = table
        self._parser = parser
        self._text = text

    def list_standards(self) -> Sequence[StandardInfo]:
        return list_standards(self._table, self._text)

    def find_workbook(self, standard: StandardInfo, bead_spec_name: str) -> str | None:
        return find_bead_workbook(standard, bead_spec_name, self._text)

    def list_workbooks(self, standard: StandardInfo) -> Sequence[str]:
        return list_bead_spec_workbooks(standard)

    def read_workbook(self, standard: StandardInfo, workbook_path: str) -> Sequence[BeadSpecRow]:
        with Path(workbook_path).open("rb") as stream:
            return self._parser.parse(standard.id, workbook_name_of(workbook_path), stream)


class BeadSpecCache:
    """Fronts a spec source with one JSON file per workbook, keyed by the workbook's path and its own modification
    time: Excel is only re-imported when the workbook is newer than the cache, or the Standard's folder now holds a
    different workbook. Keeps SPEC lookups fast without giving up "Excel is the editable source of truth"."""

    def __init__(self, source: Any, cache_directory: str | Path, on_warning: Warn | None = None) -> None:
        """`source` is a `BeadSpecSource`."""
        self._source = source
        self._cache_directory = Path(cache_directory)
        self._warn = on_warning or _log.warning
        self._cache_directory.mkdir(parents=True, exist_ok=True)

    def list_standards(self) -> Sequence[StandardInfo]:
        return self._source.list_standards()

    def find_workbook(self, standard: StandardInfo, bead_spec_name: str) -> str | None:
        """For a tool explaining why a Standard offers no SPECs."""
        return self._source.find_workbook(standard, bead_spec_name)

    def get_specs(self, standard: StandardInfo, bead_spec_name: str) -> tuple[BeadSpecRow, ...]:
        """Empty when the Standard has no workbook for this bead SPEC: no SPEC under it is allowed."""
        workbook = self._source.find_workbook(standard, bead_spec_name)
        return self._load_workbook(standard, workbook) if workbook is not None else ()

    def get_all_specs(self, standard: StandardInfo) -> tuple[BeadSpecRow, ...]:
        """Every row of every workbook the Standard holds, for identifying an unstamped bead. A workbook that cannot
        be read is skipped with a warning. SPEC ids shared across workbooks are warned about here, where both are in
        hand and can be named."""
        rows: list[BeadSpecRow] = []
        for workbook in self._source.list_workbooks(standard):
            try:
                rows.extend(self._load_workbook(standard, workbook))
            except Exception as ex:
                if not is_spec_data_failure(ex):
                    raise
                self._warn(f"Skipped bead SPEC workbook '{workbook}' of Standard '{standard.id}': {ex}")
        self._warn_on_duplicate_spec_ids(standard, rows)
        return tuple(rows)

    def _load_workbook(self, standard: StandardInfo, workbook: str) -> tuple[BeadSpecRow, ...]:
        write_time = Path(workbook).stat().st_mtime_ns
        cache_path = self._cache_path_for(standard.id, workbook)
        cached = _try_read_cache(cache_path)
        if (
            cached is not None
            and cached[1] == write_time
            and cached[0] is not None
            and cached[0].casefold() == workbook.casefold()
        ):
            return cached[2]
        rows = tuple(self._source.read_workbook(standard, workbook))
        _write_cache(cache_path, workbook, Path(workbook).stat().st_mtime_ns, rows)
        return rows

    def _warn_on_duplicate_spec_ids(self, standard: StandardInfo, rows: Sequence[BeadSpecRow]) -> None:
        by_spec: dict[str, list[BeadSpecRow]] = {}
        for row in rows:
            by_spec.setdefault(row.spec_id.casefold(), []).append(row)
        for group in by_spec.values():
            books = workbooks_of(group)
            if len(books) >= 2:
                self._warn(
                    f"Standard '{standard.id}' has SPEC '{group[0].spec_id}' in more than one bead SPEC workbook "
                    f"({', '.join(books)}). SPEC ids are expected to be unique within a Standard; a bead stamped "
                    "with this SPEC cannot be traced back to one workbook."
                )

    def _cache_path_for(self, standard_id: str, workbook: str) -> Path:
        """One cache file per workbook, so a Standard's several workbooks do not overwrite each other's cache."""
        return self._cache_directory / f"{_sanitize(standard_id)}__{_sanitize(workbook_name_of(workbook))}.json"


def workbooks_of(rows: Sequence[BeadSpecRow]) -> tuple[str, ...]:
    """The distinct workbooks rows come from. More than one for rows sharing a SPEC id is a collision a stamp cannot
    be traced through; repeats within one workbook are the family."""
    seen: dict[str, str] = {}
    for row in rows:
        seen.setdefault(row.workbook_name.casefold(), row.workbook_name)
    return tuple(seen.values())


class CachedBeadSpecLookup:
    """Looks SPECs up through the shared cache, so every tool reads the same parsed workbook data."""

    def __init__(self, cache: BeadSpecCache, on_warning: Warn | None = None) -> None:
        self._cache = cache
        self._warn = on_warning or _log.warning

    def find_all(self, standard_id: str, spec_id: str) -> Sequence[BeadSpecRow]:
        try:
            standard = next(
                (s for s in self._cache.list_standards() if s.id.casefold() == standard_id.casefold()), None
            )
            if standard is None:
                return ()
            matches = [r for r in self._cache.get_all_specs(standard) if r.spec_id.casefold() == spec_id.casefold()]
            # Two workbooks claiming one SPEC id is bad data, and either answer would be a guess; "not found" is
            # what a retired SPEC gets, and callers treat that as unverifiable.
            if len(workbooks_of(matches)) > 1:
                return ()
            return tuple(sorted(matches, key=lambda r: r.thickness))
        except Exception as ex:
            if not is_spec_data_failure(ex):
                raise
            # Inside an interactive dialog: unreadable spec data must not take it down. Safe only because the
            # provider turns "not found" into a warning that names it.
            self._warn(f"Could not read spec data while resolving Standard '{standard_id}' SPEC '{spec_id}': {ex}")
            return ()

    def all_specs(self) -> Sequence[BeadSpecRow]:
        try:
            standards = self._cache.list_standards()
        except Exception as ex:
            if not is_spec_data_failure(ex):
                raise
            self._warn(f"Could not list the Standards while identifying unstamped beads: {ex}")
            return ()
        rows: list[BeadSpecRow] = []
        for standard in standards:
            try:
                rows.extend(self._cache.get_all_specs(standard))
            except Exception as ex:
                if not is_spec_data_failure(ex):
                    raise
                self._warn(f"Skipped Standard '{standard.id}' while identifying unstamped beads: {ex}")
        return tuple(rows)


def _sanitize(name: str) -> str:
    return "_".join(part for part in _INVALID_FILE_NAME_CHARS.split(name) if part)


def _try_read_cache(path: Path) -> tuple[str | None, int, tuple[BeadSpecRow, ...]] | None:
    """A corrupt, half-written or older-format cache file just forces a re-import."""
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
        rows = tuple(
            BeadSpecRow(
                r["standard_id"],
                r["workbook_name"],
                r["spec_id"],
                float(r["radius_and_rad_s"]),
                float(r["width"]),
                float(r["height"]),
                float(r["die_radius_p"]),
                float(r["thickness"]),
                {str(k): bool(v) for k, v in r["grades"]},
            )
            for r in document["rows"]
        )
        return document.get("workbook_path"), int(document["write_time_ns"]), rows
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, workbook: str, write_time_ns: int, rows: Sequence[BeadSpecRow]) -> None:
    document = {
        "workbook_path": workbook,
        "write_time_ns": write_time_ns,
        "rows": [
            {
                "standard_id": r.standard_id,
                "workbook_name": r.workbook_name,
                "spec_id": r.spec_id,
                "radius_and_rad_s": r.radius_and_rad_s,
                "width": r.width,
                "height": r.height,
                "die_radius_p": r.die_radius_p,
                "thickness": r.thickness,
                "grades": list(r.allowed_material_grades.items()),  # a list keeps the column order
            }
            for r in rows
        ],
    }
    try:
        path.write_text(json.dumps(document), encoding="utf-8")
    except OSError as ex:
        _log.warning("Could not write the bead SPEC cache '%s': %s", path, ex)
