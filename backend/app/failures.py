"""Turn a pipeline exception string into something safe to publish.

`sar_scenes.error` is arbitrary exception text; `GET /api/scenes` is
unauthenticated. This is the same classification `PassHistory.tsx` used to do
client-side, moved server-side so the raw text never crosses the network.
"""

from __future__ import annotations

# Ordered: first matching token wins, so specific causes come before generic ones.
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

# Reasons that mean the pipeline could not actually talk to the imagery API,
# as opposed to a downstream failure (coverage, detector, AIS window) that
# happened after a successful fetch.
_UNREACHABLE_REASONS = {"Imagery access rejected", "Imagery fetch timed out"}


def is_unreachable(error: str | None) -> bool:
    """True if `error` means the imagery API itself couldn't be reached."""
    return classify(error) in _UNREACHABLE_REASONS


def classify(error: str | None) -> str | None:
    """A publishable reason for a failed scene, or None if it did not fail.

    Closed set: unrecognised errors get the generic phrase, never the raw text.
    """
    if not error:
        return None
    lowered = error.lower()
    for tokens, reason in _REASONS:
        if any(token in lowered for token in tokens):
            return reason
    return UNKNOWN
