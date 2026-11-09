"""Sign in with ChatGPT: use a ChatGPT Plus/Pro plan instead of an API key.

Implements OpenAI's documented flow for open-source, locally run apps (OAuth 2 + PKCE with a
dynamically registered client, loopback redirect). Protocol per OpenAI's SIWC docs and devkit;
this is an independent Python implementation, not a copy of the devkit.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from functools import cache

import httpx

from . import store

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"  # without it the sign-in is identity only, no plan usage
APP_NAME = "gdpr-rag"
CALLBACK_PATH = "/auth/callback"
PENDING_TTL = 600  # seconds a started sign-in stays valid

_pending: dict[str, dict] = {}  # state -> {verifier, nonce, redirect_uri, created}
_lock = threading.Lock()  # one refresh at a time; refresh tokens rotate


class SignInError(Exception):
    """Shown to the user as is."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@cache
def _endpoints() -> dict:
    r = httpx.get(f"{ISSUER}/.well-known/openid-configuration", timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("issuer") != ISSUER:
        raise SignInError("ChatGPT sign-in returned an unexpected issuer.")
    return data


def _post(url: str, form: dict) -> dict:
    r = httpx.post(url, data=form, headers={"accept": "application/json"}, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        detail = data.get("error_description") or data.get("error") or f"HTTP {r.status_code}"
        raise SignInError(f"ChatGPT sign-in failed: {detail}")
    return data


def _host_id(db) -> str:
    """A stable per-installation id OpenAI uses to recognise this app across sign-ins."""
    host = store.get_setting(db, "chatgpt_host_id")
    if not host:
        host = f"urn:uuid:{uuid.uuid4()}"
        store.set_setting(db, "chatgpt_host_id", host)
    return host


def start(db, port: int) -> str:
    """Begin a sign-in; returns the URL to open in the browser."""
    now = time.time()
    for stale in [s for s, p in _pending.items() if now - p["created"] > PENDING_TTL]:
        _pending.pop(stale, None)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    client_id = store.get_setting(db, "chatgpt_client_id")  # reuse the registration if we have one
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    _pending[state] = {"verifier": verifier, "nonce": nonce, "redirect_uri": redirect_uri,
                       "created": now}
    params = {
        "client_id": client_id or "dynamic_agent_client", "response_type": "code",
        "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
        "state": state, "nonce": nonce, "code_challenge_method": "S256",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "ext_agent_host_id": _host_id(db),
    }
    if not client_id:
        params["agent_name_hint"] = APP_NAME
    return str(httpx.URL(_endpoints()["authorization_endpoint"], params=params))


def _claims(id_token: str) -> dict:
    """ID token payload. It comes straight from the token endpoint over TLS, which OpenID Connect
    accepts in place of a signature check (Core 1.0, 3.1.3.7); issuer, audience and nonce are
    still checked."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise SignInError("ChatGPT returned an unreadable identity.") from e


def _session(data: dict, previous: dict | None = None) -> dict:
    scopes = (data.get("scope") or " ".join((previous or {}).get("scopes", []))).split()
    if data.get("token_type", "").lower() != "bearer" or not data.get("access_token"):
        raise SignInError("ChatGPT returned incomplete credentials. Please sign in again.")
    refresh = data.get("refresh_token") or (previous or {}).get("refresh_token")
    return {
        "access_token": data["access_token"],
        "refresh_token": refresh,
        "expires_at": time.time() + int(data.get("expires_in") or 3600) - 60,
        "scopes": scopes,
        "email": (previous or {}).get("email"),
    }


def finish(db, state: str, code: str, client_id: str | None) -> dict:
    """Handle the browser's return to /auth/callback: exchange the code and store the session."""
    pending = _pending.pop(state or "", None)  # single use
    if not pending or time.time() - pending["created"] > PENDING_TTL:
        raise SignInError("This sign-in link expired or was already used. Start again in Settings.")
    client_id = client_id or store.get_setting(db, "chatgpt_client_id")
    if not code or not client_id:
        raise SignInError("ChatGPT didn't return a sign-in code. Start again in Settings.")
    # keep the registration even if the exchange below fails, so a retry doesn't register again
    store.set_setting(db, "chatgpt_client_id", client_id)
    data = _post(_endpoints()["token_endpoint"], {
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"],
        "resource": RESOURCE,
    })
    claims = _claims(data.get("id_token") or "")
    audience = claims.get("aud")
    if (claims.get("iss") != ISSUER or claims.get("nonce") != pending["nonce"]
            or client_id not in (audience if isinstance(audience, list) else [audience])):
        raise SignInError("ChatGPT's identity response didn't match this sign-in. Please try again.")
    session = _session(data)
    if PLAN_SCOPE not in session["scopes"]:
        raise SignInError("Signed in, but ChatGPT didn't allow plan usage. It needs a Plus or Pro plan.")
    session["email"] = claims.get("email")
    store.set_setting(db, "chatgpt_session", json.dumps(session))
    return session


def session(db) -> dict | None:
    raw = store.get_setting(db, "chatgpt_session")
    return json.loads(raw) if raw else None


def access_token(db) -> str:
    """A valid access token, refreshed when it's about to expire."""
    with _lock:
        current = session(db)
        if not current:
            raise SignInError("Not signed in with ChatGPT.")
        if time.time() < current["expires_at"]:
            return current["access_token"]
        if not current.get("refresh_token"):
            raise SignInError("Your ChatGPT sign-in expired. Sign in again in Settings.")
        data = _post(_endpoints()["token_endpoint"], {
            "grant_type": "refresh_token", "client_id": store.get_setting(db, "chatgpt_client_id"),
            "refresh_token": current["refresh_token"], "resource": RESOURCE,
        })
        renewed = _session(data, current)
        store.set_setting(db, "chatgpt_session", json.dumps(renewed))
        return renewed["access_token"]


def sign_out(db) -> None:
    """Revoke the session with OpenAI (best effort) and forget it; the app registration stays."""
    current = session(db)
    if current and current.get("refresh_token"):
        try:
            httpx.post(_endpoints()["revocation_endpoint"], timeout=15, data={
                "token": current["refresh_token"], "token_type_hint": "refresh_token",
                "client_id": store.get_setting(db, "chatgpt_client_id"),
            })
        except httpx.HTTPError:
            pass  # forgetting the token locally is what matters; it also expires on its own
    store.set_setting(db, "chatgpt_session", None)


def models(token: str) -> list[dict]:
    """Models the signed-in plan can use: [{slug, name}]."""
    r = httpx.get(f"{RESOURCE}/models", timeout=20,
                  headers={"authorization": f"Bearer {token}", "accept": "application/json"})
    r.raise_for_status()
    return [{"slug": m["slug"], "name": m.get("display_name") or m["slug"]}
            for m in r.json().get("models", [])
            if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug")]
