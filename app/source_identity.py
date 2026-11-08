"""Identity defaults for legacy jobs; explicit sources must never be guessed.

These are the download engines currently implemented, not a provider registry.
Adding a search provider alone does not make its downloads compatible with one
of these engines.
"""

LEGACY_SOURCE_BY_KIND = {
    "film": "streamingcommunity",
    "episode": "streamingcommunity",
    "anime": "animeunity",
}


def download_source(kind: str, source: str | None = None) -> str:
    """Validate routing to an existing engine, defaulting only when omitted."""
    expected = LEGACY_SOURCE_BY_KIND.get(kind)
    if expected is None or (source is not None and source != expected):
        raise ValueError(f"Fonte {source!r} non supportata per {kind!r}")
    return expected
