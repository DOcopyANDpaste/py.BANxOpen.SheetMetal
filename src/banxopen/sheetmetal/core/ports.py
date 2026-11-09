"""The seams between the pure sheet metal rules and wherever their data lives (NX, the file system)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from banxopen.foundation import BodyId
from banxopen.sheetmetal.model.beads import BeadSpecRow, BodyFeatureInventory, StandardInfo
from banxopen.sheetmetal.model.requirements import SheetMetalRequirement
from banxopen.sheetmetal.model.table import SheetMetalPreferenceRead


class BeadSpecSource(Protocol):
    """Where spec data lives. The real one is the standards file + Standard folders + Excel workbooks."""

    def list_standards(self) -> Sequence[StandardInfo]: ...

    def find_workbook(self, standard: StandardInfo, bead_spec_name: str) -> str | None:
        """The workbook holding `bead_spec_name`'s SPECs, or None when the Standard has none for it."""
        ...

    def list_workbooks(self, standard: StandardInfo) -> Sequence[str]:
        """Every bead SPEC workbook the Standard holds."""
        ...

    def read_workbook(self, standard: StandardInfo, workbook_path: str) -> Sequence[BeadSpecRow]: ...


class BeadSpecLookup(Protocol):
    """Resolves SPECs for the constraint provider and for identifying unstamped beads."""

    def find_all(self, standard_id: str, spec_id: str) -> Sequence[BeadSpecRow]:
        """Every row of this Standard's SPEC family, ordered by thickness. Empty when the family cannot be found (the
        SPEC was retired, the Standard left the standards file, the data could not be read, or the id spans two
        workbooks). Callers treat empty as "unverifiable", never "unrestricted"."""
        ...

    def all_specs(self) -> Sequence[BeadSpecRow]:
        """Every row of every bead SPEC workbook in every Standard: an unstamped bead records no Standard or SPEC."""
        ...


class FeatureInventory(Protocol):
    """What sheet metal features are already on a body. Named for the domain: a louver or joggle inventory implements
    the same shape."""

    def read(self, body_id: BodyId) -> BodyFeatureInventory: ...


class SheetMetalPreferenceReader(Protocol):
    def read_for(self, body_id: BodyId) -> SheetMetalPreferenceRead: ...


class SheetMetalRequirementReader(Protocol):
    """One seam for the material engine's constraint, the row choice and the feature tools, so they all judge by the
    same read."""

    def read_all(self) -> tuple[Sequence[SheetMetalRequirement], Sequence[str]]:
        """Every sheet metal requirement in the work part, and what could not be read."""
        ...
