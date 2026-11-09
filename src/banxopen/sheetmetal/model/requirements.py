"""What a SPEC-driven feature requires of the sheet it sits in, as held in the part."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from banxopen.sheetmetal.model.beads import BeadSpecRow
from banxopen.sheetmetal.model.thickness import format_key_thickness, format_thickness, thickness_matches

PAIR_SEPARATOR: Final = "@"


@dataclass(frozen=True, slots=True)
class MaterialThicknessPair:
    """One sheet a SPEC-driven feature allows: a grade at a thickness."""

    grade: str
    thickness: float

    @property
    def key(self) -> str:
        """As held in the part's requirement, "2024-O@0.0400": four decimals, as the NX pair formula writes it."""
        return f"{self.grade}{PAIR_SEPARATOR}{format_key_thickness(self.thickness)}"

    @staticmethod
    def parse_key(key: str) -> MaterialThicknessPair | None:
        at = key.rfind(PAIR_SEPARATOR)
        if at <= 0:
            return None
        try:
            return MaterialThicknessPair(key[:at], float(key[at + 1 :]))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class SheetMetalAllowance:
    """Every (grade, thickness) pair a feature's SPEC family marks YES, and the thickness it was actually built at.
    The pairs cover the whole family, so a sheet moved to another of its thicknesses with an allowed grade still passes;
    the feature's driving values hold the built row's, so `built_thickness` says when it needs re-applying."""

    built_thickness: float
    pairs: tuple[MaterialThicknessPair, ...]

    @staticmethod
    def from_family(family: Iterable[BeadSpecRow], built_thickness: float) -> SheetMetalAllowance:
        """The pairs the rows mark YES, in thickness then workbook column order."""
        return SheetMetalAllowance(
            built_thickness,
            tuple(
                MaterialThicknessPair(grade, row.thickness)
                for row in sorted(family, key=lambda r: r.thickness)
                for grade in row.allowed_grades
            ),
        )

    @staticmethod
    def at_built_thickness(built_thickness: float, grades: Iterable[str]) -> SheetMetalAllowance:
        """Only `grades` at the built thickness: what a requirement written before pairs held."""
        return SheetMetalAllowance(built_thickness, tuple(MaterialThicknessPair(g, built_thickness) for g in grades))

    def is_satisfied_by(self, thickness: float, grade: str) -> bool:
        """Grades compare ignoring case."""
        folded = grade.casefold()
        return any(thickness_matches(p.thickness, thickness) and p.grade.casefold() == folded for p in self.pairs)

    def allows_thickness(self, thickness: float) -> bool:
        return any(thickness_matches(p.thickness, thickness) for p in self.pairs)

    def grades_at(self, thickness: float) -> tuple[str, ...]:
        return tuple(p.grade for p in self.pairs if thickness_matches(p.thickness, thickness))

    def matches_built_thickness(self, thickness: float) -> bool:
        return thickness_matches(thickness, self.built_thickness)

    @property
    def thickness_text(self) -> str:
        return format_thickness(self.built_thickness)

    @property
    def pairs_text(self) -> str:
        """ "2024-O, 5052-O at 0.04 in; 2024-O at 0.05 in"."""
        if not self.pairs:
            return "no material"
        groups: dict[str, list[str]] = {}
        for pair in self.pairs:
            groups.setdefault(format_thickness(pair.thickness), []).append(pair.grade)
        return "; ".join(f"{', '.join(grades)} at {thickness} in" for thickness, grades in groups.items())

    def same_pairs_as(self, other: SheetMetalAllowance) -> bool:
        """Same pairs, ignoring order and grade case."""
        return {p.key.casefold() for p in self.pairs} == {p.key.casefold() for p in other.pairs}


@dataclass(frozen=True, slots=True)
class RequirementUser:
    """A feature that relies on a requirement: `feature_key` is its tag, `label` how the user knows it."""

    feature_key: str
    label: str


_STANDARD_SUFFIX: Final = "_Standards"


@dataclass(frozen=True, slots=True)
class SheetMetalRequirement:
    """One SPEC's requirement on the sheet: every feature built to the SPEC shares it. Held in the part as native NX
    Requirements and read back by every tool that judges the sheet metal.

    `domain` is the feature domain that imposed it ("SHEETMETAL.BEAD"), `feature_noun` how messages name the source
    ("Bead SPEC"), `source` the SPEC workbook it was generated from (empty when unknown).
    """

    domain: str
    feature_noun: str
    standard_id: str
    spec_id: str
    allowance: SheetMetalAllowance
    users: tuple[RequirementUser, ...]
    source: str

    @property
    def key(self) -> str:
        return SheetMetalRequirement.key_for(self.standard_id, self.spec_id)

    @property
    def label(self) -> str:
        return (
            f"{self.feature_noun} {self.spec_id} (Standard {SheetMetalRequirement.short_standard_of(self.standard_id)})"
        )

    @property
    def is_in_use(self) -> bool:
        """Whether a live feature still relies on it. One no feature uses guards nothing, and the next refresh deletes
        it."""
        return bool(self.users)

    @staticmethod
    def key_for(standard_id: str, spec_id: str) -> str:
        """The Standard is shortened, so a key built from a bead's full Standard id matches one read back."""
        return f"{SheetMetalRequirement.short_standard_of(standard_id)}|{spec_id}"

    @staticmethod
    def short_standard_of(standard_id: str) -> str:
        """ "Bombardier_B_Standards" -> "Bombardier_B"."""
        if standard_id.casefold().endswith(_STANDARD_SUFFIX.casefold()):
            return standard_id[: -len(_STANDARD_SUFFIX)]
        return standard_id
