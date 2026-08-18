"""Single source of truth for the application version.

Every service reads its version from here. The API serves it on
/api/health and declares it in the OpenAPI schema; the frontend holds no
version of its own and renders whatever /api/health reports, so the
number on screen is always the number the running API reports.

Bumping, on merge to main:
  branch vN.M merged  -> minor  (1.0.0 -> 1.1.0)
  hotfix onto main    -> patch  (1.1.0 -> 1.1.1)
  breaking API change -> major  (1.1.1 -> 2.0.0)

Format is enforced by tests/test_version.py.
"""

VERSION = "1.0.0"
