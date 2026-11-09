import asyncio

from app.providers.contracts import ContentRef, SearchResult
from app.routers import search as search_router


class _Registry:
    def sources(self):
        return ("streamingcommunity", "animeunity")

    def search(self, *args, **kwargs):
        raise AssertionError("search is intercepted by the test")


def _result(source, external_id):
    return SearchResult(
        ContentRef(source, external_id, "movie"),
        f"{source} title",
        None,
        None,
        {"id": external_id, "name": f"{source} title", "type": "movie"},
    )


def test_unified_search_merges_sources_and_keeps_source(monkeypatch):
    async def fake_call(operation, source, query, **filters):
        return [_result(source, source)]

    monkeypatch.setattr(search_router, "call_provider", fake_call)
    rows = asyncio.run(search_router.search("term", source="all", registry=_Registry()))
    assert [row["source"] for row in rows] == ["streamingcommunity", "animeunity"]


def test_unified_search_keeps_other_source_when_one_fails(monkeypatch):
    async def fake_call(operation, source, query, **filters):
        if source == "streamingcommunity":
            from fastapi import HTTPException
            raise HTTPException(status_code=502, detail="offline")
        return [_result(source, source)]

    monkeypatch.setattr(search_router, "call_provider", fake_call)
    rows = asyncio.run(search_router.search("term", source="all", registry=_Registry()))
    assert [row["source"] for row in rows] == ["animeunity"]
