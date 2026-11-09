"""Bead features in NX: what counts as one, the provenance stamp, tracing a selection back to a bead (through
patterns), measuring an unstamped one, naming, and the per-body inventory the bead constraint reads."""

# NX 2412's Python stubs declare instance methods without `self`, type enum members as int and give collections
# no __iter__; these rules would only report those stub defects here. Everything else is still checked.
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportReturnType=false
# pyright: reportCallIssue=false, reportGeneralTypeIssues=false

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

import NXOpen
import NXOpen.Features

from banxopen.foundation import BodyId
from banxopen.foundation.nx import BodyResolver, NxSessionContext, nx_error_detail
from banxopen.sheetmetal import (
    NO_BEAD,
    UNSTAMPED_BEAD,
    BeadGeometry,
    BeadTracebackResult,
    BodyFeatureInventory,
    FeatureSpecStamp,
    UnstampedFeature,
    __version__,
)

_log = logging.getLogger(__name__)

BEAD_FEATURE_TYPE: Final = "BEAD"
"""BEST GUESS: inferred from NX's short-code naming of sheet metal feature types, not yet confirmed against a live
`Feature.FeatureType`. [verify] with scripts/smoke_sheetmetal.py."""


def is_bead_feature(feature: NXOpen.Features.Feature) -> bool:
    """One place, so the traceback and the selection expander agree on what a bead is."""
    try:
        return str(feature.FeatureType).upper() == BEAD_FEATURE_TYPE
    except NXOpen.NXException:
        return False


def feature_label(feature: NXOpen.Features.Feature) -> str:
    try:
        return feature.GetFeatureName()
    except NXOpen.NXException:
        return feature.JournalIdentifier


class BeadAttributes:
    """Stamps every built or updated bead with the tool, version and SPEC that drove it: the Standard and SPEC make the
    traceback possible, the dates are an audit trail."""

    APP_NAME: Final = "BEAD_APP_NAME"
    APP_VERSION: Final = "BEAD_APP_VERSION"
    CREATED_UTC: Final = "BEAD_CREATED_UTC"
    MODIFIED_UTC: Final = "BEAD_MODIFIED_UTC"
    STANDARD_ID: Final = "BEAD_STANDARD_ID"
    SPEC_ID: Final = "BEAD_SPEC_ID"
    BEAD_SPEC: Final = "BEAD_SPEC_NAME"
    """The bead SPEC name the dialog offers, not the workbook's file name. Optional when read: older stamps lack it."""
    _APP: Final = "NxSheetMetalBead"
    _NOT_AN_ARRAY: Final = -1
    _LEGACY_ARRAY_INDEX: Final = 0
    """Beads stamped before the index fix carry their attributes as one-element arrays."""

    @staticmethod
    def stamp(feature: NXOpen.Features.Feature, standard_id: str, bead_spec: str, spec_id: str, is_new: bool) -> None:
        """Update.Option.Later: an attribute does not change geometry, and Now ran a full update per attribute."""
        now = datetime.now(UTC).isoformat()
        values = {
            BeadAttributes.APP_NAME: BeadAttributes._APP,
            BeadAttributes.APP_VERSION: __version__,
            BeadAttributes.STANDARD_ID: standard_id,
            BeadAttributes.BEAD_SPEC: bead_spec,
            BeadAttributes.SPEC_ID: spec_id,
            BeadAttributes.MODIFIED_UTC: now,
        }
        if is_new:
            values[BeadAttributes.CREATED_UTC] = now
        for title, value in values.items():
            # A bead re-stamped now drops the old array form, so it never carries both.
            if feature.HasUserAttribute(
                title, NXOpen.NXObject.AttributeType.String, BeadAttributes._LEGACY_ARRAY_INDEX
            ):
                feature.DeleteUserAttribute(
                    NXOpen.NXObject.AttributeType.String, title, True, NXOpen.Update.Option.Later
                )
            feature.SetUserAttribute(title, BeadAttributes._NOT_AN_ARRAY, value, NXOpen.Update.Option.Later)

    @staticmethod
    def read(feature: NXOpen.Features.Feature, title: str) -> str | None:
        """The value, plain or (from an older version) a one-element array; None when absent."""
        for index in (BeadAttributes._NOT_AN_ARRAY, BeadAttributes._LEGACY_ARRAY_INDEX):
            if feature.HasUserAttribute(title, NXOpen.NXObject.AttributeType.String, index):
                return feature.GetStringUserAttribute(title, index)
        return None

    @staticmethod
    def read_stamp(feature: NXOpen.Features.Feature) -> BeadTracebackResult:
        standard_id = BeadAttributes.read(feature, BeadAttributes.STANDARD_ID)
        spec_id = BeadAttributes.read(feature, BeadAttributes.SPEC_ID)
        if standard_id is None or spec_id is None:
            return UNSTAMPED_BEAD
        created = BeadAttributes.read(feature, BeadAttributes.CREATED_UTC)
        try:
            created_utc = datetime.fromisoformat(created) if created else None
        except ValueError:
            created_utc = None
        return BeadTracebackResult(
            True,
            False,
            standard_id,
            spec_id,
            created_utc,
            bead_spec=BeadAttributes.read(feature, BeadAttributes.BEAD_SPEC),
        )


def apply_spec_name(feature: NXOpen.Features.Feature, spec_id: str) -> bool:
    """Puts the SPEC in front of the feature's own name ("B1005010-2_BEAD(12)"), replacing a prefix this wrote for a
    previous SPEC (read off the stamp, so call it BEFORE `BeadAttributes.stamp`). Cosmetic: a refused rename is logged,
    never a reason to fail the bead."""
    try:
        current = feature.GetFeatureName()
    except NXOpen.NXException as ex:
        _log.warning("Could not read a bead's name to put SPEC %s in front of it: %s", spec_id, nx_error_detail(ex))
        return False
    try:
        previous = BeadAttributes.read(feature, BeadAttributes.SPEC_ID)
    except NXOpen.NXException:
        previous = None
    base = current
    if previous and current.upper().startswith(f"{previous}_".upper()):
        base = current[len(previous) + 1 :]
    name = f"{spec_id}_{base}"
    if name == current:
        return True
    try:
        feature.SetName(name)
        return True
    except NXOpen.NXException as ex:
        _log.warning(
            "Could not rename bead '%s' to '%s': %s. The bead itself is built and stamped with SPEC %s.",
            current,
            name,
            nx_error_detail(ex),
            spec_id,
        )
        return False


@dataclass(frozen=True, slots=True)
class Traceback:
    """`existing_feature` is the bead a selection resolves to: for a pattern member, its stamped original."""

    existing_feature: NXOpen.Features.Feature | None
    result: BeadTracebackResult


class BeadTraceback:
    """Whether a selection (a curve, or a Bead feature picked directly) belongs to an existing bead, and its stamp.
    A pattern does not copy attributes onto its members, so an unstamped member is followed to the stamped sibling.

    BEST GUESS, to confirm on a live patterned bead: the stamped sibling is the original (NXOpen has no "pattern seed"
    API), and editing a pattern-traced bead re-opens the original's builder."""

    def __init__(self, context: NxSessionContext) -> None:
        self._context = context

    def trace(self, selection: NXOpen.NXObject) -> Traceback:
        if isinstance(selection, NXOpen.Features.Feature):
            candidate = selection if is_bead_feature(selection) else None
        else:
            candidate = self._bead_using_curve(selection)
        if candidate is None:
            return Traceback(None, NO_BEAD)
        own = BeadAttributes.read_stamp(candidate)
        if own.found:
            return Traceback(candidate, own)
        original = self._stamped_pattern_original(candidate)
        if original is not None:
            return Traceback(original, replace(BeadAttributes.read_stamp(original), is_pattern_instance=True))
        return Traceback(candidate, UNSTAMPED_BEAD)

    def _bead_using_curve(self, curve: NXOpen.NXObject) -> NXOpen.Features.Feature | None:
        for feature in self._context.work_part.Features:
            if is_bead_feature(feature) and self._uses_curve(feature, curve):
                return feature
        return None

    def _uses_curve(self, feature: NXOpen.Features.Feature, curve: NXOpen.NXObject) -> bool:
        """Off the feature's own sections: an edit builder per bead rolled the model back while open."""
        try:
            return any(c.Tag == curve.Tag for section in feature.GetSections() for c in section.GetOutputCurves())
        except NXOpen.NXException as ex:
            _log.warning("Could not read the sections of '%s': %s", feature_label(feature), nx_error_detail(ex))
            return False

    def _stamped_pattern_original(self, instance: NXOpen.Features.Feature) -> NXOpen.Features.Feature | None:
        for feature in self._context.work_part.Features:
            if not isinstance(feature, NXOpen.Features.PatternFeature):
                continue
            try:
                members = list(feature.GetAllContainedFeatures())
            except NXOpen.NXException as ex:
                _log.warning(
                    "Could not read the members of pattern '%s': %s", feature_label(feature), nx_error_detail(ex)
                )
                continue
            if any(m.Tag == instance.Tag for m in members):
                stamped = next((m for m in members if is_bead_feature(m) and BeadAttributes.read_stamp(m).found), None)
                if stamped is not None:
                    return stamped
        return None


def read_bead_geometry(
    context: NxSessionContext, feature: NXOpen.Features.Feature, thickness: float
) -> BeadGeometry | None:
    """Reads back what the feature service writes (Height, Radius, DieRadius), in the units it writes them, so a bead
    the tool built round-trips to its SPEC. None when the bead cannot be opened: "cannot be identified"."""
    manager = context.work_part.Features.SheetmetalManager
    try:
        builder = manager.CreateBeadFeatureBuilder(feature)
    except NXOpen.NXException as ex:
        _log.warning("Could not open bead '%s' to read its geometry: %s", feature_label(feature), nx_error_detail(ex))
        return None
    try:
        return BeadGeometry(thickness, builder.Height.Value, builder.Radius.Value, builder.DieRadius.Value)
    except NXOpen.NXException as ex:
        _log.warning("Could not read the parameters of bead '%s': %s", feature_label(feature), nx_error_detail(ex))
        return None
    finally:
        builder.Destroy()  # opened only to read; destroying without committing leaves the feature untouched


class NxBeadInventory:
    """The beads on one body, for the bead constraint and the bead tool. Stamps are read through the traceback, so a
    pattern member resolves to its original exactly as the bead tool resolves it. Every feature is scanned per call."""

    def __init__(self, context: NxSessionContext, traceback: BeadTraceback) -> None:
        self._context = context
        self._traceback = traceback

    def read(self, body_id: BodyId) -> BodyFeatureInventory:
        try:
            features = list(self._context.work_part.Features)
        except NXOpen.NXException as ex:
            _log.error(
                "Could not enumerate features while checking bead SPECs on '%s': %s", body_id, nx_error_detail(ex)
            )
            return BodyFeatureInventory.unreadable(nx_error_detail(ex))

        stamps: list[FeatureSpecStamp] = []
        unstamped: list[UnstampedFeature] = []
        thickness: float | None = None
        thickness_read = False
        for feature in features:
            body = self._body_of(feature, body_id)
            if body is None:
                continue
            trace = self._traceback.trace(feature)
            bead = trace.existing_feature or feature
            key = str(bead.Tag)  # a pattern member follows its original, so selecting the original lifts both
            result = trace.result
            if result.found and result.standard_id is not None and result.spec_id is not None:
                stamps.append(FeatureSpecStamp(result.standard_id, result.spec_id, key, feature_label(feature)))
            elif result.has_unstamped_feature:
                if not thickness_read:
                    thickness, thickness_read = self._thickness_of(body), True
                geometry = read_bead_geometry(self._context, bead, thickness) if thickness is not None else None
                unstamped.append(UnstampedFeature(feature_label(feature), geometry, key))
        return BodyFeatureInventory(tuple(stamps), tuple(unstamped))

    def _thickness_of(self, body: NXOpen.Body) -> float | None:
        try:
            return self._context.work_part.Features.SheetmetalManager.GetBodyThickness(body)
        except NXOpen.NXException as ex:
            _log.warning("Could not read the thickness of '%s': %s", body.Name, nx_error_detail(ex))
            return None

    @staticmethod
    def _body_of(feature: NXOpen.Features.Feature, body_id: BodyId) -> NXOpen.Body | None:
        try:
            return next((b for b in feature.GetBodies() if BodyResolver.body_id(b) == body_id), None)
        except NXOpen.NXException:
            return None  # datums and sketches have no body and throw; they are never beads
