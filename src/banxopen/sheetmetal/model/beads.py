"""Bead SPECs, the beads found on a body, and what a selection traces back to."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from banxopen.sheetmetal.model.thickness import format_thickness, thickness_matches


class BeadFeatureParameter(Enum):
    """A value that exists on a bead in the model. HEIGHT, RADIUS and DIE_RADIUS are bead builder parameters the tool
    writes; THICKNESS belongs to the sheet the bead sits on, so it is compared when identifying a bead, never
    written."""

    HEIGHT = "Height"
    RADIUS = "Radius"
    DIE_RADIUS = "DieRadius"
    THICKNESS = "Thickness"


@dataclass(frozen=True, slots=True)
class BeadParameterMapping:
    """Which SPEC column supplies one bead feature parameter."""

    feature: BeadFeatureParameter
    spec_column: str


@dataclass(frozen=True, slots=True)
class BeadSpecRow:
    """One row of a Standard's SPEC workbook: the driving parameters of one bead SPEC at one sheet thickness, plus which
    material grades it is allowed on.

    `workbook_name` is the file stem of the workbook the row was read from. A Standard holds one workbook per bead
    SPEC, so it says which of them a row belongs to. It is provenance, not an identifier the business owns.

    `allowed_material_grades` maps each "Allowed Material" column header to whether the row marks it YES, in workbook
    column order. Keys compare exactly, as the C# dictionary did.
    """

    standard_id: str
    workbook_name: str
    spec_id: str
    radius_and_rad_s: float
    width: float
    height: float
    die_radius_p: float
    thickness: float
    allowed_material_grades: Mapping[str, bool] = field(default_factory=dict[str, bool])

    def __hash__(self) -> int:
        return hash((self.standard_id, self.workbook_name, self.spec_id, self.thickness))

    def is_allowed_for(self, grade: str) -> bool:
        """True only when the grade is a column of the workbook AND marked YES. `MaterialAllowedRule` tells an unknown
        column from a "-" so its message can say which."""
        return self.allowed_material_grades.get(grade, False)

    @property
    def allowed_grades(self) -> tuple[str, ...]:
        return tuple(grade for grade, allowed in self.allowed_material_grades.items() if allowed)


@dataclass(frozen=True, slots=True)
class BeadGeometry:
    """The measurable shape of an existing bead, read back off the model: the values the tool writes into a bead
    feature, plus the thickness of the sheet it sits on. Width is absent on purpose: the circular cross section has no
    width parameter, so a SPEC's width cannot be recovered from the feature. In the SPEC workbook's units."""

    thickness: float
    height: float
    radius: float
    die_radius: float

    def value_of(self, parameter: BeadFeatureParameter) -> float:
        match parameter:
            case BeadFeatureParameter.THICKNESS:
                return self.thickness
            case BeadFeatureParameter.HEIGHT:
                return self.height
            case BeadFeatureParameter.RADIUS:
                return self.radius
            case BeadFeatureParameter.DIE_RADIUS:
                return self.die_radius


@dataclass(frozen=True, slots=True)
class BeadSpecMatch:
    """The SPEC rows a bead's geometry is consistent with."""

    candidates: tuple[BeadSpecRow, ...]

    @property
    def single(self) -> BeadSpecRow | None:
        """The one SPEC the bead can be identified as, or None when there is none or more than one."""
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def is_none(self) -> bool:
        return not self.candidates


@dataclass(frozen=True, slots=True)
class BeadTracebackResult:
    """What reading a selection's stamp turned up. `found` False means either no bead at all (create from scratch) or a
    bead with no stamp; `has_unstamped_feature` tells those apart. `is_pattern_instance`: the selection resolved to a
    pattern member, whose stamp was read off the pattern's stamped original. `bead_spec` is None for a bead stamped
    before the bead SPEC name was recorded; that is never a reason to treat it as unstamped."""

    found: bool
    has_unstamped_feature: bool
    standard_id: str | None = None
    spec_id: str | None = None
    created_utc: datetime | None = None
    is_pattern_instance: bool = False
    bead_spec: str | None = None


NO_BEAD = BeadTracebackResult(found=False, has_unstamped_feature=False)
UNSTAMPED_BEAD = BeadTracebackResult(found=False, has_unstamped_feature=True)


@dataclass(frozen=True, slots=True)
class CurveEnds:
    """One curve as the chain grouper sees it: an id the caller maps back to its own object, and its two end points."""

    id: int
    start: tuple[float, float, float]
    end: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class StandardInfo:
    """One Standard of the sheet metal material standards file. `id` is the Standard as written in the table and is
    stamped onto beads built to it. `bead_spec_folder` holds its bead SPEC workbooks; it need not exist."""

    id: str
    display_name: str
    bead_spec_folder: str


# ---- What is on a body ----


@dataclass(frozen=True, slots=True)
class FeatureSpecStamp:
    """One feature found on a body, identified by the Standard and SPEC it was built to. `feature_key` is its NX tag,
    `name` its feature name for messages."""

    standard_id: str
    spec_id: str
    feature_key: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class UnstampedFeature:
    """A bead on the body with no SPEC stamp, usually built by hand in NX. `geometry` is None when it could not be
    read."""

    name: str
    geometry: BeadGeometry | None
    feature_key: str = ""


@dataclass(frozen=True, slots=True)
class BodyFeatureInventory:
    """What sheet metal features a body carries, as far as the tool can tell. `read_error` is set when they could not be
    read at all: "could not look" must not be mistaken for "nothing there", or every restriction silently lifts."""

    stamps: tuple[FeatureSpecStamp, ...] = ()
    unstamped_features: tuple[UnstampedFeature, ...] = ()
    read_error: str | None = None

    @staticmethod
    def unreadable(error: str) -> BodyFeatureInventory:
        return BodyFeatureInventory((), (), error)

    @property
    def is_empty(self) -> bool:
        return self.read_error is None and not self.stamps and not self.unstamped_features


EMPTY_INVENTORY = BodyFeatureInventory()


class BeadSpecStatus(Enum):
    """How a bead's SPEC is known, or why it is not."""

    STAMPED = "Stamped"
    """Stamped by the tool, and the SPEC is in its workbook."""
    STAMPED_SPEC_MISSING = "StampedSpecMissing"
    """Stamped, but the SPEC is no longer in its Standard's workbooks."""
    MATCHED = "Matched"
    """Not stamped; its geometry matches exactly one SPEC."""
    GEOMETRY_UNREADABLE = "GeometryUnreadable"
    AMBIGUOUS = "Ambiguous"
    """Not stamped; its geometry matches several SPECs."""
    UNMATCHED = "Unmatched"
    """Not stamped; its geometry matches no SPEC in any Standard."""


@dataclass(frozen=True, slots=True)
class BeadOnBody:
    """One bead on a body and the SPEC it is built to.

    `spec_family` is every row the bead's SPEC id names, ordered by thickness: one per sheet thickness the SPEC is
    driven for. The family rather than one row, because which row applies depends on the thickness of the sheet, and
    NX re-thicknesses the sheet from the preferences and rebuilds every bead into it. Empty unless the status is
    STAMPED or MATCHED. `candidates` are the SPECs an ambiguous bead matches.
    """

    feature_key: str
    name: str
    status: BeadSpecStatus
    standard_id: str | None
    spec_id: str | None
    spec_family: tuple[BeadSpecRow, ...] = ()
    candidates: tuple[BeadSpecRow, ...] = ()

    @property
    def is_stamped(self) -> bool:
        return self.status in (BeadSpecStatus.STAMPED, BeadSpecStatus.STAMPED_SPEC_MISSING)

    @property
    def is_spec_known(self) -> bool:
        """Whether this bead's SPEC is known well enough to restrict anything."""
        return bool(self.spec_family)

    def spec_at(self, thickness: float) -> BeadSpecRow | None:
        """The family's row driven for a sheet of `thickness`, or None when the SPEC is not driven for it: the bead
        cannot be rebuilt into such a sheet."""
        return next((row for row in self.spec_family if thickness_matches(row.thickness, thickness)), None)

    @property
    def thickness_coverage(self) -> str:
        """The thicknesses the SPEC is driven for, "0.02 - 0.063" or one value. Empty when the SPEC is not known."""
        if not self.spec_family:
            return ""
        first = format_thickness(self.spec_family[0].thickness)
        if len(self.spec_family) == 1:
            return first
        return f"{first} - {format_thickness(self.spec_family[-1].thickness)}"
