"""Single source of truth for the application version.

Every service reads its version from here. The API serves it on
/api/health and declares it in the OpenAPI schema; the frontend holds no
version of its own and renders whatever /api/health reports, so the
number on screen is always the number the running API reports.

Bumping, on merge to main:
  branch vN.M merged  -> minor  (1.4.0 -> 1.5.0)
  hotfix onto main    -> patch  (1.5.0 -> 1.5.1)
  breaking API change -> major  (1.5.1 -> 2.0.0)

1.0.0 was the first deployed commit (bd88a3a, 2026-08-05). Everything between
it and 1.4.0 predates this constant and was numbered after the fact from the
history — see the release table in README.md. Format is enforced by
tests/test_version.py.
"""

VERSION = "1.4.0"
