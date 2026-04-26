"""ASTRIX AI Backend — production wiring.
- AI fallback chain (Gemini → OpenRouter → Grok) with per-workspace monthly quota
- Dodo Payments checkout + webhook
- Resend email for invites & launch reminders
- Sentry error tracking
- Supabase JWT auth verification
- Audit logging on critical mutations
"""
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from dotenv import load_dotenv
from pathlib import Path
import os, json, logging, httpx, hmac, hashlib, base64, time
from datetime import datetime, timezone, timedelta
import bleach
import jwt as pyjwt
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
import resend

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("astrix")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROK_API_KEY = os.environ.get("GROK_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM_EMAIL", "ASTRIX AI <onboarding@resend.dev>")
DODO_API_KEY = os.environ.get("DODO_API_KEY", "")
DODO_WEBHOOK_SECRET = os.environ.get("DODO_WEBHOOK_SECRET", "")
DODO_MODE = os.environ.get("DODO_MODE", "test")
DODO_API_BASE = os.environ.get("DODO_API_BASE", "https://test.dodopayments.com")
APP_URL = os.environ.get("APP_URL", "http://localhost:3000")
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")

if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, integrations=[FastApiIntegration()],
                    traces_sample_rate=0.1, environment=DODO_MODE)
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

app = FastAPI(title="ASTRIX AI Backend")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ----------------------------- Supabase REST helper -----------------------------
class SB:
    base = f"{SUPABASE_URL}/rest/v1"
    rpc_base = f"{SUPABASE_URL}/rest/v1/rpc"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    @classmethod
    async def select(cls, table, params=None):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{cls.base}/{table}", headers=cls.headers, params=params or {})
            if r.status_code >= 300: raise HTTPException(r.status_code, r.text)
            return r.json()

    @classmethod
    async def insert(cls, table, data):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{cls.base}/{table}", headers=cls.headers, json=data)
            if r.status_code >= 300: raise HTTPException(r.status_code, r.text)
            return r.json()

    @classmethod
    async def upsert(cls, table, data, on_conflict=None):
        h = {**cls.headers, "Prefer": "return=representation,resolution=merge-duplicates"}
        params = {"on_conflict": on_conflict} if on_conflict else None
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{cls.base}/{table}", headers=h, params=params, json=data)
            if r.status_code >= 300: raise HTTPException(r.status_code, r.text)
            return r.json()

    @classmethod
    async def update(cls, table, params, data):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.patch(f"{cls.base}/{table}", headers=cls.headers, params=params, json=data)
            if r.status_code >= 300: raise HTTPException(r.status_code, r.text)
            return r.json()

    @classmethod
    async def rpc(cls, fn, args=None):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{cls.rpc_base}/{fn}", headers=cls.headers, json=args or {})
            if r.status_code >= 300: raise HTTPException(r.status_code, r.text)
            return r.json()


# ----------------------------- Auth dependency -----------------------------
# Cache JWKS for JWT verification
_JWKS_CACHE: dict = {"keys": None, "fetched_at": 0}

def _get_jwks():
    if _JWKS_CACHE["keys"] and (time.time() - _JWKS_CACHE["fetched_at"]) < 3600:
        return _JWKS_CACHE["keys"]
    try:
        r = httpx.get(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json", timeout=10)
        r.raise_for_status()
        _JWKS_CACHE["keys"] = r.json().get("keys", [])
        _JWKS_CACHE["fetched_at"] = time.time()
        return _JWKS_CACHE["keys"]
    except Exception as e:
        logger.warning(f"JWKS fetch failed: {e}")
        return []


def get_user_from_token(authorization: Optional[str] = Header(None)) -> dict:
    """Verify Supabase JWT signature (ES256 via JWKS, fallback HS256) and return user info."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        header = pyjwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
        if alg in ("ES256", "RS256"):
            kid = header.get("kid")
            jwks = _get_jwks()
            key = next((k for k in jwks if k.get("kid") == kid), None) if kid else (jwks[0] if jwks else None)
            if not key:
                raise HTTPException(401, "no matching JWKS key")
            public_key = pyjwt.algorithms.ECAlgorithm.from_jwk(json.dumps(key)) if alg == "ES256" \
                else pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
            decoded = pyjwt.decode(token, public_key, algorithms=[alg], audience="authenticated")
        elif SUPABASE_JWT_SECRET:
            decoded = pyjwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
        else:
            decoded = pyjwt.decode(token, options={"verify_signature": False})
            if decoded.get("exp") and decoded["exp"] < time.time():
                raise HTTPException(401, "token expired")
        sub = decoded.get("sub")
        if not sub:
            raise HTTPException(401, "invalid token")
        return {"id": sub, "email": decoded.get("email"), "token": token}
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except HTTPException:
        raise
    except pyjwt.PyJWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        raise HTTPException(401, "invalid token")
    except Exception as e:
        logger.warning(f"JWT verify error: {e}")
        raise HTTPException(401, "invalid token")


async def assert_workspace_member(ws_id: str, user_id: str):
    rows = await SB.select("workspace_members",
        {"workspace_id": f"eq.{ws_id}", "user_id": f"eq.{user_id}", "limit": "1"})
    if not rows:
        raise HTTPException(403, "not a workspace member")
    return rows[0]  # contains role


# ----------------------------- Validation helpers -----------------------------
def sanitize_text(s: Optional[str], max_len: int = 10000) -> Optional[str]:
    if s is None: return None
    s = bleach.clean(s, tags=[], strip=True)
    return s[:max_len]


# ----------------------------- AI provider chain -----------------------------
async def call_gemini(prompt: str, json_mode=False) -> str:
    if not GEMINI_API_KEY: raise RuntimeError("no gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    body = {"contents":[{"parts":[{"text":prompt}]}],
            "generationConfig":{"temperature":0.4,"maxOutputTokens":2048}}
    if json_mode: body["generationConfig"]["responseMimeType"]="application/json"
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(url, json=body)
        if r.status_code >= 300: raise RuntimeError(f"gemini {r.status_code}: {r.text[:200]}")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]


async def call_openrouter(prompt, json_mode=False, model="google/gemini-2.0-flash-exp:free"):
    if not OPENROUTER_API_KEY: raise RuntimeError("no openrouter")
    body = {"model": model, "messages":[{"role":"user","content":prompt}], "temperature":0.4}
    if json_mode: body["response_format"] = {"type":"json_object"}
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENROUTER_API_KEY}","Content-Type":"application/json"}, json=body)
        if r.status_code >= 300: raise RuntimeError(f"openrouter {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]["content"]


async def call_grok(prompt, json_mode=False):
    if not GROK_API_KEY: raise RuntimeError("no grok")
    body = {"model":"llama-3.3-70b-versatile", "messages":[{"role":"user","content":prompt}], "temperature":0.4}
    if json_mode: body["response_format"] = {"type":"json_object"}
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {GROK_API_KEY}","Content-Type":"application/json"}, json=body)
        if r.status_code >= 300: raise RuntimeError(f"grok {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]["content"]


async def ai_generate(prompt, json_mode=False, prefer_paid=False):
    chain = [("gemini", call_gemini), ("openrouter", call_openrouter), ("grok", call_grok)]
    if prefer_paid: chain = [("grok", call_grok)] + chain
    last = None
    for name, fn in chain:
        try:
            return await fn(prompt, json_mode=json_mode), name
        except Exception as e:
            logger.warning(f"AI provider {name} failed: {e}"); last = e
    raise HTTPException(503, f"All AI providers failed: {last}")


def _strip_json_fences(s):
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n",1)[1] if "\n" in s else s
        if s.endswith("```"): s = s.rsplit("```",1)[0]
        if s.startswith("json"): s = s[4:].lstrip()
    return s.strip()


async def enforce_ai_quota(ws_id: str) -> dict:
    res = await SB.rpc("check_and_increment_ai_quota", {"ws_id": ws_id})
    if not res.get("allowed"):
        raise HTTPException(429, f"AI quota exhausted for plan {res.get('plan')}: {res.get('used')}/{res.get('limit')}. Upgrade your plan.")
    return res


# ----------------------------- Audit log helper -----------------------------
async def audit_log(ws_id: str, user_id: Optional[str], action: str, obj_type: str, obj_id: Optional[str], meta: dict = None):
    try:
        await SB.insert("activity_logs", {
            "workspace_id": ws_id, "user_id": user_id, "action": action,
            "object_type": obj_type, "object_id": obj_id, "metadata_json": meta or {},
        })
    except Exception as e:
        logger.warning(f"audit_log failed: {e}")


# ----------------------------- Email helpers -----------------------------
async def send_email(to: str, subject: str, html: str):
    if not RESEND_API_KEY:
        logger.warning("Resend not configured"); return None
    try:
        return resend.Emails.send({
            "from": RESEND_FROM, "to": [to], "subject": subject, "html": html,
        })
    except Exception as e:
        logger.error(f"resend send failed: {e}")
        return None


# ----------------------------- Health -----------------------------
@app.get("/api/health")
async def health():
    return {"status":"ok","ts":datetime.now(timezone.utc).isoformat(),"mode":DODO_MODE}


# ----------------------------- AI: classify signal -----------------------------
class ClassifyReq(BaseModel):
    raw_text: str = Field(..., max_length=10000)
    workspace_id: str
    product_areas: List[str] = Field(default_factory=list)


@app.post("/api/ai/classify-signal")
async def classify_signal(req: ClassifyReq, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(req.workspace_id, user["id"])
    await enforce_ai_quota(req.workspace_id)
    text = sanitize_text(req.raw_text, 10000)
    areas_str = ", ".join(req.product_areas) if req.product_areas else "Authentication, Core UI, API, Billing, Performance, Other"
    prompt = f"""You analyze customer feedback for a B2B SaaS company and return STRICT JSON only.
Given the signal text below, return:
{{"title": <8-word concise title>,
 "severity_label": "Critical"|"High"|"Medium"|"Low",
 "sentiment_label": "Positive"|"Neutral"|"Negative",
 "category": "Bug"|"Feature Request"|"Pricing"|"Performance"|"Onboarding"|"Other",
 "product_area": one of [{areas_str}],
 "normalized_text": <one-sentence summary>}}

Signal: ```{text}```
Return ONLY the JSON object."""
    out, provider = await ai_generate(prompt, json_mode=True)
    try: data = json.loads(_strip_json_fences(out))
    except Exception:
        data = {"title": text[:80], "severity_label":"Medium","sentiment_label":"Neutral",
                "category":"Other","product_area":"Other","normalized_text": text[:200]}
    return {"data": data, "provider": provider}


# ----------------------------- AI: memo & proof -----------------------------
class MemoReq(BaseModel):
    workspace_id: str
    problem: Dict[str, Any]
    opportunity: Optional[Dict[str, Any]] = None
    decision: Dict[str, Any]
    artifact_type: str = "decision_memo"


@app.post("/api/ai/generate-memo")
async def generate_memo(req: MemoReq, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(req.workspace_id, user["id"])
    await enforce_ai_quota(req.workspace_id)
    if req.artifact_type == "prd":
        prompt = f"""Write a concise Product Requirements Document in markdown.
Sections: Problem Statement, Goals, Non-goals, Scope, User Stories, Success Metrics, Risks, Open Questions.
PROBLEM: {json.dumps(req.problem)[:3000]}
OPPORTUNITY: {json.dumps(req.opportunity or {})[:1500]}
DECISION: {json.dumps(req.decision)[:1500]}
Return only the markdown PRD."""
    else:
        prompt = f"""Write a Decision Memo in markdown.
Sections: TL;DR, Context (the problem), Evidence (signals + ARR), Options Considered, Decision, Rationale, Risks, Next Steps.
PROBLEM: {json.dumps(req.problem)[:3000]}
OPPORTUNITY: {json.dumps(req.opportunity or {})[:1500]}
DECISION: {json.dumps(req.decision)[:1500]}
Return only the markdown memo."""
    text, provider = await ai_generate(prompt)
    return {"content": text.strip(), "provider": provider}


class ProofReq(BaseModel):
    workspace_id: str
    launch: Dict[str, Any]
    measurements: List[Dict[str, Any]] = Field(default_factory=list)
    verdict: Optional[str] = None


@app.post("/api/ai/proof-summary")
async def proof_summary(req: ProofReq, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(req.workspace_id, user["id"])
    await enforce_ai_quota(req.workspace_id)
    prompt = f"""Write a short markdown Proof Summary card for a product launch.
Sections: Launch, Expected Outcome, Before vs After, Verdict, Key Learnings.
LAUNCH: {json.dumps(req.launch)[:2000]}
MEASUREMENTS: {json.dumps(req.measurements)[:2000]}
VERDICT: {req.verdict or "N/A"}
Return only the markdown."""
    text, provider = await ai_generate(prompt)
    return {"content": text.strip(), "provider": provider}


class AskReq(BaseModel):
    workspace_id: str
    question: str = Field(..., max_length=1000)


@app.post("/api/ai/ask")
async def ai_ask(req: AskReq, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(req.workspace_id, user["id"])
    await enforce_ai_quota(req.workspace_id)
    ws = req.workspace_id
    opps = await SB.select("opportunities", {"workspace_id":f"eq.{ws}","order":"opportunity_score.desc","limit":"5"})
    decisions = await SB.select("decisions", {"workspace_id":f"eq.{ws}","order":"created_at.desc","limit":"5"})
    launches = await SB.select("launches", {"workspace_id":f"eq.{ws}","order":"created_at.desc","limit":"5"})
    problems = await SB.select("problems", {"workspace_id":f"eq.{ws}","order":"created_at.desc","limit":"5"})
    context = {"top_opportunities":opps,"recent_decisions":decisions,"recent_launches":launches,"top_problems":problems}
    prompt = f"""You are ASTRIX AI assistant. Answer using ONLY the workspace context.
If the answer is not in the context, say "I don't have enough workspace data to answer that."
CONTEXT (JSON):
{json.dumps(context)[:6000]}
QUESTION: {sanitize_text(req.question, 1000)}
Respond in concise markdown."""
    text, provider = await ai_generate(prompt)
    return {"answer": text.strip(), "provider": provider}


# ----------------------------- Opportunity scoring -----------------------------
class ScoreReq(BaseModel):
    workspace_id: str
    problem_id: str


def compute_opportunity(problem, signals, accounts):
    sig_count = len(signals)
    affected_account_ids = {s.get("account_id") for s in signals if s.get("account_id")}
    affected_accounts = [a for a in accounts if a["id"] in affected_account_ids]
    affected_arr = sum(float(a.get("arr") or 0) for a in affected_accounts)
    demand = min(30, (sig_count / 50.0) * 30)
    pain = min(20, (len(affected_accounts) / 10.0) * 20)
    arr = min(30, (affected_arr / 5_000_000.0) * 30)
    sev_map = {"Critical":10,"High":7,"Medium":4,"Low":2}
    severity = sev_map.get(problem.get("severity"), 3)
    recency = 5
    if problem.get("last_seen_at"):
        try:
            ls = datetime.fromisoformat(problem["last_seen_at"].replace("Z","+00:00"))
            days = (datetime.now(timezone.utc) - ls).days
            recency = 10 if days <= 7 else 6 if days <= 30 else 3 if days <= 90 else 1
        except: pass
    total = round(demand + pain + arr + severity + recency, 1)
    action = "Build" if total >= 70 else "Fix" if total >= 50 else "Experiment" if total >= 30 else "Defer"
    return {
        "opportunity_score": total, "demand_score": round(demand,1), "pain_score": round(pain,1),
        "arr_score": round(arr,1), "trend_score": float(severity), "recency_score": float(recency),
        "affected_arr": affected_arr, "recommended_action": action,
        "score_breakdown_json": {"signal_count":sig_count,"affected_account_count":len(affected_accounts),
                                  "affected_arr":affected_arr,"severity":problem.get("severity")},
    }


@app.post("/api/score-opportunity")
async def score_opportunity(req: ScoreReq, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(req.workspace_id, user["id"])
    return await _score_internal(req.workspace_id, req.problem_id)


async def _score_internal(ws: str, pid: str):
    problems = await SB.select("problems", {"id":f"eq.{pid}","limit":"1"})
    if not problems: raise HTTPException(404, "problem not found")
    problem = problems[0]
    links = await SB.select("problem_signal_links", {"problem_id":f"eq.{pid}","select":"signal_id"})
    sig_ids = [l["signal_id"] for l in links]
    signals = await SB.select("signals", {"id":f"in.({','.join(sig_ids)})"}) if sig_ids else []
    accounts = await SB.select("accounts", {"workspace_id":f"eq.{ws}"})
    score = compute_opportunity(problem, signals, accounts)
    existing = await SB.select("opportunities", {"problem_id":f"eq.{pid}","limit":"1"})
    if existing:
        await SB.update("opportunities", {"id":f"eq.{existing[0]['id']}"}, score)
        opp_id = existing[0]["id"]
    else:
        res = await SB.insert("opportunities", {**score, "workspace_id":ws, "problem_id":pid})
        opp_id = res[0]["id"] if res else None
    await SB.update("problems", {"id":f"eq.{pid}"}, {
        "evidence_count": len(signals),
        "affected_arr": score["affected_arr"],
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"opportunity_id": opp_id, **score}


@app.post("/api/score-workspace/{ws_id}")
async def score_workspace(ws_id: str, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(ws_id, user["id"])
    problems = await SB.select("problems", {"workspace_id":f"eq.{ws_id}"})
    out = []
    for p in problems:
        try:
            r = await _score_internal(ws_id, p["id"])
            out.append({"problem_id": p["id"], "score": r["opportunity_score"]})
        except Exception as e:
            out.append({"problem_id": p["id"], "error": str(e)})
    return {"results": out}


# ----------------------------- Sample seeder -----------------------------
class SeedReq(BaseModel):
    workspace_id: str


@app.post("/api/seed-sample")
async def seed_sample(req: SeedReq, user: dict = Depends(get_user_from_token)):
    ws = req.workspace_id
    await assert_workspace_member(ws, user["id"])
    accs = await SB.insert("accounts", [
        {"workspace_id":ws,"name":"CloudScale Inc","domain":"cloudscale.com","arr":1200000,"plan":"Enterprise","health_score":"84"},
        {"workspace_id":ws,"name":"TechFlow","domain":"techflow.io","arr":840000,"plan":"Enterprise","health_score":"42"},
        {"workspace_id":ws,"name":"DataSync","domain":"datasync.co","arr":45000,"plan":"Standard","health_score":"91"},
        {"workspace_id":ws,"name":"Loomis AI","domain":"loomis.ai","arr":250000,"plan":"Growth","health_score":"65"},
    ])
    a_map = {a["name"]: a["id"] for a in accs}
    sigs_data = [
        ("CloudScale Inc","We cannot renew our contract next quarter unless SAML SSO is implemented. IT mandates Okta.","Critical","Negative","Feature Request","Authentication","Slack"),
        ("CloudScale Inc","Need SCIM provisioning to auto-deprovision users when they leave.","High","Negative","Feature Request","Authentication","Email"),
        ("TechFlow","Data sync keeps failing with 429 Too Many Requests. We need higher API limits.","High","Negative","Bug","API","Intercom"),
        ("TechFlow","API rate limits are blocking our nightly ETL job. Please raise the cap.","High","Negative","Feature Request","API","Support"),
        ("DataSync","Any updates on dark mode? My eyes are burning.","Low","Neutral","Feature Request","Core UI","Discord"),
        ("Loomis AI","Okta SSO is a must-have — our security team is blocking adoption without it.","Critical","Negative","Feature Request","Authentication","Sales Call"),
    ]
    sigs_payload = [{"workspace_id":ws,"account_id":a_map.get(t[0]),"raw_text":t[1],"normalized_text":t[1][:200],
                     "severity_label":t[2],"sentiment_label":t[3],"category":t[4],"product_area":t[5],"source_type":t[6]}
                    for t in sigs_data]
    inserted_sigs = await SB.insert("signals", sigs_payload)

    probs = await SB.insert("problems", [
        {"workspace_id":ws,"title":"SAML SSO Integration Missing",
         "description":"Enterprise customers blocked from deploying widely without Okta/Azure AD SAML support.",
         "severity":"Critical","trend":"Rising","product_area":"Authentication","status":"Active"},
        {"workspace_id":ws,"title":"API Rate Limits Too Strict",
         "description":"Power users hitting the 100 req/min limit during peak hours, causing sync failures.",
         "severity":"High","trend":"Stable","product_area":"API","status":"Active"},
    ])
    p1, p2 = probs[0]["id"], probs[1]["id"]
    auth_sig_ids = [s["id"] for s in inserted_sigs if s["product_area"]=="Authentication"]
    api_sig_ids = [s["id"] for s in inserted_sigs if s["product_area"]=="API"]
    if auth_sig_ids: await SB.insert("problem_signal_links", [{"problem_id":p1,"signal_id":sid,"workspace_id":ws} for sid in auth_sig_ids])
    if api_sig_ids: await SB.insert("problem_signal_links", [{"problem_id":p2,"signal_id":sid,"workspace_id":ws} for sid in api_sig_ids])
    await _score_internal(ws, p1)
    await _score_internal(ws, p2)
    await audit_log(ws, user["id"], "seed_sample", "workspace", ws, {})
    return {"accounts": len(accs), "signals": len(inserted_sigs), "problems": 2}


# ----------------------------- DODO PAYMENTS -----------------------------
class CheckoutReq(BaseModel):
    workspace_id: str
    plan: Literal['starter','growth','scale']
    billing_period: Literal['monthly','annual']


@app.post("/api/billing/checkout")
async def billing_checkout(req: CheckoutReq, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(req.workspace_id, user["id"])
    if not DODO_API_KEY: raise HTTPException(503, "billing not configured")

    plan_key = f"{req.plan}_{'monthly' if req.billing_period=='monthly' else 'annual'}"
    rows = await SB.select("pricing_config", {"plan":f"eq.{plan_key}","limit":"1"})
    if not rows: raise HTTPException(400, f"unknown plan {plan_key}")
    product_id = rows[0]["stripe_price_id"]
    if not product_id: raise HTTPException(400, "plan has no Dodo product id")

    # Resolve customer email (Supabase auth admin)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{SUPABASE_URL}/auth/v1/admin/users/{user['id']}",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        email = r.json().get("email") if r.status_code < 300 else user.get("email")

    payload = {
        "billing": {"city":"NA","country":"US","state":"NA","street":"NA","zipcode":"00000"},
        "customer": {"email": email or "user@astrixai.app", "name": email.split("@")[0] if email else "ASTRIX User"},
        "product_id": product_id,
        "quantity": 1,
        "payment_link": True,
        "return_url": f"{APP_URL}/app/settings?billing=success",
        "metadata": {
            "workspace_id": req.workspace_id,
            "user_id": user["id"],
            "plan": req.plan,
            "billing_period": req.billing_period,
        },
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{DODO_API_BASE}/subscriptions",
            headers={"Authorization": f"Bearer {DODO_API_KEY}", "Content-Type":"application/json"},
            json=payload)
        if r.status_code >= 300:
            logger.error(f"Dodo error {r.status_code}: {r.text}")
            raise HTTPException(r.status_code, f"Dodo: {r.text[:300]}")
        data = r.json()
    await audit_log(req.workspace_id, user["id"], "billing_checkout_created", "subscription", None, {"plan":req.plan, "period":req.billing_period})
    return {"checkout_url": data.get("payment_link") or data.get("url"), "subscription_id": data.get("subscription_id")}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request,
    webhook_id: Optional[str] = Header(None, alias="webhook-id"),
    webhook_signature: Optional[str] = Header(None, alias="webhook-signature"),
    webhook_timestamp: Optional[str] = Header(None, alias="webhook-timestamp")):
    body_bytes = await request.body()
    body = body_bytes.decode("utf-8")

    # When secret is configured, ALWAYS require valid signature
    if DODO_WEBHOOK_SECRET:
        if not (webhook_id and webhook_signature and webhook_timestamp):
            raise HTTPException(401, "missing webhook signature headers")
        try:
            from standardwebhooks.webhooks import Webhook
            wh = Webhook(DODO_WEBHOOK_SECRET)
            wh.verify(body, {"webhook-id": webhook_id, "webhook-signature": webhook_signature, "webhook-timestamp": webhook_timestamp})
        except Exception as e:
            logger.error(f"webhook verify failed: {e}")
            raise HTTPException(401, "invalid signature")

    event = json.loads(body)
    etype = event.get("type") or event.get("event_type")
    data = event.get("data") or {}
    meta = (data.get("metadata") or {})
    ws_id = meta.get("workspace_id")
    plan = meta.get("plan")
    period = meta.get("billing_period")
    sub_id = data.get("subscription_id") or data.get("id")

    plan_key = f"{plan}_{period}" if plan and period else None
    if etype in ("subscription.active", "subscription.created", "subscription.renewed", "payment.succeeded") and ws_id and plan_key:
        # Mark subscription active
        period_end = data.get("next_billing_date") or (datetime.now(timezone.utc) + timedelta(days=30 if period=="monthly" else 365)).isoformat()
        existing = await SB.select("subscriptions", {"workspace_id":f"eq.{ws_id}","limit":"1"})
        sub_row = {
            "workspace_id": ws_id, "plan": plan_key, "status": "active",
            "payment_provider": "dodo", "payment_provider_subscription_id": sub_id,
            "current_period_start": datetime.now(timezone.utc).isoformat(),
            "current_period_end": period_end,
            "canceled_at": None,
        }
        if existing:
            await SB.update("subscriptions", {"id":f"eq.{existing[0]['id']}"}, sub_row)
        else:
            await SB.insert("subscriptions", sub_row)
        await audit_log(ws_id, meta.get("user_id"), "subscription_activated", "subscription", sub_id, {"plan":plan_key})
    elif etype in ("subscription.cancelled", "subscription.canceled", "subscription.expired") and ws_id:
        existing = await SB.select("subscriptions", {"workspace_id":f"eq.{ws_id}","limit":"1"})
        if existing:
            await SB.update("subscriptions", {"id":f"eq.{existing[0]['id']}"},
                {"status":"canceled","canceled_at": datetime.now(timezone.utc).isoformat()})
        await audit_log(ws_id, meta.get("user_id"), "subscription_canceled", "subscription", sub_id, {})
    elif etype in ("payment.failed", "subscription.failed") and ws_id:
        existing = await SB.select("subscriptions", {"workspace_id":f"eq.{ws_id}","limit":"1"})
        if existing:
            await SB.update("subscriptions", {"id":f"eq.{existing[0]['id']}"}, {"status":"past_due"})
        await audit_log(ws_id, meta.get("user_id"), "payment_failed", "subscription", sub_id, {})

    return {"received": True, "type": etype}


@app.get("/api/billing/status/{ws_id}")
async def billing_status(ws_id: str, user: dict = Depends(get_user_from_token)):
    await assert_workspace_member(ws_id, user["id"])
    subs = await SB.select("subscriptions", {"workspace_id":f"eq.{ws_id}","limit":"1","order":"created_at.desc"})
    plan_resp = await SB.rpc("get_workspace_plan", {"ws_id": ws_id})
    return {"subscription": subs[0] if subs else None, "current_plan": plan_resp}


@app.get("/api/billing/pricing")
async def billing_pricing():
    rows = await SB.select("pricing_config", {"active":"eq.true","order":"amount_minor.asc"})
    return {"plans": rows}


# ----------------------------- INVITES + EMAILS -----------------------------
class InviteReq(BaseModel):
    workspace_id: str
    email: str = Field(..., min_length=3, max_length=255)
    role: Literal['member','admin','viewer'] = "member"


@app.post("/api/team/invite")
async def team_invite(req: InviteReq, user: dict = Depends(get_user_from_token)):
    member = await assert_workspace_member(req.workspace_id, user["id"])
    if member.get("role") not in ("owner","admin"):
        raise HTTPException(403, "only admins can invite")
    inv = await SB.insert("invitations", [{
        "workspace_id": req.workspace_id, "invited_email": req.email.lower(),
        "invited_by": user["id"], "role": req.role, "status":"pending",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }])
    if not inv: raise HTTPException(500, "invite create failed")
    token = inv[0]["token"]
    accept_url = f"{APP_URL}/accept-invitation?token={token}"
    ws_rows = await SB.select("workspaces", {"id":f"eq.{req.workspace_id}","limit":"1"})
    ws_name = ws_rows[0]["name"] if ws_rows else "your workspace"
    html = f"""
    <h2>You've been invited to {ws_name} on ASTRIX AI</h2>
    <p>Click below to accept (expires in 7 days):</p>
    <p><a href="{accept_url}" style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;font-weight:bold">Accept invitation</a></p>
    <p style="color:#666;font-size:12px">Or paste this link: {accept_url}</p>
    """
    await send_email(req.email, f"You're invited to {ws_name} on ASTRIX AI", html)
    await audit_log(req.workspace_id, user["id"], "invite_sent", "invitation", inv[0]["id"], {"email":req.email})
    return {"id": inv[0]["id"], "accept_url": accept_url}


class AcceptReq(BaseModel):
    token: str


@app.post("/api/team/accept-invite")
async def accept_invite(req: AcceptReq, user: dict = Depends(get_user_from_token)):
    invs = await SB.select("invitations", {"token":f"eq.{req.token}","status":"eq.pending","limit":"1"})
    if not invs: raise HTTPException(404, "invitation not found or expired")
    inv = invs[0]
    if datetime.fromisoformat(inv["expires_at"].replace("Z","+00:00")) < datetime.now(timezone.utc):
        raise HTTPException(410, "expired")
    await SB.upsert("workspace_members",
        [{"workspace_id":inv["workspace_id"],"user_id":user["id"],"role":inv["role"]}],
        on_conflict="workspace_id,user_id")
    await SB.update("invitations", {"id":f"eq.{inv['id']}"},
        {"status":"accepted","accepted_at": datetime.now(timezone.utc).isoformat()})
    return {"workspace_id": inv["workspace_id"]}


# ----------------------------- LAUNCH REMINDERS (scheduled) -----------------------------
@app.post("/api/cron/send-launch-reminders")
async def cron_launch_reminders(secret: Optional[str] = Header(None, alias="x-cron-secret")):
    """Called by external cron. Sends Day-7/Day-30 review reminders."""
    if secret != DODO_WEBHOOK_SECRET:
        raise HTTPException(401)
    now = datetime.now(timezone.utc)
    # Find launches needing reminders
    launches = await SB.select("launches", {"status":"eq.active"})
    sent = 0
    for l in launches:
        if not l.get("launched_at"): continue
        ld = datetime.fromisoformat(l["launched_at"].replace("Z","+00:00"))
        delta = (now - ld).days
        for due_days, kind in [(7,"day_7"),(30,"day_30")]:
            if delta == due_days:
                # Check if already sent
                existing = await SB.select("reminder_jobs", {"launch_id":f"eq.{l['id']}","reminder_type":f"eq.{kind}","limit":"1"})
                if existing and existing[0].get("status") == "sent": continue
                # Get owner email
                owner = l.get("owner_id") or l.get("created_by")
                if not owner: continue
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(f"{SUPABASE_URL}/auth/v1/admin/users/{owner}",
                        headers={"apikey": SUPABASE_SERVICE_KEY,"Authorization":f"Bearer {SUPABASE_SERVICE_KEY}"})
                    email = r.json().get("email") if r.status_code < 300 else None
                if email:
                    review_url = f"{APP_URL}/app/launches/{l['id']}"
                    html = f"<h2>Day {due_days} review due</h2><p>It's been {due_days} days since '{l.get('title')}' launched. Review it: <a href='{review_url}'>{review_url}</a></p>"
                    await send_email(email, f"Day {due_days} review: {l.get('title')}", html)
                await SB.upsert("reminder_jobs",
                    [{"workspace_id":l["workspace_id"],"launch_id":l["id"],"reminder_type":kind,
                      "due_at": now.isoformat(),"status":"sent","sent_at": now.isoformat()}],
                    on_conflict="launch_id,reminder_type")
                sent += 1
    return {"sent": sent}
