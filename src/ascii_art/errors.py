"""Exceptions with the exit codes the CLI contract promises.

Exit codes are fixed by the design (report section 6.1):

* ``0`` success
* ``1`` usage error
* ``2`` input error

Every error is written to stderr; stdout is never used for diagnostics.
"""

from __future__ import annotations


class AsciiArtError(Exception):
    """Base class for every error the tool reports to the user."""

    exit_code = 2


class UsageError(AsciiArtError):
    """Bad command line: unknown flag, contradictory options, bad value."""

    exit_code = 1


class InputError(AsciiArtError):
    """The input could not be read or decoded, or writing output failed."""

    exit_code = 2
