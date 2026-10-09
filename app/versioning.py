from __future__ import annotations

from packaging.version import InvalidVersion, Version


def parse_version(value: str) -> Version | None:
    """Parse a ``bundle_version`` (``YYYY.MM.DD`` or ``YYYY.MM.N``) or an app
    version (``1.4.2``). Returns ``None`` if unparseable."""
    if not value:
        return None
    try:
        return Version(value.strip())
    except (InvalidVersion, AttributeError):
        return None


def is_newer(candidate: str, current: str | None) -> bool:
    """True if ``candidate`` is a strictly newer release than ``current``.

    An unknown / unparseable ``current`` is treated as "older than anything", so
    a client that reports garbage still gets offered the update.
    """
    cand = parse_version(candidate)
    if cand is None:
        return False
    if not current:
        return True
    cur = parse_version(current)
    if cur is None:
        return True
    return cand > cur


def app_supported(app_version: str | None, min_app_version: str | None) -> bool:
    """True if an app at ``app_version`` may receive updates on a channel whose
    floor is ``min_app_version``.

    Missing / unparseable inputs resolve to ``True`` — this is a demo server and
    should not hard-block a client over a malformed version string.
    """
    if not app_version or not min_app_version:
        return True
    app = parse_version(app_version)
    floor = parse_version(min_app_version)
    if app is None or floor is None:
        return True
    return app >= floor
