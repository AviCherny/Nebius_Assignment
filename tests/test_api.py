from fastapi.testclient import TestClient

import main


def _client(monkeypatch, ctx: str, llm_result: dict):
    async def fake_grab_repo(url: str) -> str:
        return ctx

    async def fake_ask_llm(context: str) -> dict:
        return llm_result

    monkeypatch.setattr(main, "grab_repo", fake_grab_repo)
    monkeypatch.setattr(main, "_ask_llm", fake_ask_llm)
    return TestClient(main.app)


def test_summarize_success(monkeypatch):
    client = _client(
        monkeypatch,
        "dummy context",
        {"summary": "ok", "technologies": ["python"], "structure": "flat"},
    )
    r = client.post("/summarize", json={"github_url": "https://github.com/psf/requests"})
    assert r.status_code == 200
    assert r.json()["summary"] == "ok"


def test_bad_url(monkeypatch):
    client = _client(
        monkeypatch,
        "dummy context",
        {"summary": "ok", "technologies": ["python"], "structure": "flat"},
    )
    r = client.post("/summarize", json={"github_url": "not-a-url"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["status"] == "error"
