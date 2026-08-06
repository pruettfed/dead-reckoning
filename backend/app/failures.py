"""Turn a pipeline exception string into something safe to publish.

`sar_scenes.error` holds 500 characters of arbitrary exception text: CDSE
request URLs, SQLAlchemy statements and their parameters, file paths, whatever
the failing library chose to say. `GET /api/scenes` is unauthenticated, so
serving that column raw published all of it to anyone who asked.

The frontend never wanted the raw string either — it reduced it to one of a
handful of phrases for display. Doing that classification here instead means
the raw text stops crossing the network at all, while the UI shows exactly what
it showed before. The strings are the ones `PassHistory.tsx` used, so this is a
move rather than a rewrite.

The raw column is still written (redacted) and still readable over psql or the
scripts, which is where an operator debugging a failure actually looks.
"""

from __future__ import annotations

# Ordered: the first pattern whose token appears wins, so put the specific
# causes ahead of the generic ones. Matched case-insensitively against the
# stored error.
_REASONS: list[tuple[tuple[str, ...], str]] = [
    (("real data", "coverage"), "Swath missed the box"),
    (("credential", "401", "403"), "Imagery access rejected"),
    (("timeout", "timed out"), "Imagery fetch timed out"),
    (("model checkpoint", "dependencies"), "Detector unavailable"),
    (("killed for memory", "subprocess died"), "Detector ran out of memory"),
    (("interrupted by restart",), "Interrupted by a restart"),
    (("ais",), "No AIS reference in window"),
]

UNKNOWN = "Analysis error"


def classify(error: str | None) -> str | None:
    """A publishable reason for a failed scene, or None if it did not fail.

    Deliberately closed: anything unrecognised reads as the generic phrase
    rather than falling back to the exception text. An open fallback is how the
    raw string would leak back out the moment a new error type appeared.
    """
    if not error:
        return None
    lowered = error.lower()
    for tokens, reason in _REASONS:
        if any(token in lowered for token in tokens):
            return reason
    return UNKNOWN
