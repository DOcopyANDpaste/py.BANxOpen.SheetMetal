"""Where the sheet metal configuration lives, the tool-wide settings, and the Sheet Metal Preferences check.

    %UGII_USER_DIR% | %UGII_GROUP_DIR% | %UGII_SITE_DIR%
      SHEETMETAL\\                        sheet metal root: NX's material standards file lives here
        BA_NXOPEN_settings\\
          sheetmetal-settings.json       names the standards file (file name only)
          bead-settings.json
    %TEMP%\\BANxOpen.SheetMetal\\cache\\    generated, so kept out of the shared UGII directories
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Final

from banxopen.sheetmetal.core.errors import SpecDataError
from banxopen.sheetmetal.model.table import (
    SheetMetalPartPreference,
    SheetMetalPreferenceCheckResult,
    SheetMetalPreferenceStatus,
)
from banxopen.sheetmetal.model.thickness import TOLERANCE_INCHES, format_thickness, thickness_matches
from banxopen.sheetmetal.text import SheetMetalText


class SheetMetalSettings:
    """sheetmetal-settings.json: `{"materialTable": "sheet_metal_material_table.xml"}`. Almost everything is decided by
    NX's standards file; this only says which file it is."""

    DEFAULT: ClassVar[SheetMetalSettings]

    def __init__(self, material_table: str | None) -> None:
        self.material_table = material_table
        """File name of the standards file in the sheet metal root; None when not set."""

    @staticmethod
    def load(path: str | Path, text: SheetMetalText | None = None) -> SheetMetalSettings:
        """A missing file yields `DEFAULT`. A file that is present but invalid raises `SpecDataError`."""
        text = text or SheetMetalText.DEFAULT
        file = Path(path)
        if not file.is_file():
            return SheetMetalSettings.DEFAULT
        try:
            document: Any = json.loads(file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as ex:
            raise SpecDataError(text.settings_invalid_json(str(path), str(ex))) from ex
        if document is None:
            raise SpecDataError(text.settings_empty(str(path)))
        if not isinstance(document, dict):
            raise SpecDataError(text.settings_invalid_json(str(path), "the top level is not an object"))
        entries: dict[str, Any] = {str(k).upper(): v for k, v in document.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        table = entries.get("MATERIALTABLE")
        if table is None or not str(table).strip():
            return SheetMetalSettings.DEFAULT
        return SheetMetalSettings(str(table).strip())


SheetMetalSettings.DEFAULT = SheetMetalSettings(None)


class SheetMetalConfigLocator:
    """Finds the sheet metal root and everything read from it. Resolved rather than passed in, because more than one
    entry point reads it: two tools hardcoding their own path is how they end up reading two copies.

    The root is SHEETMETAL under UGII_USER_DIR, then UGII_GROUP_DIR, then UGII_SITE_DIR (NX's own precedence). A
    SHEETMETAL folder only counts if it holds BA_NXOPEN_settings/sheetmetal-settings.json.
    """

    ROOT_FOLDER_NAME: Final = "SHEETMETAL"
    SETTINGS_FOLDER_NAME: Final = "BA_NXOPEN_settings"
    SEARCH_VARIABLES: Final = ("UGII_USER_DIR", "UGII_GROUP_DIR", "UGII_SITE_DIR")
    _SETTINGS_FILE_NAME: Final = "sheetmetal-settings.json"
    _APPLICATION_FOLDER_NAME: Final = "BANxOpen.SheetMetal"

    def __init__(self, root_directory: str | Path | None = None, text: SheetMetalText | None = None) -> None:
        """A given root is trusted as given; otherwise it is located on first use."""
        self._root = Path(root_directory) if root_directory is not None else None
        self._text = text or SheetMetalText.DEFAULT

    @property
    def root_directory(self) -> Path:
        """Raises FileNotFoundError naming everywhere it looked."""
        if self._root is None:
            self._root = self._locate()
        return self._root

    @property
    def settings_directory(self) -> Path:
        return self.root_directory / self.SETTINGS_FOLDER_NAME

    @property
    def settings_path(self) -> Path:
        return self.settings_directory / self._SETTINGS_FILE_NAME

    @property
    def bead_settings_path(self) -> Path:
        """Optional: a missing file means default bead settings."""
        return self.settings_directory / "bead-settings.json"

    @property
    def material_table_path(self) -> Path:
        """The file `materialTable` names, in the root. Raises FileNotFoundError when it is not set or missing."""
        file_name = SheetMetalSettings.load(self.settings_path, self._text).material_table
        if file_name is None:
            raise FileNotFoundError(self._text.material_table_not_set(str(self.settings_path)))
        path = self.root_directory / file_name
        if not path.is_file():
            raise FileNotFoundError(self._text.material_table_file_missing(str(self.settings_path), str(path)))
        return path

    @property
    def cache_directory(self) -> Path:
        """%TEMP%/BANxOpen.SheetMetal/cache: generated, so per machine rather than in a shared root. Created on
        demand."""
        temp = os.environ.get("TEMP")
        base = Path(temp) if temp and temp.strip() else Path(tempfile.gettempdir())
        cache = base / self._APPLICATION_FOLDER_NAME / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        return cache

    def _locate(self) -> Path:
        attempted: list[str] = []
        for variable in self.SEARCH_VARIABLES:
            directory = os.environ.get(variable)
            if directory is None or not directory.strip():
                attempted.append(f"{variable} (not set)")
                continue
            candidate = Path(directory) / self.ROOT_FOLDER_NAME
            attempted.append(str(candidate))
            if (candidate / self.SETTINGS_FOLDER_NAME / self._SETTINGS_FILE_NAME).is_file():
                return candidate
        settings_file = str(Path(self.ROOT_FOLDER_NAME) / self.SETTINGS_FOLDER_NAME / self._SETTINGS_FILE_NAME)
        raise FileNotFoundError(self._text.config_directory_not_found(settings_file, attempted))


PREFERENCE_THICKNESS_TOLERANCE: Final = TOLERANCE_INCHES
"""The SPEC thickness tolerance, so a thickness the preferences accept is one a SPEC accepts too."""


def check_preference(
    body_physical_material: str | None,
    body_thickness: float,
    preference: SheetMetalPartPreference,
    text: SheetMetalText | None = None,
) -> SheetMetalPreferenceCheckResult:
    """A sheet metal body against its part's Sheet Metal Preferences. Material before thickness: NX fills the thickness
    from the material's row. A body with no physical material is not checked against the row's material, since
    assigning one is part of applying a picked row."""
    text = text or SheetMetalText.DEFAULT
    if not preference.is_material_table_entry or not (preference.material_name or "").strip():
        return SheetMetalPreferenceCheckResult(
            SheetMetalPreferenceStatus.MATERIAL_NOT_SET,
            text.preferences_have_no_material
            if preference.is_material_table_entry
            else text.preferences_not_material_table,
        )

    row = preference.row
    if row is None:
        return SheetMetalPreferenceCheckResult(
            SheetMetalPreferenceStatus.MATERIAL_NOT_IN_TABLE,
            text.preference_material_not_in_table(preference.material_name or ""),
        )

    if (
        body_physical_material is not None
        and body_physical_material.strip()
        and row.physical_material_name.casefold() != body_physical_material.casefold()
    ):
        return SheetMetalPreferenceCheckResult(
            SheetMetalPreferenceStatus.MATERIAL_OUT_OF_SYNC,
            text.preference_material_out_of_sync(row.name, row.physical_material_name, body_physical_material),
        )

    if not thickness_matches(body_thickness, preference.thickness):
        return SheetMetalPreferenceCheckResult(
            SheetMetalPreferenceStatus.THICKNESS_MISMATCH,
            text.preference_thickness_mismatch(
                format_thickness(body_thickness), format_thickness(preference.thickness)
            ),
        )

    return SheetMetalPreferenceCheckResult.IN_SYNC
