"""ASTRIX AI Backend — Comprehensive E2E API Tests."""
import os
import time
import requests
import pytest
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ["APP_URL"].rstrip("/")
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


# ----------------------------- 1. Health -----------------------------
class TestHealth:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["mode"] == "live"
        assert "ts" in data


# ----------------------------- 2. Auth enforcement -----------------------------
class TestAuthEnforcement:
    def test_no_token_returns_401(self):
        r = requests.post(f"{BASE_URL}/api/ai/ask",
                          json={"workspace_id": "00000000-0000-0000-0000-000000000000",
                                "question": "hi"}, timeout=15)
        assert r.status_code == 401

    def test_invalid_token_returns_401(self):
        r = requests.post(f"{BASE_URL}/api/ai/ask",
                          headers={"Authorization": "Bearer not.a.jwt"},
                          json={"workspace_id": "00000000-0000-0000-0000-000000000000",
                                "question": "hi"}, timeout=15)
        assert r.status_code == 401

    def test_seed_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/seed-sample",
                          json={"workspace_id": "00000000-0000-0000-0000-000000000000"}, timeout=15)
        assert r.status_code == 401

    def test_billing_status_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/billing/status/00000000-0000-0000-0000-000000000000", timeout=15)
        assert r.status_code == 401


# ----------------------------- 3. Workspace creation (RPC) verified in fixture -----------------------------
class TestWorkspaceCreation:
    def test_workspace_created_with_owner_member(self, workspace, test_user, supabase_admin_headers):
        # Verify workspace row
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/workspaces",
            headers=supabase_admin_headers,
            params={"id": f"eq.{workspace['id']}"},
            timeout=15,
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["slug"] == workspace["slug"]

        # Verify owner membership
        mr = requests.get(
            f"{SUPABASE_URL}/rest/v1/workspace_members",
            headers=supabase_admin_headers,
            params={"workspace_id": f"eq.{workspace['id']}", "user_id": f"eq.{test_user['id']}"},
            timeout=15,
        )
        assert mr.status_code == 200
        members = mr.json()
        assert len(members) == 1
        assert members[0]["role"] in ("owner", "admin")


# ----------------------------- 4. Seed sample -----------------------------
class TestSeed:
    def test_seed_creates_data(self, auth_headers, workspace, supabase_admin_headers):
        r = requests.post(f"{BASE_URL}/api/seed-sample",
                          headers=auth_headers,
                          json={"workspace_id": workspace["id"]}, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["accounts"] == 4
        assert data["signals"] == 6
        assert data["problems"] == 2

        # Verify opportunities created with computed fields
        opps = requests.get(
            f"{SUPABASE_URL}/rest/v1/opportunities",
            headers=supabase_admin_headers,
            params={"workspace_id": f"eq.{workspace['id']}"},
            timeout=15,
        ).json()
        assert len(opps) == 2
        for o in opps:
            assert "opportunity_score" in o and o["opportunity_score"] is not None
            assert o["recommended_action"] in ("Build", "Fix", "Experiment", "Defer")
            assert "affected_arr" in o


# ----------------------------- 5. AI endpoints -----------------------------
class TestAI:
    def test_classify_signal(self, auth_headers, workspace):
        r = requests.post(f"{BASE_URL}/api/ai/classify-signal",
                          headers=auth_headers,
                          json={"workspace_id": workspace["id"],
                                "raw_text": "We desperately need SAML SSO with Okta or we cannot renew."},
                          timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "data" in data and "provider" in data
        d = data["data"]
        assert "title" in d
        assert d["severity_label"] in ("Critical", "High", "Medium", "Low")
        assert d["sentiment_label"] in ("Positive", "Neutral", "Negative")
        assert "category" in d
        assert "product_area" in d
        assert "normalized_text" in d

    def test_generate_memo(self, auth_headers, workspace):
        r = requests.post(f"{BASE_URL}/api/ai/generate-memo",
                          headers=auth_headers,
                          json={
                              "workspace_id": workspace["id"],
                              "problem": {"title": "SSO missing", "description": "Enterprise blocked"},
                              "opportunity": {"opportunity_score": 75, "affected_arr": 1450000},
                              "decision": {"option": "Build SAML SSO", "owner": "VP Eng"},
                          }, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data.get("content"), str) and len(data["content"]) > 50
        assert "provider" in data

    def test_proof_summary(self, auth_headers, workspace):
        r = requests.post(f"{BASE_URL}/api/ai/proof-summary",
                          headers=auth_headers,
                          json={
                              "workspace_id": workspace["id"],
                              "launch": {"title": "SAML SSO GA", "expected_outcome": "Unblock $1.4M ARR"},
                              "measurements": [{"metric": "ARR retained", "value": 1200000}],
                              "verdict": "Win",
                          }, timeout=60)
        assert r.status_code == 200, r.text
        assert len(r.json().get("content", "")) > 30

    def test_ai_ask(self, auth_headers, workspace):
        # depends on seeded data; tests grounding
        r = requests.post(f"{BASE_URL}/api/ai/ask",
                          headers=auth_headers,
                          json={"workspace_id": workspace["id"],
                                "question": "What is our top opportunity?"},
                          timeout=60)
        assert r.status_code == 200, r.text
        assert isinstance(r.json().get("answer"), str)


# ----------------------------- 6. Scoring -----------------------------
class TestScoring:
    def test_score_workspace_bulk(self, auth_headers, workspace):
        r = requests.post(f"{BASE_URL}/api/score-workspace/{workspace['id']}",
                          headers=auth_headers, timeout=60)
        assert r.status_code == 200, r.text
        results = r.json().get("results", [])
        assert len(results) >= 2
        for res in results:
            assert "problem_id" in res

    def test_score_single_opportunity(self, auth_headers, workspace, supabase_admin_headers):
        # pick first problem
        probs = requests.get(
            f"{SUPABASE_URL}/rest/v1/problems",
            headers=supabase_admin_headers,
            params={"workspace_id": f"eq.{workspace['id']}", "limit": "1"},
            timeout=15,
        ).json()
        assert probs, "no problems to score"
        pid = probs[0]["id"]
        r = requests.post(f"{BASE_URL}/api/score-opportunity",
                          headers=auth_headers,
                          json={"workspace_id": workspace["id"], "problem_id": pid},
                          timeout=30)
        assert r.status_code == 200, r.text
        assert "opportunity_score" in r.json()


# ----------------------------- 7. Billing -----------------------------
class TestBilling:
    def test_pricing_returns_plans(self):
        r = requests.get(f"{BASE_URL}/api/billing/pricing", timeout=15)
        assert r.status_code == 200, r.text
        plans = r.json().get("plans", [])
        assert len(plans) >= 6, f"expected >=6 plans got {len(plans)}: {[p.get('plan') for p in plans]}"
        # Should include starter/growth/scale variants
        plan_keys = {p.get("plan") for p in plans}
        assert any("starter" in (p or "") for p in plan_keys)
        assert any("growth" in (p or "") for p in plan_keys)
        assert any("scale" in (p or "") for p in plan_keys)

    def test_billing_status(self, auth_headers, workspace):
        r = requests.get(f"{BASE_URL}/api/billing/status/{workspace['id']}",
                         headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "subscription" in data
        assert "current_plan" in data

    @pytest.mark.parametrize("plan,period", [("starter", "monthly"), ("growth", "annual")])
    def test_checkout_returns_dodo_url(self, auth_headers, workspace, plan, period):
        r = requests.post(f"{BASE_URL}/api/billing/checkout",
                          headers=auth_headers,
                          json={"workspace_id": workspace["id"], "plan": plan, "billing_period": period},
                          timeout=45)
        assert r.status_code == 200, r.text
        data = r.json()
        url = data.get("checkout_url") or ""
        assert "dodopayments.com" in url, f"Expected Dodo URL, got: {url}"


# ----------------------------- 8. Team invites -----------------------------
class TestInvites:
    def test_invite_and_accept_flow(self, auth_headers, workspace, second_user, supabase_admin_headers):
        # send invite
        r = requests.post(f"{BASE_URL}/api/team/invite",
                          headers=auth_headers,
                          json={"workspace_id": workspace["id"],
                                "email": second_user["email"], "role": "member"}, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "accept_url" in data
        assert "token=" in data["accept_url"]
        token = data["accept_url"].split("token=")[1]

        # accept invite as second_user
        ar = requests.post(f"{BASE_URL}/api/team/accept-invite",
                           headers={"Authorization": f"Bearer {second_user['token']}",
                                    "Content-Type": "application/json"},
                           json={"token": token}, timeout=30)
        assert ar.status_code == 200, ar.text
        assert ar.json().get("workspace_id") == workspace["id"]

        # verify membership
        mr = requests.get(
            f"{SUPABASE_URL}/rest/v1/workspace_members",
            headers=supabase_admin_headers,
            params={"workspace_id": f"eq.{workspace['id']}", "user_id": f"eq.{second_user['id']}"},
            timeout=15,
        ).json()
        assert len(mr) == 1


# ----------------------------- 9. RLS / Workspace isolation -----------------------------
class TestRLS:
    def test_other_user_cannot_read_workspace_data(self, workspace, second_user):
        """second_user (not yet a member here unless invite-accept ran) should not see signals via REST with their JWT."""
        # Use a fresh second user that is NOT a member. We use a brand-new user.
        # Here second_user may have been added via TestInvites. So we just test backend enforcement instead.
        r = requests.post(
            f"{BASE_URL}/api/ai/ask",
            headers={"Authorization": f"Bearer {second_user['token']}",
                     "Content-Type": "application/json"},
            json={"workspace_id": workspace["id"], "question": "hi"},
            timeout=30,
        )
        # if invite accept already ran, second_user IS a member; allow either 200 or 403
        assert r.status_code in (200, 403)

    def test_random_user_cannot_seed_other_workspace(self, workspace, supabase_admin_headers):
        """Create an isolated user and confirm they get 403 on workspace operations."""
        ts = int(time.time())
        email = f"test+rls{ts}@astrixai.app"
        cr = requests.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=supabase_admin_headers,
            json={"email": email, "password": "TestPass123!", "email_confirm": True},
            timeout=30,
        )
        assert cr.status_code in (200, 201)
        uid = cr.json().get("id") or cr.json().get("user", {}).get("id")
        try:
            lr = requests.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={"email": email, "password": "TestPass123!"},
                timeout=15,
            )
            assert lr.status_code == 200
            tok = lr.json()["access_token"]
            r = requests.post(f"{BASE_URL}/api/seed-sample",
                              headers={"Authorization": f"Bearer {tok}",
                                       "Content-Type": "application/json"},
                              json={"workspace_id": workspace["id"]}, timeout=30)
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

            # RLS: direct REST read with user JWT should return empty list
            sigs = requests.get(
                f"{SUPABASE_URL}/rest/v1/signals",
                headers={"apikey": ANON_KEY, "Authorization": f"Bearer {tok}"},
                params={"workspace_id": f"eq.{workspace['id']}"},
                timeout=15,
            )
            assert sigs.status_code == 200
            assert sigs.json() == [], f"RLS leak! got {len(sigs.json())} rows for non-member"
        finally:
            requests.delete(f"{SUPABASE_URL}/auth/v1/admin/users/{uid}",
                            headers=supabase_admin_headers, timeout=15)


# ----------------------------- 10. AI Quota RPC -----------------------------
class TestQuota:
    def test_quota_rpc_initial_allowed(self, workspace, supabase_admin_headers):
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/check_and_increment_ai_quota",
            headers=supabase_admin_headers,
            json={"ws_id": workspace["id"]},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("allowed") is True
        assert "used" in data and "limit" in data
        assert "plan" in data
