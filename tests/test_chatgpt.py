import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from gdpr_rag import chatgpt, llm, server, store

ENDPOINTS = {
    "authorization_endpoint": "https://auth.openai.com/api/accounts/authorize",
    "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
    "revocation_endpoint": "https://auth.openai.com/api/accounts/oauth/revoke",
}


def id_token(**claims) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    """Simulated OpenAI sign-in: records token requests, answers with the configured response."""
    monkeypatch.setattr(server, "DB", tmp_path / "app.db")
    monkeypatch.setattr(chatgpt, "_endpoints", lambda: ENDPOINTS)
    chatgpt._pending.clear()
    state = {"calls": [], "scope": chatgpt.SCOPES, "nonce": None}

    def post(url, form):
        state["calls"].append(form)
        if form["grant_type"] == "refresh_token":
            return {"access_token": "at-2", "refresh_token": "rt-2", "token_type": "Bearer",
                    "expires_in": 3600}
        return {"access_token": "at-1", "refresh_token": "rt-1", "token_type": "Bearer",
                "expires_in": 3600, "scope": state["scope"],
                "id_token": id_token(iss=chatgpt.ISSUER, aud=form["client_id"],
                                     nonce=state["nonce"], email="me@example.com")}

    monkeypatch.setattr(chatgpt, "_post", post)
    monkeypatch.setattr(chatgpt, "models", lambda token: [{"slug": "gpt-x", "name": "GPT X"},
                                                          {"slug": "gpt-x-mini", "name": "GPT X mini"}])
    return state


def sign_in(client, oauth, **callback):
    url = client.post("/api/chatgpt/start").json()["url"]
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    oauth["nonce"] = q["nonce"]
    params = {"code": "c-1", "state": q["state"], "client_id": "cid-1", **callback}
    return q, client.get("/auth/callback", params=params, follow_redirects=False)


def test_sign_in_round_trip_picks_models_and_detects_embeddings(oauth, monkeypatch):
    def embed(texts, model):
        if llm.is_chatgpt(model):
            raise RuntimeError("not allowed for plan tokens")
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(llm, "embed", embed)
    with TestClient(server.app, base_url="http://localhost") as client:
        q, r = sign_in(client, oauth)
        assert q["client_id"] == "dynamic_agent_client" and q["code_challenge_method"] == "S256"
        assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["agent_name_hint"] == "gdpr-rag"
        assert r.status_code == 303 and "chatgpt=connected" in r.headers["location"]
        assert oauth["calls"][0]["code_verifier"]  # PKCE verifier sent with the code exchange
        s = client.get("/api/settings").json()
        assert s["chatgpt"] == {"connected": True, "email": "me@example.com", "embeddings": "no"}
        assert (s["model"], s["fast_model"]) == ("gpt-x", "gpt-x-mini")
        assert s["embed_model"] == llm.EMBED_MODEL  # plan can't embed: OpenRouter keeps search

        # a second sign-in reuses the registered client instead of registering again
        url = client.post("/api/chatgpt/start").json()["url"]
        assert parse_qs(urlsplit(url).query)["client_id"] == ["cid-1"]

        out = client.post("/api/chatgpt/signout").json()
        assert out["chatgpt"]["connected"] is False and out["model"] == llm.ANSWER_MODEL


def test_sign_in_uses_plan_embeddings_when_allowed(oauth, monkeypatch):
    monkeypatch.setattr(llm, "embed", lambda texts, model: [[1.0, 0.0] for _ in texts])
    with TestClient(server.app, base_url="http://localhost") as client:
        sign_in(client, oauth)
        s = client.get("/api/settings").json()
        assert s["chatgpt"]["embeddings"] == "yes" and s["embed_model"] == server.CHATGPT_EMBED_MODEL
        client.post("/api/chatgpt/signout")


@pytest.mark.parametrize("problem, message", [
    ({"state": "forged"}, "expired"),
    ({"error": "access_denied"}, "cancelled"),
])
def test_callback_rejects_bad_returns(oauth, problem, message):
    with TestClient(server.app, base_url="http://localhost") as client:
        _, r = sign_in(client, oauth, **problem)
        assert "chatgpt_error=" in r.headers["location"] and message in r.headers["location"]
        assert client.get("/api/settings").json()["chatgpt"]["connected"] is False


def test_identity_only_sign_in_is_refused(oauth):
    oauth["scope"] = "openid profile email offline_access"  # no plan usage granted
    with TestClient(server.app, base_url="http://localhost") as client:
        _, r = sign_in(client, oauth)
        assert "Plus+or+Pro" in r.headers["location"]
        assert client.get("/api/settings").json()["chatgpt"]["connected"] is False


def test_expired_token_refreshes_and_rotates(oauth, tmp_path):
    db = store.connect(tmp_path / "t.db")
    store.set_setting(db, "chatgpt_client_id", "cid-1")
    store.set_setting(db, "chatgpt_session", json.dumps({
        "access_token": "old", "refresh_token": "rt-1", "expires_at": time.time() - 1,
        "scopes": [chatgpt.PLAN_SCOPE], "email": "me@example.com"}))
    assert chatgpt.access_token(db) == "at-2"
    assert oauth["calls"][-1]["refresh_token"] == "rt-1"
    saved = chatgpt.session(db)
    assert saved["refresh_token"] == "rt-2" and saved["email"] == "me@example.com"
    assert chatgpt.access_token(db) == "at-2" and len(oauth["calls"]) == 1  # still fresh: no call


def test_sign_out_revokes_with_openai(oauth, tmp_path, monkeypatch):
    revoked = []
    monkeypatch.setattr(chatgpt.httpx, "post", lambda url, **kw: revoked.append(kw["data"]))
    db = store.connect(tmp_path / "t.db")
    store.set_setting(db, "chatgpt_client_id", "cid-1")
    store.set_setting(db, "chatgpt_session", json.dumps({"access_token": "a", "refresh_token": "rt-9",
                                                         "expires_at": 0, "scopes": []}))
    chatgpt.sign_out(db)
    assert revoked[0]["token"] == "rt-9" and chatgpt.session(db) is None
    assert store.get_setting(db, "chatgpt_client_id") == "cid-1"  # registration kept for next time


def _fake_responses(events, seen):
    def create(**request):
        seen.update(request)
        return iter(SimpleNamespace(**e) for e in events)
    return SimpleNamespace(responses=SimpleNamespace(create=create))


def test_bare_model_names_go_to_the_chatgpt_responses_api(monkeypatch):
    seen = {}
    events = [{"type": "response.output_text.delta", "delta": "Hel"},
              {"type": "response.output_text.delta", "delta": "lo"},
              {"type": "response.completed"}]
    monkeypatch.setattr(llm, "_chatgpt", lambda: _fake_responses(events, seen))
    msgs = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}]
    assert llm.chat(msgs, "gpt-x", temperature=0) == "Hello"
    assert seen["instructions"] == "Be brief." and seen["input"] == [{"role": "user", "content": "Hi"}]
    assert seen["store"] is False and "temperature" not in seen
    assert llm.is_chatgpt("gpt-x") and not llm.is_chatgpt("anthropic/claude-haiku-5.5")


def test_unfinished_chatgpt_stream_is_an_error(monkeypatch):
    monkeypatch.setattr(llm, "_chatgpt", lambda: _fake_responses(
        [{"type": "response.output_text.delta", "delta": "Half"}], {}))
    with pytest.raises(RuntimeError, match="ended early"):
        llm.chat([{"role": "user", "content": "Hi"}], "gpt-x")
