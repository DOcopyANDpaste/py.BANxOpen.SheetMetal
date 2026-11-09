"""Tunable bead behaviour, from bead-settings.json, and the SPEC workbook's column vocabulary."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Final

from banxopen.sheetmetal.core.errors import SpecDataError
from banxopen.sheetmetal.model.beads import BeadFeatureParameter, BeadParameterMapping, BeadSpecRow
from banxopen.sheetmetal.model.thickness import TOLERANCE_INCHES
from banxopen.sheetmetal.text import SheetMetalText


class BeadSpecColumns:
    """The SPEC workbook's driving numeric columns, by the canonical header names the parser recognises."""

    RADIUS_AND_RAD_S: Final = "R&RADS"
    WIDTH: Final = "W"
    HEIGHT: Final = "H"
    DIE_RADIUS_P: Final = "RADP"
    THICKNESS: Final = "THICKNESS"
    ALL: Final = (RADIUS_AND_RAD_S, WIDTH, HEIGHT, DIE_RADIUS_P, THICKNESS)

    @staticmethod
    def normalize(column: str) -> str:
        """Case and spaces ignored, so "Rad P" and "RADP" are the same column."""
        return column.replace(" ", "").upper()

    @staticmethod
    def is_known(column: str) -> bool:
        return BeadSpecColumns.normalize(column) in BeadSpecColumns.ALL

    @staticmethod
    def value_of(row: BeadSpecRow, column: str) -> float:
        match BeadSpecColumns.normalize(column):
            case BeadSpecColumns.RADIUS_AND_RAD_S:
                return row.radius_and_rad_s
            case BeadSpecColumns.WIDTH:
                return row.width
            case BeadSpecColumns.HEIGHT:
                return row.height
            case BeadSpecColumns.DIE_RADIUS_P:
                return row.die_radius_p
            case BeadSpecColumns.THICKNESS:
                return row.thickness
            case _:
                raise ValueError(f"'{column}' is not a SPEC workbook column. Known: {', '.join(BeadSpecColumns.ALL)}.")


_WRITTEN_PARAMETERS: Final = (BeadFeatureParameter.HEIGHT, BeadFeatureParameter.RADIUS, BeadFeatureParameter.DIE_RADIUS)
"""Parameters the tool writes into every bead, so each must have a column."""


class BeadSettings:
    """`geometry_match_tolerance`: how far an unstamped bead's mapped values may each differ from a SPEC row and still
    be identified as it, in the workbook's units. `parameter_mapping` is used both to write a SPEC into a bead and to
    identify an unstamped bead, so the two never disagree about which column drives which parameter.

    File shape: `{"geometryMatchTolerance": 0.0005, "parameterMapping": [{"feature": "Height", "specColumn": "H"}]}`.
    """

    DEFAULT_GEOMETRY_MATCH_TOLERANCE: Final = TOLERANCE_INCHES
    """Matches the thickness tolerance, so a bead the tool built itself is always recognised."""
    DEFAULT_PARAMETER_MAPPING: Final = (
        BeadParameterMapping(BeadFeatureParameter.HEIGHT, BeadSpecColumns.HEIGHT),
        BeadParameterMapping(BeadFeatureParameter.RADIUS, BeadSpecColumns.RADIUS_AND_RAD_S),
        BeadParameterMapping(BeadFeatureParameter.DIE_RADIUS, BeadSpecColumns.DIE_RADIUS_P),
        BeadParameterMapping(BeadFeatureParameter.THICKNESS, BeadSpecColumns.THICKNESS),
    )
    DEFAULT: ClassVar[BeadSettings]

    def __init__(
        self,
        geometry_match_tolerance: float,
        parameter_mapping: Sequence[BeadParameterMapping],
        text: SheetMetalText | None = None,
    ) -> None:
        """Raises ValueError, worded by `text`, for a non-positive tolerance or an invalid mapping."""
        text = text or SheetMetalText.DEFAULT
        if not geometry_match_tolerance > 0:
            raise ValueError(text.bead_tolerance_not_positive)
        _validate(parameter_mapping, text)
        self.geometry_match_tolerance = geometry_match_tolerance
        self.parameter_mapping = tuple(parameter_mapping)

    def spec_value_for(self, spec: BeadSpecRow, parameter: BeadFeatureParameter) -> float:
        mapping = next((m for m in self.parameter_mapping if m.feature is parameter), None)
        if mapping is None:
            raise LookupError(f"No SPEC column is mapped to bead parameter {parameter.value}.")
        return BeadSpecColumns.value_of(spec, mapping.spec_column)

    @staticmethod
    def load(path: str | Path, text: SheetMetalText | None = None) -> BeadSettings:
        """A missing file yields `DEFAULT`, and an omitted section its default: these are tuning values with safe
        defaults. A file that is present but invalid raises `SpecDataError`, since silently ignoring an edit made on
        purpose is worse than refusing to start. Property names match case-insensitively, as Newtonsoft did."""
        text = text or SheetMetalText.DEFAULT
        file = Path(path)
        if not file.is_file():
            return BeadSettings.DEFAULT

        try:
            document: Any = json.loads(file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as ex:
            raise SpecDataError(text.bead_settings_invalid_json(str(path), str(ex))) from ex
        if document is None:
            raise SpecDataError(text.bead_settings_empty(str(path)))
        if not isinstance(document, dict):
            raise SpecDataError(text.bead_settings_invalid_json(str(path), "the top level is not an object"))

        entries = _upper_keys(document)  # pyright: ignore[reportUnknownArgumentType]
        try:
            raw_mapping = entries.get("PARAMETERMAPPING")
            mapping = (
                BeadSettings.DEFAULT_PARAMETER_MAPPING
                if raw_mapping is None
                else tuple(_parse_entry(e, text) for e in _as_list(raw_mapping))
            )
            tolerance = entries.get("GEOMETRYMATCHTOLERANCE")
            return BeadSettings(
                BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE if tolerance is None else float(tolerance),
                mapping,
                text,
            )
        except (ValueError, TypeError) as ex:
            raise SpecDataError(text.bead_settings_invalid(str(path), str(ex))) from ex


def _upper_keys(document: dict[Any, Any]) -> dict[str, Any]:
    return {str(k).upper(): v for k, v in document.items()}


def _as_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError("parameterMapping is not a list.")
    return list(value)  # pyright: ignore[reportUnknownArgumentType]


def _parse_entry(entry: Any, text: SheetMetalText) -> BeadParameterMapping:
    fields = _upper_keys(entry) if isinstance(entry, dict) else {}  # pyright: ignore[reportUnknownArgumentType]
    feature_name = fields.get("FEATURE")
    feature = next(
        (p for p in BeadFeatureParameter if isinstance(feature_name, str) and p.value.upper() == feature_name.upper()),
        None,
    )
    if feature is None:
        raise SpecDataError(
            text.mapping_feature_unknown(
                None if feature_name is None else str(feature_name), (p.value for p in BeadFeatureParameter)
            )
        )
    column = fields.get("SPECCOLUMN")
    return BeadParameterMapping(feature, "" if column is None else str(column))


def _validate(mapping: Sequence[BeadParameterMapping], text: SheetMetalText) -> None:
    for entry in mapping:
        if not BeadSpecColumns.is_known(entry.spec_column):
            raise ValueError(text.mapping_column_unknown(entry.spec_column, entry.feature.value, BeadSpecColumns.ALL))

    seen: list[BeadFeatureParameter] = []
    duplicated: list[str] = []
    for entry in mapping:
        if entry.feature in seen and entry.feature.value not in duplicated:
            duplicated.append(entry.feature.value)
        seen.append(entry.feature)
    if duplicated:
        raise ValueError(text.mapping_duplicated(duplicated))

    missing = [p.value for p in _WRITTEN_PARAMETERS if p not in seen]
    if missing:
        raise ValueError(text.mapping_missing(missing))


BeadSettings.DEFAULT = BeadSettings(
    BeadSettings.DEFAULT_GEOMETRY_MATCH_TOLERANCE, BeadSettings.DEFAULT_PARAMETER_MAPPING
)
