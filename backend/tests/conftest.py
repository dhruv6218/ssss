"""Shared fixtures for ASTRIX AI backend tests."""
import os
import time
import uuid
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ["APP_URL"].rstrip("/")
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def supabase_admin_headers():
    return {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="session")
def test_user(supabase_admin_headers):
    """Create a verified test user via Supabase admin endpoint."""
    ts = int(time.time())
    email = f"test+{ts}@astrixai.app"
    password = "TestPass123!"
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=supabase_admin_headers,
        json={
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {"full_name": "Test User"},
        },
        timeout=30,
    )
    assert r.status_code in (200, 201), f"Admin create user failed: {r.status_code} {r.text}"
    user = r.json()
    user_id = user.get("id") or user.get("user", {}).get("id")
    assert user_id, f"No user id in {user}"

    # login
    lr = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30,
    )
    assert lr.status_code == 200, f"Login failed: {lr.status_code} {lr.text}"
    token = lr.json()["access_token"]

    yield {"id": user_id, "email": email, "password": password, "token": token}

    # cleanup
    try:
        requests.delete(
            f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers=supabase_admin_headers,
            timeout=15,
        )
    except Exception:
        pass


@pytest.fixture(scope="session")
def second_user(supabase_admin_headers):
    """Second user for RLS isolation testing."""
    ts = int(time.time()) + 1
    email = f"test+iso{ts}@astrixai.app"
    password = "TestPass123!"
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers=supabase_admin_headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30,
    )
    assert r.status_code in (200, 201), r.text
    uid = r.json().get("id") or r.json().get("user", {}).get("id")
    lr = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30,
    )
    assert lr.status_code == 200, lr.text
    yield {"id": uid, "email": email, "token": lr.json()["access_token"]}
    try:
        requests.delete(
            f"{SUPABASE_URL}/auth/v1/admin/users/{uid}",
            headers=supabase_admin_headers,
            timeout=15,
        )
    except Exception:
        pass


@pytest.fixture(scope="session")
def auth_headers(test_user):
    return {
        "Authorization": f"Bearer {test_user['token']}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="session")
def workspace(test_user, supabase_admin_headers):
    """Create a workspace via RPC create_workspace_with_owner using user JWT."""
    slug = f"test-ws-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    # Call as the user (RPC needs auth.uid()) — use user JWT
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/create_workspace_with_owner",
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {test_user['token']}",
            "Content-Type": "application/json",
        },
        json={"p_name": "TEST_Workspace", "p_slug": slug, "p_timezone": "UTC"},
        timeout=30,
    )
    assert r.status_code in (200, 201), f"create_workspace_with_owner failed: {r.status_code} {r.text}"
    res = r.json()
    # rpc may return id or row
    ws_id = res if isinstance(res, str) else (res.get("id") if isinstance(res, dict) else (res[0].get("id") if isinstance(res, list) and res else None))
    assert ws_id, f"No workspace id in response: {res}"

    # verify membership row exists
    mr = requests.get(
        f"{SUPABASE_URL}/rest/v1/workspace_members",
        headers=supabase_admin_headers,
        params={"workspace_id": f"eq.{ws_id}", "user_id": f"eq.{test_user['id']}"},
        timeout=15,
    )
    assert mr.status_code == 200 and len(mr.json()) >= 1, f"No member row: {mr.text}"

    yield {"id": ws_id, "slug": slug}

    # cleanup workspace (cascade deletes children)
    try:
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/workspaces",
            headers=supabase_admin_headers,
            params={"id": f"eq.{ws_id}"},
            timeout=15,
        )
    except Exception:
        pass
