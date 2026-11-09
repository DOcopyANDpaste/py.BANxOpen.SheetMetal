"""Failures that mean "this configuration or spec data cannot be used", as opposed to a bug."""

from __future__ import annotations


class SpecDataError(ValueError):
    """A file that exists but is not valid: settings, the standards file, a SPEC workbook, a Standard folder name.
    The message is user-facing (`SheetMetalText`). The C# `InvalidDataException`."""


def is_spec_data_failure(ex: BaseException) -> bool:
    """Unreadable or invalid spec data: skipped with a warning by the searches, never a crash of the dialog."""
    return isinstance(ex, OSError | SpecDataError)
