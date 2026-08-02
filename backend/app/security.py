"""Shared constant-time API-key check.

Lives apart from main.py because devtools.py needs it too, and main.py imports
devtools.py to register its router.
"""

import secrets

from fastapi import HTTPException


def check_admin_key(
    provided: str | None,
    configured: str | None,
    *,
    what: str = "analysis",
    setting: str = "ANALYSIS_API_KEY",
    header: str = "X-Analysis-Key",
) -> None:
    if not configured:
        raise HTTPException(
            status_code=503, detail=f"{what} disabled: {setting} not configured"
        )
    # compare_digest raises TypeError on non-ASCII str, which would surface as a
    # 500 instead of a 401. Compare bytes so any header value is just wrong.
    provided_bytes = (provided or "").encode("utf-8")
    if not provided or not secrets.compare_digest(provided_bytes, configured.encode("utf-8")):
        raise HTTPException(
            status_code=401, detail=f"invalid or missing {header} header"
        )
