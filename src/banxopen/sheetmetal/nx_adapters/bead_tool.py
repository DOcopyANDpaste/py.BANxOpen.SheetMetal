"""The bead tool's NX side: reads the part and the selection, keeps the previews, and builds on Apply. Every gesture
returns a `BeadView` for the dialog to render; every decision is `BeadToolLogic`'s. The dialog only reads its blocks
into `BeadControls` and writes the view back."""

# NX 2412's Python stubs declare instance methods without `self`, type enum members as int and give collections
# no __iter__; these rules would only report those stub defects here. Everything else is still checked.
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportReturnType=false
# pyright: reportCallIssue=false, reportGeneralTypeIssues=false

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import NXOpen
import NXOpen.Features

from banxopen.foundation import OperationResult
from banxopen.foundation.nx import NxSessionContext, UndoScope, nx_error_detail
from banxopen.sheetmetal import (
    BEAD_DOMAIN,
    NO_BEAD,
    BeadControls,
    BeadSpecRow,
    BeadSpecValidator,
    BeadView,
    SheetMetalCodes,
    StandardInfo,
    resolve_beads_on_body,
)
from banxopen.sheetmetal.core.errors import is_spec_data_failure
from banxopen.sheetmetal.core.tools.bead_tool import BeadBody, BeadPart, BeadTarget, BeadToolLogic, chain_error
from banxopen.sheetmetal.nx_adapters import uf_modl
from banxopen.sheetmetal.nx_adapters.beads import BeadAttributes, Traceback, apply_spec_name, feature_label
from banxopen.sheetmetal.nx_adapters.features import (
    BeadFeatureService,
    BeadPreview,
    SelectedItem,
    create_curve_section,
    destroy_section,
    expand_selection,
    find_direction,
    height_side,
)
from banxopen.sheetmetal.nx_adapters.preferences import read_body_profile
from banxopen.sheetmetal.nx_adapters.services import SheetMetalServices

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Line:
    item: SelectedItem
    traceback: Traceback
    target: BeadTarget
    sources: list[NXOpen.NXObject]


@dataclass(frozen=True, slots=True)
class BlockContents:
    """What the dialog's two selection blocks should hold after lines were deleted. Set the feature block first."""

    bead_features: tuple[NXOpen.TaggedObject, ...]
    curve_sections: tuple[NXOpen.TaggedObject, ...]


class BeadTool:
    """One dialog session. Gestures, in the order a dialog calls them: `open` (initialize), `selection_changed`
    (dialog shown, and every selection block update), `controls_changed` (bead SPEC radio, SPEC tree, direction,
    preview), `delete_lines`, `apply`, `close` (however the dialog closed)."""

    def __init__(self, services: SheetMetalServices) -> None:
        """Use `create`."""
        self._services = services
        self._context: NxSessionContext = services.context
        self._text = services.text
        self._features = BeadFeatureService(self._context, services.bead_settings)
        self._logic = BeadToolLogic(
            BeadSpecValidator.standard(services.text),
            services.spec_lookup.find_all,
            self._load_specs,
            services.text,
        )
        self._standards: tuple[StandardInfo, ...] = ()
        self._part = BeadPart(None, None, None)
        self._body: NXOpen.Body | None = None
        self._lines: list[_Line] = []
        self._previews: list[BeadPreview] = []
        self._block_sections: list[NXOpen.Section] = []
        self._last_status: str | None = None

    @staticmethod
    def create(services: SheetMetalServices) -> BeadTool:
        return BeadTool(services)

    # ---- gestures ----

    def open(self, controls: BeadControls) -> BeadView:
        """Reads the Standards and the part, and brings the part's bead requirements up to date with its beads."""
        try:
            self._standards = tuple(self._services.spec_cache.list_standards())
        except Exception as ex:
            if not is_spec_data_failure(ex) and not isinstance(ex, RuntimeError):
                raise
            _log.warning(self._text.standards_unlistable(str(ex)))
        self._read_part()
        self._sync_requirements()
        self._audit("on open")
        return self._view(controls, notify=True)

    def selection_changed(
        self,
        curve_block_objects: Sequence[NXOpen.NXObject],
        feature_block_objects: Sequence[NXOpen.NXObject],
        controls: BeadControls,
        *,
        notify: bool = False,
    ) -> BeadView:
        """Pass what the blocks hold, as they hold it. Also after a sketch drawn on the fly in the curve block is
        finished or cancelled: the previews go first, since their builders must not outlive curves the sketch task
        rolled back, and objects the rollback deleted are left out (the block can still hand them out)."""
        self._close_previews()
        curves = self._not_deleted(curve_block_objects, "curve block")
        features = self._not_deleted(feature_block_objects, "feature block")
        self._trace(expand_selection(self._context, curves, features))
        return self._view(controls, selection_changed=True, notify=notify)

    def controls_changed(self, controls: BeadControls, *, bead_spec_changed: bool = False) -> BeadView:
        return self._view(controls, notify=bead_spec_changed)

    def delete_lines(self, indices: Sequence[int]) -> BlockContents:
        """The selection list's delete button. The curve block only takes Sections, so each remaining chain goes back
        as a Section of its own. Set the blocks from the result, then call `selection_changed`."""
        deleted = {i for i in indices if 0 <= i < len(self._lines)}
        remaining = [line.sources for i, line in enumerate(self._lines) if i not in deleted]
        features: dict[str, NXOpen.TaggedObject] = {}
        chains: list[list[NXOpen.NXObject]] = []
        for sources in remaining:
            chain: list[NXOpen.NXObject] = []
            for obj in sources:
                if isinstance(obj, NXOpen.Features.Feature):
                    features.setdefault(str(obj.Tag), obj)
                elif obj not in chain:
                    chain.append(obj)
            if chain:
                chains.append(chain)
        self._close_previews()
        sections = [create_curve_section(self._context.work_part, chain) for chain in chains]
        self._block_sections.extend(sections)  # no feature takes them: destroyed when the dialog closes
        return BlockContents(tuple(features.values()), tuple(sections))

    def apply(self, controls: BeadControls) -> OperationResult[str]:
        """Builds or updates every listed bead to the picked SPEC, names and stamps it, and moves the SPEC's requirement
        to the row just applied, in one undo mark: a failure anywhere takes everything back. The material and the
        preferences are never touched. On success the value is the message to show; re-render with
        `selection_changed`, since the part was read again."""
        blocker = self._logic.apply_blocker(self._part, self._targets(), controls)
        spec = controls.picked_spec
        if blocker is not None or spec is None or self._part.body is None:
            return OperationResult.fail(SheetMetalCodes.BEAD_APPLY_BLOCKED, blocker or self._text.choose_spec_first)

        self._close_previews()  # their builders are open on the same features and curves
        notes: tuple[str, ...] = ()
        with UndoScope(self._context.session, "Create/Update Bead") as undo:
            if not self._build_beads(spec, controls):
                return OperationResult.fail(SheetMetalCodes.BEAD_CREATE_FAILED, self._text.build_failed)
            # Beads without their requirement would be invisible to NX's own check: a failure takes them back too.
            refreshed = self._services.requirements.refresh_beads(
                self._part.body.body_id, {(spec.standard_id, spec.spec_id): spec}
            )
            if not refreshed.ok:
                return OperationResult.fail(
                    refreshed.error_code or "", self._text.requirement_write_failed(refreshed.message)
                )
            undo.commit()
            notes = refreshed.value or ()

        self._read_part()
        self._logic.reload_specs()
        self._audit("after Apply")
        return OperationResult.success(self._text.applied_with_notes(notes) if notes else self._text.applied)

    def close(self) -> None:
        """Removes any live preview and the Sections handed to the curve block."""
        self._close_previews()
        for section in self._block_sections:
            destroy_section(self._context, section)
        self._block_sections.clear()

    # ---- the part ----

    def _read_part(self) -> None:
        services, text = self._services, self._text
        read = services.preferences.read_for_part()
        if read.preference is None:
            _log.warning("Could not read this part's Sheet Metal Preferences: %s.", read.read_error)
        self._body = None
        bodies = services.preferences.sheet_metal_bodies()
        part = BeadPart(read.preference, None, None, standards=self._standards)
        if len(bodies) != 1:
            self._part = dataclasses.replace(part, body_error=text.not_exactly_one_body(len(bodies)))
            _log.error(self._part.body_error)
            return
        profile = read_body_profile(self._context, services.preferences, bodies[0], text)
        if not profile.ok or profile.value is None:
            self._part = dataclasses.replace(part, body_error=profile.message or text.body_unreadable(bodies[0].Name))
            _log.error(self._part.body_error)
            return

        p = profile.value
        self._body = bodies[0]
        inventory = services.inventory.read(p.body_id)
        if inventory.read_error is not None:
            _log.warning(
                "The beads on this body could not be read, so they do not restrict the material: %s",
                inventory.read_error,
            )
            beads = ()
        else:
            beads = resolve_beads_on_body(inventory, services.spec_lookup, services.bead_settings)
        requirements, errors = services.requirement_reader.read_all()
        self._part = BeadPart(
            p.preference,
            BeadBody(p.body_id, p.name, p.thickness, p.physical_material),
            None,
            beads,
            tuple(r for r in requirements if r.domain == BEAD_DOMAIN and r.is_in_use),
            tuple(errors),
            self._standards,
        )

    def _sync_requirements(self) -> None:
        """Its own undo step, left in the list only when something was written; a failed write is taken back."""
        body = self._part.body
        if body is None:
            return
        try:
            with UndoScope(self._context.session, "Sync Bead Requirements") as undo:
                sync = self._services.requirements.sync_beads(body.body_id)
                if not sync.ok:
                    _log.warning("The bead requirements could not be brought up to date: %s", sync.message)
                    return
                if sync.value is not None and sync.value.changed:
                    undo.commit()
                    _log.info("The bead requirements were brought up to date with the beads in this part.")
                    self._read_part()
        except NXOpen.NXException as ex:
            _log.error("Bringing the bead requirements up to date failed: %s", nx_error_detail(ex))

    def _audit(self, when: str) -> None:
        try:
            lines = self._services.requirements.audit_beads()
            _log.debug("Requirement audit %s: %d requirement(s)\n%s", when, len(lines), "\n".join(lines))
        except NXOpen.NXException as ex:
            _log.debug("Requirement audit %s failed: %s", when, nx_error_detail(ex))

    def _load_specs(self, standard: StandardInfo, bead_spec: str) -> tuple[tuple[BeadSpecRow, ...], str | None]:
        text = self._text
        cache = self._services.spec_cache
        try:
            rows = cache.get_specs(standard, bead_spec)
            if rows:
                return rows, None
            if cache.find_workbook(standard, bead_spec) is None:
                return (), text.no_workbook_named(standard.bead_spec_folder, bead_spec)
            return (), text.workbook_lists_no_specs(bead_spec)
        except Exception as ex:
            if not is_spec_data_failure(ex):
                raise
            return (), text.workbook_unreadable_reason(str(ex))

    # ---- the selection ----

    def _trace(self, items: Sequence[SelectedItem]) -> None:
        """One line per build target: a Bead feature picked directly and a chain of its own curves, or two pattern
        members of one stamped original, come down to one thing to build; a duplicate's sources fold into its line."""
        self._lines = []
        by_key: dict[str, _Line] = {}
        for item in items:
            line = self._line_for(item)
            existing = by_key.get(line.target.key)
            if existing is not None:
                existing.sources.extend(line.sources)
                continue
            by_key[line.target.key] = line
            self._lines.append(line)

    def _line_for(self, item: SelectedItem) -> _Line:
        traceback_service = self._services.traceback
        if item.bead_feature is not None:
            trace = traceback_service.trace(item.bead_feature)
            error = None
        else:
            traces = [traceback_service.trace(curve) for curve in item.curves]
            beads: dict[str, Traceback] = {}
            for t in traces:
                if t.existing_feature is not None:
                    beads.setdefault(str(t.existing_feature.Tag), t)
            new_curves = sum(1 for t in traces if t.existing_feature is None)
            error = chain_error(
                [feature_label(t.existing_feature) for t in beads.values() if t.existing_feature],
                new_curves,
                self._text,
            )
            if error is not None:
                trace = Traceback(None, NO_BEAD)  # neither new nor an update: no bead to update
            else:
                trace = next(iter(beads.values())) if beads else traces[0]

        feature = trace.existing_feature
        key = f"F:{feature.Tag}" if feature is not None else "C:" + ",".join(sorted(str(c.Tag) for c in item.curves))
        anchor = feature if feature is not None else item.curves[0]
        target = BeadTarget(
            key,
            self._label(item, trace),
            anchor.JournalIdentifier,
            trace.result,
            str(feature.Tag) if feature is not None else None,
            error,
        )
        if target.is_unstamped and feature is not None:
            # An unstamped bead was identified from its geometry when the part was read.
            on_body = next((b for b in self._part.beads if b.feature_key == str(feature.Tag)), None)
            target = dataclasses.replace(
                target,
                matched_spec=on_body.spec_family[0] if on_body is not None and on_body.spec_family else None,
                unmatched_reason=(
                    self._text.selection_not_among_body_beads
                    if on_body is None
                    else self._text.bead_unidentified_reason(on_body)
                ),
            )
        return _Line(item, trace, target, list(item.sources))

    def _label(self, item: SelectedItem, trace: Traceback) -> str:
        text = self._text
        if trace.existing_feature is not None:
            suffix = text.selection_via_pattern_suffix if trace.result.is_pattern_instance else ""
            return feature_label(trace.existing_feature) + suffix
        first = item.curves[0]
        label = first.Name or text.selection_unnamed(type(first).__name__, first.JournalIdentifier)
        if len(item.curves) > 1:
            label += text.selection_more_curves(len(item.curves) - 1)
        sketch = item.from_sketch
        return label if sketch is None else text.selection_from_sketch(label, sketch.Name or sketch.JournalIdentifier)

    def _not_deleted(self, objects: Sequence[NXOpen.NXObject], block: str) -> list[NXOpen.NXObject]:
        kept: list[NXOpen.NXObject] = []
        dropped: list[str] = []
        for obj in objects:
            if uf_modl.is_deleted(self._context.uf, obj.Tag):
                dropped.append(f"{type(obj).__name__} tag {obj.Tag}")
            else:
                kept.append(obj)
        if dropped:
            _log.warning(
                "%s: %d selected object(s) no longer exist (e.g. a cancelled sketch) and were left out: %s.",
                block,
                len(dropped),
                ", ".join(dropped),
            )
        return kept

    def _targets(self) -> list[BeadTarget]:
        return [line.target for line in self._lines]

    # ---- rendering ----

    def _view(self, controls: BeadControls, *, selection_changed: bool = False, notify: bool = False) -> BeadView:
        view = self._logic.view(
            self._part, self._targets(), controls, selection_changed=selection_changed, notify=notify
        )
        self._refresh_preview(view)
        view = dataclasses.replace(view, direction=self._direction())
        self._log(view)
        return view

    def _direction(self) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        first = next((line for line in self._lines if line.target.chain_error is None), None)
        if first is None or self._body is None:
            return None
        return find_direction(self._context, first.item.curves, first.traceback.existing_feature, self._body)

    def _refresh_preview(self, view: BeadView) -> None:
        """With Show Preview on and nothing blocking, previews every bead Apply would build; one that cannot be
        previewed is logged and skipped."""
        self._close_previews()
        controls = view.controls
        spec = controls.picked_spec
        if not controls.preview_on or not self._lines:
            return
        if view.blocking is not None or spec is None or self._part.row is None:
            _log.debug("Preview: not shown — %s", view.blocking or "choose a SPEC first")
            return
        side = height_side(controls.flipped)
        for line in self._lines:
            preview = self._features.open_preview(line.item.curves, spec, line.traceback.existing_feature, side)
            if preview.ok and preview.value is not None:
                self._previews.append(preview.value)
            else:
                _log.warning("Preview of '%s' not shown: %s", line.target.label, preview.message)

    def _close_previews(self) -> None:
        for preview in self._previews:
            preview.close()
        self._previews.clear()

    def _log(self, view: BeadView) -> None:
        """Only when something changed: an unchanged block on every click would bury the line that did change."""
        fingerprint = f"{view.status}|{view.blocking}|{view.warnings}"
        if fingerprint == self._last_status:
            return
        self._last_status = fingerprint
        _log.debug(view.status)
        if view.warnings:
            _log.warning(view.warnings)
        if view.blocking:
            _log.warning("Blocking: %s", view.blocking)

    def _build_beads(self, spec: BeadSpecRow, controls: BeadControls) -> bool:
        """False when any bead failed; each failure is logged, and the caller's undo mark takes back the rest."""
        bead_spec = self._logic.stamped_bead_spec(controls, spec)
        side = height_side(controls.flipped)
        all_built = True
        for line in self._lines:
            existing = line.traceback.existing_feature
            result = self._features.create_or_update(line.item.curves, spec, existing, side)
            if not result.ok or result.value is None:
                all_built = False
                _log.error(
                    "Bead %s failed for '%s': %s",
                    "create" if existing is None else "update",
                    line.target.label,
                    result.message,
                )
                continue
            # Named before it is stamped: the namer reads the previous SPEC id the stamp overwrites.
            apply_spec_name(result.value, spec.spec_id)
            BeadAttributes.stamp(result.value, spec.standard_id, bead_spec, spec.spec_id, is_new=existing is None)
        return all_built
