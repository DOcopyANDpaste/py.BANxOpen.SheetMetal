"""The bead tool's decisions, free of NX: what the dialog shows and what stops Apply. The NX side (`BeadTool`) reads
the part and the selection into the plain data below and asks this for a `BeadView` on every gesture, so a rule or
wording change never touches a dialog.

The part has one sheet metal body. Its Sheet Metal Preferences are read-only here: their Material Table row decides
the Standard and the grade, and with the body's thickness, which SPECs are listed. Each line of the selection is one
bead to build or update.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from banxopen.foundation import BodyId
from banxopen.sheetmetal.core.beads.rules import BeadSpecValidator, grades_allowed_by_all
from banxopen.sheetmetal.model.beads import BeadOnBody, BeadSpecRow, BeadTracebackResult, StandardInfo
from banxopen.sheetmetal.model.requirements import SheetMetalAllowance, SheetMetalRequirement
from banxopen.sheetmetal.model.table import SheetMetalMaterialRow, SheetMetalPartPreference, SheetMetalProfile
from banxopen.sheetmetal.model.thickness import format_short, format_thickness, thickness_matches
from banxopen.sheetmetal.text import SheetMetalText

FamilyLookup = Callable[[str, str], Sequence[BeadSpecRow]]
SpecLoader = Callable[[StandardInfo, str], tuple[tuple[BeadSpecRow, ...], str | None]]
"""Loads a bead SPEC workbook's rows for a Standard, and why there are none when there are none."""


@dataclass(frozen=True, slots=True)
class BeadBody:
    body_id: BodyId
    name: str
    thickness: float
    physical_material: str | None


@dataclass(frozen=True, slots=True)
class BeadPart:
    """The part as the bead tool reads it on open and after each Apply."""

    preference: SheetMetalPartPreference | None
    """None when the preferences could not be read."""
    body: BeadBody | None
    """None, with `body_error` saying why, when the part does not have exactly one readable sheet metal body."""
    body_error: str | None
    beads: tuple[BeadOnBody, ...] = ()
    requirements: tuple[SheetMetalRequirement, ...] = ()
    """The bead requirements in use."""
    requirement_errors: tuple[str, ...] = ()
    standards: tuple[StandardInfo, ...] = ()

    @property
    def row(self) -> SheetMetalMaterialRow | None:
        """The standards-file row the preferences hold; None when NX is not going by a row."""
        p = self.preference
        return p.row if p is not None and p.is_material_table_entry else None

    @property
    def standard(self) -> StandardInfo | None:
        """The preference row's Standard: material and Standard must agree, or a grade would be matched against
        another Standard's workbooks."""
        row = self.row
        if row is None:
            return None
        return next((s for s in self.standards if s.id.casefold() == row.standard.casefold()), None)

    @property
    def thickness(self) -> float | None:
        """What SPECs are judged against: the body's own, else the preferences'."""
        if self.body is not None:
            return self.body.thickness
        return self.preference.thickness if self.preference is not None else None

    @property
    def profile(self) -> SheetMetalProfile | None:
        row, thickness = self.row, self.thickness
        return SheetMetalProfile(thickness, row.grade) if row is not None and thickness is not None else None

    def is_current_standard(self, standard_id: str) -> bool:
        standard = self.standard
        return standard is not None and standard.id.casefold() == standard_id.casefold()


@dataclass(frozen=True, slots=True)
class BeadTarget:
    """One line of the selection: a chain of curves (a new bead), or an existing bead to update."""

    key: str
    """What the bead builder works on, as a key: one line per key."""
    label: str
    journal_id: str
    traceback: BeadTracebackResult
    existing_feature_key: str | None = None
    chain_error: str | None = None
    """Why the chain cannot be one bead. Listed, and blocks Apply."""
    matched_spec: BeadSpecRow | None = None
    """The SPEC an unstamped bead was identified as."""
    unmatched_reason: str | None = None

    @property
    def is_unstamped(self) -> bool:
        return self.traceback.has_unstamped_feature

    @property
    def is_existing(self) -> bool:
        return self.existing_feature_key is not None

    @property
    def known_spec(self) -> tuple[str, str, str | None] | None:
        """(Standard, SPEC id, bead SPEC) off the stamp or the geometry match."""
        t = self.traceback
        if t.found and t.standard_id is not None and t.spec_id is not None:
            return t.standard_id, t.spec_id, t.bead_spec
        m = self.matched_spec
        return (m.standard_id, m.spec_id, m.workbook_name) if m is not None else None


@dataclass(frozen=True, slots=True)
class BeadControls:
    """What the dialog's controls hold. The tool owns these; the library returns updated values in the view."""

    bead_spec_names: tuple[str, ...] = ()
    """What the bead SPEC radio offers."""
    bead_spec: str | None = None
    picked_spec: BeadSpecRow | None = None
    """The row checked in the SPEC tree."""
    flipped: bool = False
    preview_on: bool = False


@dataclass(frozen=True, slots=True)
class SpecPreview:
    radius: float
    width: float
    height: float
    die_radius: float


@dataclass(frozen=True, slots=True)
class BeadView:
    """Everything the bead dialog renders. `controls` carries values the library changed (the pre-filled bead SPEC and
    row); write them back to the blocks."""

    controls: BeadControls
    spec_rows: tuple[BeadSpecRow, ...]
    kept_for_beads: frozenset[BeadSpecRow]
    """Rows listed only because a selected bead is built to them although they no longer validate: show as a warning."""
    spec_preview: SpecPreview | None
    selection_lines: tuple[str, ...]
    preference_summary: str
    mode: str
    blocking: str | None
    """The one thing stopping Apply, in the order the user has to resolve them; None when Apply may run."""
    warnings: str | None
    """Advisories that do not stop Apply."""
    info: str | None = None
    """A message to show once in a box (no SPEC matches), or None."""
    status: str = ""
    """The state as a debug block for the Listing Window."""
    direction: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    """Where the direction arrow goes (origin, normal); None hides it."""


@dataclass(slots=True)
class _Specs:
    rows: tuple[BeadSpecRow, ...] = ()
    no_specs_reason: str | None = None


@dataclass(slots=True)
class BeadToolLogic:
    """Holds what one dialog session has worked out (the loaded bead SPEC, unresolved stamps, the last info shown)."""

    validator: BeadSpecValidator
    find_family: FamilyLookup
    load_specs: SpecLoader
    text: SheetMetalText = field(default_factory=lambda: SheetMetalText.DEFAULT)
    _specs: _Specs = field(default_factory=_Specs)
    _loaded_for: tuple[str | None, str | None] | None = None
    _unresolved: tuple[str, ...] = ()
    _last_no_match_key: str | None = None

    # ---- gestures ----

    def view(
        self,
        part: BeadPart,
        targets: Sequence[BeadTarget],
        controls: BeadControls,
        *,
        selection_changed: bool = False,
        notify: bool = False,
    ) -> BeadView:
        """`selection_changed`: re-resolve the stamps and pre-fill the SPEC from the selected beads. `notify`: on open
        and when the bead SPEC changes, say in `info` when the chosen bead SPEC has no row the preferences allow."""
        self._ensure_specs(part, controls.bead_spec)
        if selection_changed:
            controls = self._prefill(part, targets, controls)
        rows, kept, picked = self._spec_variants(part, targets, controls.picked_spec)
        controls = replace(controls, picked_spec=picked)
        mode, blocking = self._mode_and_blocking(part, targets, picked)
        warnings = self._warnings(part, targets)
        return BeadView(
            controls=controls,
            spec_rows=rows,
            kept_for_beads=kept,
            spec_preview=(
                SpecPreview(picked.radius_and_rad_s, picked.width, picked.height, picked.die_radius_p)
                if picked is not None
                else None
            ),
            selection_lines=self._selection_lines(targets),
            preference_summary=self._preference_summary(part),
            mode=mode,
            blocking=blocking,
            warnings=warnings,
            info=self._no_match_info(part, controls) if notify else None,
            status=self._status(part, targets, mode, picked),
        )

    def apply_blocker(self, part: BeadPart, targets: Sequence[BeadTarget], controls: BeadControls) -> str | None:
        """Why Apply cannot run, or None."""
        self._ensure_specs(part, controls.bead_spec)
        _, blocking = self._mode_and_blocking(part, targets, controls.picked_spec)
        if blocking is not None:
            return blocking
        if controls.picked_spec is None:
            return self.text.choose_spec_first
        if not targets:
            return self.text.nothing_selected
        return None

    def stamped_bead_spec(self, controls: BeadControls, spec: BeadSpecRow) -> str:
        """The bead SPEC recorded on a bead: the name the dialog offers, not the workbook's file name, so a rename or
        a revision suffix cannot orphan a stamped bead."""
        return _match_bead_spec_name(controls.bead_spec_names, spec.workbook_name) or spec.workbook_name

    def constraining_beads(self, part: BeadPart, targets: Sequence[BeadTarget]) -> tuple[BeadOnBody, ...]:
        """The beads on the body that must allow the preference row: all but the selected ones, which Apply
        rebuilds."""
        selected = {t.existing_feature_key for t in targets if t.existing_feature_key is not None}
        return tuple(b for b in part.beads if b.feature_key not in selected)

    # ---- the preferences ----

    def preference_problem(self, part: BeadPart) -> str | None:
        """Why the preferences cannot drive this dialog, or None. Each case is fixed in NX."""
        text, p = self.text, part.preference
        if p is None:
            return text.preferences_unreadable_reason
        if not p.is_material_table_entry:
            return text.preferences_not_material_table_reason
        if p.material_name is None:
            return text.preferences_no_material_reason
        if p.row is None:
            return text.preferences_material_not_in_table_reason(p.material_name)
        if part.standard is None:
            return text.preferences_standard_not_listed_reason(p.row.standard)
        return None

    def _preference_state(self, p: SheetMetalPartPreference) -> str:
        text = self.text
        if not p.is_material_table_entry:
            return text.preference_state_not_material_table
        if p.material_name is None:
            return text.preference_state_no_material
        if p.row is None:
            return text.preference_state_not_in_table(p.material_name)
        return text.preference_state_material(p.material_name)

    def _preference_summary(self, part: BeadPart) -> str:
        text, p = self.text, part.preference
        if p is None:
            return text.preference_summary_unreadable(text.preference_state_unreadable)
        summary = text.preference_summary(
            self._preference_state(p),
            format_thickness(p.thickness),
            p.row.grade if p.row is not None else None,
            p.row.standard if p.row is not None else None,
        )
        problem = self.preference_problem(part)
        return text.preference_summary_not_usable(summary, problem) if problem is not None else summary

    # ---- SPECs ----

    def _ensure_specs(self, part: BeadPart, bead_spec: str | None) -> None:
        standard = part.standard
        key = (standard.id if standard is not None else None, bead_spec)
        if key == self._loaded_for:
            return
        self._loaded_for = key
        if standard is None:
            self._specs = _Specs()
        elif not bead_spec:
            self._specs = _Specs((), self.text.no_bead_spec_selected)
        else:
            rows, reason = self.load_specs(standard, bead_spec)
            self._specs = _Specs(rows, reason)

    def reload_specs(self) -> None:
        """Forget the loaded workbook, e.g. after Apply."""
        self._loaded_for = None

    def _spec_variant_at(self, part: BeadPart, spec_id: str) -> BeadSpecRow | None:
        """The loaded workbook's row for `spec_id` driven for the sheet's thickness: a SPEC id is carried by one row per
        thickness, and offering the wrong one would only fail validation."""
        thickness = part.thickness
        if thickness is None:
            return None
        return next(
            (
                s
                for s in self._specs.rows
                if s.spec_id.casefold() == spec_id.casefold() and thickness_matches(s.thickness, thickness)
            ),
            None,
        )

    def _spec_variants(
        self, part: BeadPart, targets: Sequence[BeadTarget], picked: BeadSpecRow | None
    ) -> tuple[tuple[BeadSpecRow, ...], frozenset[BeadSpecRow], BeadSpecRow | None]:
        """The rows that validate against the sheet, none while the preferences name no usable row. A SPEC a selected
        bead is built to is kept even when it no longer validates, so the reason it fails can become visible. The
        user's pick carries across whenever it is still listed."""
        profile = part.profile
        rows = list(self.validator.find_valid(profile, self._specs.rows)) if profile is not None else []
        valid_count = len(rows)
        if profile is not None:
            for _, spec_id, _ in _known_specs(targets):
                if all(r.spec_id != spec_id for r in rows) and (known := self._spec_variant_at(part, spec_id)):
                    rows.append(known)
        carried = next((r for r in rows if picked is not None and _same_row(r, picked)), None)
        return tuple(rows), frozenset(rows[valid_count:]), carried

    def _prefill(self, part: BeadPart, targets: Sequence[BeadTarget], controls: BeadControls) -> BeadControls:
        """Pre-fill the SPEC when every known bead agrees on one SPEC in the chosen Standard; otherwise leave the
        user's choice alone. The Standard itself is never switched: it belongs to the sheet metal material."""
        known = _known_specs(targets)
        self._unresolved = tuple(
            dict.fromkeys(
                spec_id
                for standard_id, spec_id, bead_spec in known
                if part.is_current_standard(standard_id)
                and self._resolve_bead_spec(controls, standard_id, spec_id, bead_spec) is None
            )
        )
        if len(known) != 1 or not part.is_current_standard(known[0][0]):
            return controls
        standard_id, spec_id, bead_spec = known[0]
        resolved = self._resolve_bead_spec(controls, standard_id, spec_id, bead_spec)
        if resolved is not None and resolved.casefold() != (controls.bead_spec or "").casefold():
            # The radio moves first: the SPEC tree only holds one bead SPEC's rows.
            controls = replace(controls, bead_spec=resolved)
            self._ensure_specs(part, resolved)
        variant = self._spec_variant_at(part, spec_id)
        return replace(controls, picked_spec=variant) if variant is not None else controls

    def _resolve_bead_spec(
        self, controls: BeadControls, standard_id: str, spec_id: str, stamped: str | None
    ) -> str | None:
        """Which bead SPEC a known bead belongs to: off its stamp, or for an older stamp by its SPEC id. None when it
        cannot be pinned down: the radio is then left alone rather than pointed at a guess."""
        if stamped:
            return _match_bead_spec_name(controls.bead_spec_names, stamped)
        family = self.find_family(standard_id, spec_id)
        return _match_bead_spec_name(controls.bead_spec_names, family[0].workbook_name) if family else None

    def _no_match_info(self, part: BeadPart, controls: BeadControls) -> str | None:
        """When the chosen bead SPEC has no row the preferences allow, otherwise the tree is just empty. Not twice in a
        row for the same SPEC and sheet; preferences that cannot drive the dialog are left to the blocking message."""
        profile = part.profile
        if profile is None:
            return None
        text = self.text
        bead_spec = controls.bead_spec or text.no_bead_spec
        reason = self._specs.no_specs_reason
        message: str | None = None
        if reason is not None:
            message = text.no_specs(bead_spec, reason)
        elif self._specs.rows and not self.validator.find_valid(profile, self._specs.rows):
            covered = (format_thickness(t) for t in sorted({s.thickness for s in self._specs.rows}))
            message = text.no_row_matches(
                bead_spec, format_thickness(profile.thickness), profile.material_grade_label, covered
            )
        if message is None:
            self._last_no_match_key = None
            return None
        key = f"{bead_spec}|{profile.thickness}|{profile.material_grade_label}|{reason}"
        if key == self._last_no_match_key:
            return None
        self._last_no_match_key = key
        return message

    # ---- blocking and mode ----

    def _mode_and_blocking(
        self, part: BeadPart, targets: Sequence[BeadTarget], picked: BeadSpecRow | None
    ) -> tuple[str, str | None]:
        text = self.text
        mode = self._mode(targets)
        if part.body_error is not None:
            return mode, part.body_error
        row, standard = part.row, part.standard
        problem = self.preference_problem(part)
        if problem is not None or row is None or standard is None:
            return mode, text.preferences_unusable(problem or text.preferences_no_row_reason)
        thickness = part.thickness
        if thickness is not None and (refusal := self.bead_refusal(part, targets, row, thickness)) is not None:
            return mode, refusal
        if self._specs.no_specs_reason is not None:
            return mode, text.no_spec_allowed_under_standard(standard.id, self._specs.no_specs_reason)
        broken = next((t for t in targets if t.chain_error is not None), None)
        if broken is not None:
            return mode, text.cannot_be_one_bead(broken.label, broken.chain_error)
        if picked is None:
            return text.mode_choose_spec, None
        profile = part.profile
        if profile is None:
            return mode, None
        result = self.validator.validate(profile, picked)
        if result.is_valid:
            return mode, None
        alternatives = (s.spec_id for s in self.validator.find_valid(profile, self._specs.rows))
        return mode, text.with_alternatives(result.blocking_message, alternatives)

    def bead_refusal(
        self, part: BeadPart, targets: Sequence[BeadTarget], row: SheetMetalMaterialRow, thickness: float
    ) -> str | None:
        """Why the beads already on the body refuse the row's grade at the thickness, naming them, or None. A SPEC not
        driven for the thickness at all is the coarser problem and is reported first and on its own."""
        uncovered: list[BeadOnBody] = []
        refusing: list[BeadOnBody] = []
        for bead in self.constraining_beads(part, targets):
            if not bead.is_spec_known:
                continue
            spec_row = bead.spec_at(thickness)
            if spec_row is None:
                uncovered.append(bead)
            elif not spec_row.is_allowed_for(row.grade):
                refusing.append(bead)
        text = self.text
        if uncovered:
            items = (text.uncovered_item(b.spec_id or "", b.name, b.thickness_coverage) for b in uncovered)
            return text.thickness_not_covered(format_thickness(thickness), items)
        if refusing:
            items = (text.refusing_item(b.name, b.spec_id or "") for b in refusing)
            return text.grade_refused_by_beads(row.name, row.grade, format_thickness(thickness), items)
        return None

    def _mode(self, targets: Sequence[BeadTarget]) -> str:
        text = self.text
        buildable = [t for t in targets if t.chain_error is None]
        updates = sum(1 for t in buildable if t.is_existing)
        creates = len(buildable) - updates
        if updates == 0:
            return text.mode_begin if creates == 0 else text.mode_new(creates)
        return text.mode_editing(updates) if creates == 0 else text.mode_mixed(creates, updates)

    # ---- the selection list ----

    def _selection_lines(self, targets: Sequence[BeadTarget]) -> tuple[str, ...]:
        text = self.text
        counts: dict[str, int] = {}
        for t in targets:
            counts[t.label] = counts.get(t.label, 0) + 1
        lines: list[str] = []
        for t in targets:
            label = text.selection_disambiguated(t.label, t.journal_id) if counts[t.label] > 1 else t.label
            result = t.traceback
            if t.chain_error is not None:
                status = text.selection_error(t.chain_error)
            elif t.is_unstamped and t.matched_spec is not None:
                status = text.selection_unstamped_matched(t.matched_spec.spec_id)
            elif t.is_unstamped:
                status = text.selection_unstamped_unmatched(t.unmatched_reason)
            elif result.found and result.is_pattern_instance:
                status = text.selection_editing_via_pattern(result.spec_id)
            elif result.found:
                status = text.selection_editing(result.spec_id)
            else:
                status = text.selection_new_bead
            lines.append(text.selection_line(label, status))
        return tuple(lines)

    # ---- advisories ----

    def _warnings(self, part: BeadPart, targets: Sequence[BeadTarget]) -> str | None:
        text = self.text
        warnings: list[str] = []
        row, body = part.row, part.body
        if row is not None and body is not None:
            if body.physical_material is None:
                warnings.append(text.warning_body_has_no_material(body.name, row.physical_material_name))
            elif body.physical_material.casefold() != row.physical_material_name.casefold():
                warnings.append(
                    text.warning_body_material_differs(body.name, body.physical_material, row.physical_material_name)
                )
        unmatched = len({t.existing_feature_key for t in targets if t.unmatched_reason is not None})
        if unmatched:
            warnings.append(text.warning_unmatched_selected(unmatched))
        standard = part.standard
        others = list(dict.fromkeys(s for s, _, _ in _known_specs(targets) if not part.is_current_standard(s)))
        if others and standard is not None:
            warnings.append(text.warning_other_standards(others, standard.id))
        if self._unresolved:
            warnings.append(text.warning_unresolved_bead_specs(self._unresolved))
        unknown = [f"'{b.name}'" for b in self.constraining_beads(part, targets) if not b.is_spec_known]
        if unknown:
            warnings.append(text.warning_unknown_beads(unknown))
        warnings.extend(self._stale_requirement_warnings(part))
        return " ".join(warnings) if warnings else None

    def _stale_requirement_warnings(self, part: BeadPart) -> list[str]:
        """The SPECs the current sheet no longer suits, by the requirements the part holds. Re-applying the beads is
        the fix. A revised workbook is only reported, never rewritten here."""
        text = self.text
        warnings: list[str] = []
        if part.requirement_errors:
            warnings.append(text.warning_requirements_unreadable(" ".join(part.requirement_errors)))
        thickness, row = part.thickness, part.row
        for requirement in part.requirements:
            if thickness is None:
                continue
            allowance = requirement.allowance
            sheet = format_thickness(thickness)
            if row is not None and not allowance.is_satisfied_by(thickness, row.grade):
                warnings.append(
                    text.warning_requirement_refuses(requirement.label, row.grade, sheet, allowance.pairs_text)
                )
            elif not allowance.matches_built_thickness(thickness):
                warnings.append(text.warning_requirement_rebuild(requirement.label, allowance.thickness_text, sheet))
            bead = next(
                (
                    b
                    for b in part.beads
                    if b.is_spec_known
                    and b.standard_id is not None
                    and b.spec_id is not None
                    and SheetMetalRequirement.key_for(b.standard_id, b.spec_id) == requirement.key
                ),
                None,
            )
            if bead is not None and not SheetMetalAllowance.from_family(
                bead.spec_family, allowance.built_thickness
            ).same_pairs_as(allowance):
                warnings.append(text.warning_requirement_workbook_changed(requirement.label, allowance.pairs_text))
        return warnings

    def _status(self, part: BeadPart, targets: Sequence[BeadTarget], mode: str, picked: BeadSpecRow | None) -> str:
        """The debug block: not user text, so worded here."""
        body, p, row = part.body, part.preference, part.row
        if body is not None:
            thickness = format_short(body.thickness)
        elif p is not None:
            thickness = f"{format_short(p.thickness)} (from Sheet Metal Preferences)"
        else:
            thickness = "(unknown)"
        lines = [f"Body: {body.name if body else '(none)'}", f"Thickness: {thickness}"]
        if body is not None:
            lines.append(f"Material: {body.physical_material or '(none)'}")
        lines.append(
            "Sheet Metal Material: "
            + (
                f"{row.name} — {row.physical_material_name}, grade {row.grade} "
                "(from the part's Sheet Metal Preferences)"
                if row is not None
                else "(none — the Sheet Metal Preferences name no standards-file row)"
            )
        )
        if p is not None:
            lines.append(f"Preferences: {self._preference_state(p)}")
        if (allow := self._beads_allow(part, targets)) is not None:
            lines.append(f"Beads on this body allow: {allow}")
        lines.append(f"Mode: {mode}")
        if picked is not None:
            lines.append(f"SPEC Thickness: {format_short(picked.thickness)}")
            lines.append(f"Allowed Materials: {', '.join(picked.allowed_grades)}")
        return "\n".join(lines)

    def _beads_allow(self, part: BeadPart, targets: Sequence[BeadTarget]) -> str | None:
        thickness = part.thickness
        if thickness is None:
            return None
        beads = [b for b in self.constraining_beads(part, targets) if b.is_spec_known]
        if not beads:
            return None
        at = format_thickness(thickness)
        uncovered = [b for b in beads if b.spec_at(thickness) is None]
        if uncovered:
            return f"nothing at {at} in — " + ", ".join(
                f"SPEC {b.spec_id} covers {b.thickness_coverage}" for b in uncovered
            )
        grades = grades_allowed_by_all([r for b in beads if (r := b.spec_at(thickness)) is not None], thickness)
        if not grades:
            return f"nothing at {at} in"
        specs = ", ".join(dict.fromkeys(b.spec_id or "" for b in beads))
        return f"{', '.join(sorted(grades))} at {at} in (from {len(beads)} bead(s): {specs})"


def chain_error(bead_labels: Sequence[str], new_curves: int, text: SheetMetalText | None = None) -> str | None:
    """A chain is one bead: new when none of its curves belongs to a bead, an update when all belong to the same one.
    Curves of several beads, or a bead's curves mixed with new ones, cannot be one bead. `bead_labels` are the distinct
    beads the chain's curves belong to."""
    text = text or SheetMetalText.DEFAULT
    if not bead_labels or (len(bead_labels) == 1 and new_curves == 0):
        return None
    if len(bead_labels) == 1:
        return text.selection_mixed_with_new_curves(bead_labels[0], new_curves)
    return text.selection_spans_beads(bead_labels)


def _known_specs(targets: Sequence[BeadTarget]) -> list[tuple[str, str, str | None]]:
    return list(dict.fromkeys(k for t in targets if (k := t.known_spec) is not None))


def _same_row(a: BeadSpecRow, b: BeadSpecRow) -> bool:
    return (a.standard_id, a.workbook_name, a.spec_id) == (b.standard_id, b.workbook_name, b.spec_id) and (
        thickness_matches(a.thickness, b.thickness)
    )


def _match_bead_spec_name(names: Sequence[str], workbook_or_spec_name: str) -> str | None:
    """The bead SPEC whose name the workbook name contains (the rule the workbook was found by). The longest wins, so
    a SPEC name inside another cannot shadow it."""
    folded = workbook_or_spec_name.casefold()
    matches = [n for n in names if n.casefold() in folded]
    return max(matches, key=len) if matches else None
