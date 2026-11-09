"""The sheet metal pieces every entry point needs, built once from the shared config, and the material rule modules this
domain contributes to the material engine. Building them here, not in each tool, is what makes the bead dialog and the
Material Assignment dialog read the same files and judge a body by the same rules."""

# NX 2412's Python stubs declare instance methods without `self`, type enum members as int and give collections
# no __iter__; these rules would only report those stub defects here. Everything else is still checked.
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportReturnType=false
# pyright: reportCallIssue=false, reportGeneralTypeIssues=false

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from banxopen.foundation import BodyId, OperationResult
from banxopen.foundation.nx import NxSessionContext, UndoScope, create_requirement_store
from banxopen.materials import (
    FeatureMaterialConstraintProvider,
    MaterialValidationRule,
    PostAssignmentEffectRule,
)
from banxopen.materials.nx import NxMaterialRuleModule, SideEffectExecutor
from banxopen.sheetmetal import (
    BEAD_DOMAIN,
    PREFERENCES_DOMAIN,
    REQUIREMENT_DOMAIN,
    BeadMaterialConstraintProvider,
    BeadSettings,
    BeadSpecCache,
    BeadSpecWorkbookParser,
    CachedBeadSpecLookup,
    FileSystemBeadSpecSource,
    RefreshRequirementsEffectRule,
    SheetMetalAssignmentSupport,
    SheetMetalCodes,
    SheetMetalConfigLocator,
    SheetMetalMaterialRow,
    SheetMetalMaterialTable,
    SheetMetalPartPreference,
    SheetMetalPreferenceConstraintProvider,
    SheetMetalRequirementConstraintProvider,
    SheetMetalRowGateRule,
    SheetMetalStandardSelection,
    SheetMetalText,
    SpecDataError,
    StoreRequirementReader,
    SyncSheetMetalPreferenceEffectRule,
)
from banxopen.sheetmetal.nx_adapters.beads import BeadTraceback, NxBeadInventory
from banxopen.sheetmetal.nx_adapters.preferences import NxSheetMetalPreferences, PreferenceSyncExecutor
from banxopen.sheetmetal.nx_adapters.requirements import RefreshRequirementsExecutor, SheetMetalRequirementService

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuleModule:
    """One `NxMaterialRuleModule`: the checks and the effects registered together, so neither is wired alone."""

    module_id: str
    validation_rules: Sequence[MaterialValidationRule] = ()
    feature_constraints: Sequence[FeatureMaterialConstraintProvider] = ()
    side_effect_rules: Sequence[PostAssignmentEffectRule] = ()
    side_effect_executors: Sequence[SideEffectExecutor] = field(default=())


class SheetMetalServices:
    """Use `create`. Tools reach the sheet metal domain through this, `BeadTool` and `assignment_support`."""

    def __init__(
        self,
        context: NxSessionContext,
        locator: SheetMetalConfigLocator,
        table: SheetMetalMaterialTable,
        settings: BeadSettings,
        cache: BeadSpecCache,
        lookup: CachedBeadSpecLookup,
        text: SheetMetalText,
    ) -> None:
        self.context = context
        self.locator = locator
        self.table = table
        """NX's sheet metal material standards file."""
        self.bead_settings = settings
        self.spec_cache = cache
        self.spec_lookup = lookup
        self.text = text
        self.standard_selection = SheetMetalStandardSelection(table)
        """The Standard a dialog with a Standard picker has chosen; unset, every Standard's rows are offered."""
        self.traceback = BeadTraceback(context)
        self.inventory = NxBeadInventory(context, self.traceback)
        self.preferences = NxSheetMetalPreferences(context, table, text)
        self.requirement_reader = StoreRequirementReader(create_requirement_store(context), text=text)
        self.requirements = SheetMetalRequirementService(
            context,
            self.inventory,
            lookup,
            settings,
            self.preferences,
            create_requirement_store(context),
            self.requirement_reader,
            text,
        )
        self.material_modules: tuple[NxMaterialRuleModule, ...] = self._modules()
        """Pass to `MaterialEngine.create`: bead SPEC restrictions, Sheet Metal Preferences, the part's requirements."""

    @staticmethod
    def create(
        context: NxSessionContext, text: SheetMetalText | None = None, root_directory: str | Path | None = None
    ) -> OperationResult[SheetMetalServices]:
        """A failure carries a user-facing message when the config or the standards file cannot be found or is
        invalid. Show it and stop, rather than start with partial rules: a tool that silently stopped enforcing SPEC
        restrictions would assign forbidden materials to beaded parts."""
        text = text or SheetMetalText.DEFAULT
        locator = SheetMetalConfigLocator(root_directory, text)
        try:
            table = SheetMetalMaterialTable.load(locator.material_table_path, text)
            source = FileSystemBeadSpecSource(table, BeadSpecWorkbookParser(text), text)
            cache = BeadSpecCache(source, locator.cache_directory, _log.warning)
            lookup = CachedBeadSpecLookup(cache, _log.warning)
            settings = BeadSettings.load(locator.bead_settings_path, text)
        except (OSError, SpecDataError) as ex:
            _log.error("Sheet metal configuration could not be loaded: %s", ex)
            return OperationResult.fail(SheetMetalCodes.CONFIG_UNAVAILABLE, text.config_unavailable(str(ex)))
        return OperationResult.success(SheetMetalServices(context, locator, table, settings, cache, lookup, text))

    def assignment_support(self) -> SheetMetalAssignmentSupport:
        """The Material Assignment dialog's sheet metal decisions."""
        return SheetMetalAssignmentSupport(self.table, self.text)

    def read_part_preference(self) -> SheetMetalPartPreference | None:
        """The work part's preferences, None when unreadable (the reason is logged)."""
        read = self.preferences.read_for_part()
        if read.preference is None:
            _log.warning("Could not read this part's Sheet Metal Preferences: %s", read.read_error)
        return read.preference

    def body_thickness_of(self, body_id: BodyId) -> float | None:
        """The body's own thickness, None when it is not sheet metal or NX will not say."""
        return self.preferences.read_for(body_id).body_thickness

    def set_preferences(self, row: SheetMetalMaterialRow) -> OperationResult[str]:
        """Sets only the part's preferences to `row` (a part with no solid body), and refreshes the requirements' grade
        attribute, in one undo mark: a refused change leaves no half-built preferences edit. On success the value is
        the message to show."""
        with UndoScope(self.context.session, "Set Sheet Metal Preferences") as undo:
            result = self.preferences.set_material(row)
            if not result.ok:
                return OperationResult.fail(result.error_code or "", result.message or "")
            executor = RefreshRequirementsExecutor(self.context, self.preferences, self.requirements, self.text)
            grade = executor.ensure_grade_attribute()
            if not grade.ok:
                return OperationResult.fail(grade.error_code or "", grade.message or "")
            undo.commit()
        return OperationResult.success(self.text.preferences_set(row.name, row.describe()))

    def _modules(self) -> tuple[NxMaterialRuleModule, ...]:
        text, table = self.text, self.table
        return (
            # Validation only: assigning a material changes nothing about the beads.
            RuleModule(
                BEAD_DOMAIN,
                feature_constraints=(
                    BeadMaterialConstraintProvider(
                        self.inventory, self.spec_lookup, table, self.bead_settings, self.requirement_reader, text
                    ),
                ),
            ),
            RuleModule(
                PREFERENCES_DOMAIN,
                validation_rules=(
                    SheetMetalRowGateRule(self.preferences, self.standard_selection, self.requirement_reader, text),
                ),
                feature_constraints=(SheetMetalPreferenceConstraintProvider(self.preferences, table, text),),
                side_effect_rules=(SyncSheetMetalPreferenceEffectRule(table),),
                side_effect_executors=(PreferenceSyncExecutor(self.preferences, table, text),),
            ),
            RuleModule(
                REQUIREMENT_DOMAIN,
                feature_constraints=(
                    SheetMetalRequirementConstraintProvider(
                        self.requirement_reader, self.preferences, self.standard_selection, text
                    ),
                ),
                side_effect_rules=(RefreshRequirementsEffectRule(),),
                side_effect_executors=(
                    RefreshRequirementsExecutor(self.context, self.preferences, self.requirements, text),
                ),
            ),
        )
