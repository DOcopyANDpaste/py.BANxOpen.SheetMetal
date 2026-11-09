"""Stable codes the sheet metal libraries put on rule outcomes (`RuleOutcome.reason_code`) and failed operations
(`OperationResult.error_code`). Codes, not wording, are the contract: tools branch on these and show the message as-is.
The wording is `SheetMetalText`'s. Unreadable requirements use Foundation's `FoundationCodes.REQUIREMENTS_UNREADABLE`,
and a failed write `FoundationCodes.REQUIREMENT_WRITE_FAILED`."""

from __future__ import annotations

from typing import Final


class SheetMetalCodes:
    # ---- Bead SPEC validation ----
    THICKNESS_MISMATCH: Final = "THICKNESS_MISMATCH"
    """Args: spec, specThickness, sheetThickness."""
    MATERIAL_MISSING: Final = "MATERIAL_MISSING"
    MATERIAL_GRADE_UNRECOGNIZED: Final = "MATERIAL_GRADE_UNRECOGNIZED"
    """A grade the SPEC workbooks do not know (bead validation: args grade, standard), or a material with no row in the
    standards file to give it a grade (the bead constraint: args material, source)."""
    MATERIAL_NOT_ALLOWED: Final = "MATERIAL_NOT_ALLOWED"
    """Args: spec, grade."""

    # ---- Constraints on a body's material ----
    # The feature constraints (bead, requirement) are all WARNs: feature validation never blocks a preference update.
    # Only the preference basics (PREFERENCES_UNREADABLE, PREFERENCE_MATERIAL_NOT_IN_TABLE, ROW_NOT_OFFERED) block.
    MATERIAL_NOT_ALLOWED_BY_BEAD: Final = "MATERIAL_NOT_ALLOWED_BY_BEAD"
    """Args: spec, grade, material, source."""
    BEAD_SPEC_NOT_FOUND: Final = "BEAD_SPEC_NOT_FOUND"
    """Args: spec, standard, material, source."""
    BEAD_SPEC_UNMATCHED: Final = "BEAD_SPEC_UNMATCHED"
    """Args: feature, status (geometryUnreadable | ambiguous | noMatch), candidates, material, source."""
    BEAD_FEATURES_UNREADABLE: Final = "BEAD_FEATURES_UNREADABLE"
    """The beads on a body could not be read: by the bead constraint (args error, material, source) or when refreshing
    requirements."""
    REQUIREMENT_OUT_OF_DATE: Final = "REQUIREMENT_OUT_OF_DATE"
    """Args: requirement, builtThickness, workbookPairs, recordedPairs, material, source."""
    PREFERENCES_UNREADABLE: Final = "SHEETMETAL_PREFERENCES_UNREADABLE"
    """Args: error, material, source."""
    PREFERENCE_MATERIAL_NOT_IN_TABLE: Final = "PREFERENCE_MATERIAL_NOT_IN_TABLE"
    """A material with no row in the standards file, so the preferences cannot be set to it."""
    PREFERENCE_SHARED_BY_BODIES: Final = "PREFERENCE_SHARED_BY_BODIES"
    """Args: count, material, source."""
    MATERIAL_NOT_ALLOWED_BY_REQUIREMENT: Final = "MATERIAL_NOT_ALLOWED_BY_REQUIREMENT"
    """A WARN. Args: reason (thicknessNotAllowed | gradeNotAllowed), builtThickness, grades, allowedPairs, material,
    source."""

    # ---- The picked Sheet Metal Preferences row ----
    ROW_NOT_OFFERED: Final = "SHEETMETAL_ROW_NOT_OFFERED"
    """Args: row, material, standard (empty when no Standard is chosen)."""
    ROW_REFUSED_BY_REQUIREMENT: Final = "SHEETMETAL_ROW_REFUSED_BY_REQUIREMENT"
    """A WARN. Args: row, rowThickness, refusal (requirementsUnreadable | pairNotAllowed), and for pairNotAllowed
    requirement, grade, builtThickness, allowedPairs."""
    ROW_NEEDS_REAPPLY: Final = "SHEETMETAL_ROW_NEEDS_REAPPLY"
    """A WARN: the row is allowed, but the features were built at another thickness. Args: row, rowThickness,
    requirement, builtThickness."""
    ROW_THICKNESS_MISMATCH: Final = "SHEETMETAL_ROW_THICKNESS_MISMATCH"
    """Args: row, rowThickness, bodyThickness."""

    # ---- Services ----
    CONFIG_UNAVAILABLE: Final = "SHEETMETAL_CONFIG_UNAVAILABLE"
    NOT_SHEET_METAL: Final = "NOT_SHEETMETAL"
    THICKNESS_READ_FAILED: Final = "THICKNESS_READ_FAILED"
    PREFERENCE_SYNC_NOT_APPLIED: Final = "PREFERENCE_SYNC_NOT_APPLIED"
    PREFERENCE_SET_FAILED: Final = "PREFERENCE_SET_FAILED"
    THICKNESS_EXPRESSION_MISSING: Final = "THICKNESS_EXPRESSION_MISSING"
    GRADE_EXPRESSION_FAILED: Final = "GRADE_EXPRESSION_FAILED"
    GRADE_ATTRIBUTE_FAILED: Final = "GRADE_ATTRIBUTE_FAILED"
    REQUIREMENT_REFRESH_FAILED: Final = "REQUIREMENT_REFRESH_FAILED"
    BEAD_EXPRESSIONS_FAILED: Final = "BEAD_EXPRESSIONS_FAILED"
    BEAD_VALIDATION_FAILED: Final = "BEAD_VALIDATION_FAILED"
    BEAD_CREATE_FAILED: Final = "BEAD_CREATE_FAILED"
    BEAD_BUILDER_CREATE_FAILED: Final = "BEAD_BUILDER_CREATE_FAILED"
    BEAD_PREVIEW_FAILED: Final = "BEAD_PREVIEW_FAILED"
    BEAD_APPLY_BLOCKED: Final = "BEAD_APPLY_BLOCKED"
    """The bead tool refused Apply; the message is the blocking reason the view already shows."""
