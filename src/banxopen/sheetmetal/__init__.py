"""BANxOpen SheetMetal: pure public API. Safe to import without NX.

Three audiences:
- tools: `BeadView`/`BeadControls` (rendered by the bead dialog, produced by `banxopen.sheetmetal.nx.BeadTool`) and
  `SheetMetalAssignmentSupport` (the Material Assignment dialog's sheet metal decisions);
- feature domains: the ports and rules a louver or joggle domain reuses;
- both: the standards file, preference and SPEC data types, `SheetMetalCodes`, `SheetMetalText`.

NX wiring is in `banxopen.sheetmetal.nx`.
"""

from banxopen.sheetmetal.codes import SheetMetalCodes
from banxopen.sheetmetal.core.beads.matching import group_curve_chains, match_bead_spec, resolve_beads_on_body
from banxopen.sheetmetal.core.beads.rules import (
    BeadSpecValidator,
    BeadValidationContext,
    BeadValidationResult,
    MaterialAllowedRule,
    ThicknessMatchRule,
    allowed_grades_at,
    grades_allowed_by_all,
)
from banxopen.sheetmetal.core.beads.settings import BeadSettings, BeadSpecColumns
from banxopen.sheetmetal.core.common.config import (
    PREFERENCE_THICKNESS_TOLERANCE,
    SheetMetalConfigLocator,
    SheetMetalSettings,
    check_preference,
)
from banxopen.sheetmetal.core.errors import SpecDataError
from banxopen.sheetmetal.core.materials.rules import (
    BEAD_DOMAIN,
    PREFERENCES_DOMAIN,
    BeadMaterialConstraintProvider,
    SheetMetalPreferenceConstraintProvider,
    SheetMetalRowGateRule,
    SyncSheetMetalPreferenceEffectRule,
)
from banxopen.sheetmetal.core.materials.table import (
    NO_FILTER,
    SheetMetalMaterialTable,
    SheetMetalRowFilter,
    SheetMetalStandardSelection,
    parse_material_table,
)
from banxopen.sheetmetal.core.ports import (
    BeadSpecLookup,
    BeadSpecSource,
    FeatureInventory,
    SheetMetalPreferenceReader,
    SheetMetalRequirementReader,
)
from banxopen.sheetmetal.core.requirements.requirements import (
    BEAD_FEATURE_NOUN,
    NOTHING_REBUILT,
    REQUIREMENT_DOMAIN,
    BeadRequirements,
    RefreshRequirementsEffectRule,
    SheetMetalRequirementConstraintProvider,
    SheetMetalRequirementNames,
    StoreRequirementReader,
    create_bead_requirements,
    requirement_to_specs,
    requirements_from_read,
)
from banxopen.sheetmetal.core.spec_data.specs import (
    BeadSpecCache,
    CachedBeadSpecLookup,
    FileSystemBeadSpecSource,
    find_bead_workbook,
    list_bead_spec_workbooks,
    list_standards,
    workbook_name_of,
)
from banxopen.sheetmetal.core.spec_data.workbook import BeadSpecWorkbookParser
from banxopen.sheetmetal.core.tools.assignment_support import (
    BodyCheck,
    PendingSheetMetal,
    SheetMetalAssignmentSupport,
    SyncAction,
)
from banxopen.sheetmetal.core.tools.bead_tool import BeadControls, BeadView, SpecPreview
from banxopen.sheetmetal.model.beads import (
    EMPTY_INVENTORY,
    NO_BEAD,
    UNSTAMPED_BEAD,
    BeadFeatureParameter,
    BeadGeometry,
    BeadOnBody,
    BeadParameterMapping,
    BeadSpecMatch,
    BeadSpecRow,
    BeadSpecStatus,
    BeadTracebackResult,
    BodyFeatureInventory,
    CurveEnds,
    FeatureSpecStamp,
    StandardInfo,
    UnstampedFeature,
)
from banxopen.sheetmetal.model.requirements import (
    MaterialThicknessPair,
    RequirementUser,
    SheetMetalAllowance,
    SheetMetalRequirement,
)
from banxopen.sheetmetal.model.table import (
    SheetMetalMaterialColumns,
    SheetMetalMaterialRow,
    SheetMetalPartPreference,
    SheetMetalPreferenceCheckResult,
    SheetMetalPreferenceRead,
    SheetMetalPreferenceStatus,
    SheetMetalProfile,
)
from banxopen.sheetmetal.model.thickness import TOLERANCE_INCHES, format_thickness, thickness_matches
from banxopen.sheetmetal.text import SheetMetalText

__version__ = "1.1.0"

__all__ = [
    "BEAD_DOMAIN",
    "BEAD_FEATURE_NOUN",
    "EMPTY_INVENTORY",
    "NOTHING_REBUILT",
    "NO_BEAD",
    "NO_FILTER",
    "PREFERENCES_DOMAIN",
    "PREFERENCE_THICKNESS_TOLERANCE",
    "REQUIREMENT_DOMAIN",
    "TOLERANCE_INCHES",
    "UNSTAMPED_BEAD",
    "BeadControls",
    "BeadFeatureParameter",
    "BeadGeometry",
    "BeadMaterialConstraintProvider",
    "BeadOnBody",
    "BeadParameterMapping",
    "BeadRequirements",
    "BeadSettings",
    "BeadSpecCache",
    "BeadSpecColumns",
    "BeadSpecLookup",
    "BeadSpecMatch",
    "BeadSpecRow",
    "BeadSpecSource",
    "BeadSpecStatus",
    "BeadSpecValidator",
    "BeadSpecWorkbookParser",
    "BeadTracebackResult",
    "BeadValidationContext",
    "BeadValidationResult",
    "BeadView",
    "BodyCheck",
    "BodyFeatureInventory",
    "CachedBeadSpecLookup",
    "CurveEnds",
    "FeatureInventory",
    "FeatureSpecStamp",
    "FileSystemBeadSpecSource",
    "MaterialAllowedRule",
    "MaterialThicknessPair",
    "PendingSheetMetal",
    "RefreshRequirementsEffectRule",
    "RequirementUser",
    "SheetMetalAllowance",
    "SheetMetalAssignmentSupport",
    "SheetMetalCodes",
    "SheetMetalConfigLocator",
    "SheetMetalMaterialColumns",
    "SheetMetalMaterialRow",
    "SheetMetalMaterialTable",
    "SheetMetalPartPreference",
    "SheetMetalPreferenceCheckResult",
    "SheetMetalPreferenceConstraintProvider",
    "SheetMetalPreferenceRead",
    "SheetMetalPreferenceReader",
    "SheetMetalPreferenceStatus",
    "SheetMetalProfile",
    "SheetMetalRequirement",
    "SheetMetalRequirementConstraintProvider",
    "SheetMetalRequirementNames",
    "SheetMetalRequirementReader",
    "SheetMetalRowFilter",
    "SheetMetalRowGateRule",
    "SheetMetalSettings",
    "SheetMetalStandardSelection",
    "SheetMetalText",
    "SpecDataError",
    "SpecPreview",
    "StandardInfo",
    "StoreRequirementReader",
    "SyncAction",
    "SyncSheetMetalPreferenceEffectRule",
    "ThicknessMatchRule",
    "UnstampedFeature",
    "__version__",
    "allowed_grades_at",
    "check_preference",
    "create_bead_requirements",
    "find_bead_workbook",
    "format_thickness",
    "grades_allowed_by_all",
    "group_curve_chains",
    "list_bead_spec_workbooks",
    "list_standards",
    "match_bead_spec",
    "parse_material_table",
    "requirement_to_specs",
    "requirements_from_read",
    "resolve_beads_on_body",
    "thickness_matches",
    "workbook_name_of",
]
