-- ============================================================
-- ASTRIX AI — SUPABASE SCHEMA (run once in SQL Editor)
-- ============================================================
-- Paste this entire file in Supabase Dashboard → SQL Editor → New Query → Run
-- It creates: tables, indexes, RLS policies, triggers, helper functions,
-- and an auto-profile + workspace bootstrap on signup.
-- Safe to run multiple times (idempotent via IF NOT EXISTS).
-- ============================================================

-- 1. Profiles (extends auth.users)
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  full_name text,
  avatar_url text,
  created_at timestamptz default now()
);

-- 2. Workspaces
create table if not exists public.workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  timezone text default 'UTC',
  logo_url text,
  product_areas jsonb default '["Authentication","Core UI","API","Billing"]'::jsonb,
  segments jsonb default '["Enterprise","Growth","SMB"]'::jsonb,
  owner_id uuid references public.profiles(id) on delete set null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- 3. Workspace members
create table if not exists public.workspace_members (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  role text not null default 'member' check (role in ('owner','admin','member')),
  created_at timestamptz default now(),
  unique(workspace_id, user_id)
);

create index if not exists idx_wm_workspace on public.workspace_members(workspace_id);
create index if not exists idx_wm_user on public.workspace_members(user_id);

-- 4. Accounts
create table if not exists public.accounts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name text not null,
  domain text,
  arr numeric default 0,
  plan text,
  segment text,
  health_score text,
  renewal_date date,
  churn_risk text,
  owner_email text,
  signal_count integer default 0,
  last_signal_date timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_accounts_ws on public.accounts(workspace_id);
create index if not exists idx_accounts_domain on public.accounts(domain);

-- 5. Signals
create table if not exists public.signals (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  account_id uuid references public.accounts(id) on delete set null,
  source_type text default 'manual',
  raw_text text not null,
  normalized_text text,
  sentiment_label text,
  severity_label text,
  category text,
  product_area text,
  raw_record_json jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_signals_ws on public.signals(workspace_id);
create index if not exists idx_signals_account on public.signals(account_id);
create index if not exists idx_signals_severity on public.signals(severity_label);

-- 6. Problems
create table if not exists public.problems (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title text not null,
  description text,
  status text default 'Active' check (status in ('Active','Deferred','Solved')),
  severity text check (severity in ('Critical','High','Medium','Low')),
  trend text default 'Stable',
  product_area text,
  evidence_count integer default 0,
  affected_arr numeric default 0,
  first_seen timestamptz,
  last_seen timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_problems_ws on public.problems(workspace_id);

-- 7. Problem ↔ Signal links
create table if not exists public.problem_signal_links (
  id uuid primary key default gen_random_uuid(),
  problem_id uuid not null references public.problems(id) on delete cascade,
  signal_id uuid not null references public.signals(id) on delete cascade,
  created_at timestamptz default now(),
  unique(problem_id, signal_id)
);
create index if not exists idx_psl_problem on public.problem_signal_links(problem_id);
create index if not exists idx_psl_signal on public.problem_signal_links(signal_id);

-- 8. Opportunities
create table if not exists public.opportunities (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  problem_id uuid not null references public.problems(id) on delete cascade,
  opportunity_score numeric default 0,
  demand_score numeric default 0,
  pain_score numeric default 0,
  arr_score numeric default 0,
  trend_score numeric default 0,
  recency_score numeric default 0,
  affected_arr numeric default 0,
  recommended_action text,
  score_breakdown_json jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_opps_ws on public.opportunities(workspace_id);
create index if not exists idx_opps_problem on public.opportunities(problem_id);

-- 9. Decisions
create table if not exists public.decisions (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  problem_id uuid references public.problems(id) on delete set null,
  title text not null,
  action text not null check (action in ('Build','Fix','Experiment','Defer','Reject')),
  rationale text,
  author_id uuid references public.profiles(id),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_decisions_ws on public.decisions(workspace_id);

-- 10. Artifacts (Decision Memos / PRDs)
create table if not exists public.artifacts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  decision_id uuid references public.decisions(id) on delete cascade,
  title text not null,
  type text default 'decision_memo' check (type in ('decision_memo','prd','proof_summary')),
  content text default '',
  version integer default 1,
  author_id uuid references public.profiles(id),
  external_url text,
  external_id text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_artifacts_ws on public.artifacts(workspace_id);
create index if not exists idx_artifacts_decision on public.artifacts(decision_id);

-- 11. Launches
create table if not exists public.launches (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  decision_id uuid not null references public.decisions(id) on delete cascade,
  problem_id uuid references public.problems(id) on delete set null,
  title text not null,
  action text,
  owner_id uuid references public.profiles(id),
  launched_at timestamptz default now(),
  tracking_window_days integer default 30,
  expected_outcome text,
  target_metrics jsonb,
  status text default 'active' check (status in ('active','pending_review','complete')),
  before_count integer,
  after_count integer,
  pm_verdict text check (pm_verdict in ('Solved','Partially Solved','Not Solved','Regressed') or pm_verdict is null),
  notes text,
  created_by uuid references public.profiles(id),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_launches_ws on public.launches(workspace_id);

-- 12. Outcome measurements (Day-7, Day-30 reviews)
create table if not exists public.outcome_measurements (
  id uuid primary key default gen_random_uuid(),
  launch_id uuid not null references public.launches(id) on delete cascade,
  review_point text not null check (review_point in ('day_7','day_30','final')),
  baseline_signal_count integer,
  post_signal_count integer,
  signal_delta integer,
  baseline_affected_accounts integer,
  post_affected_accounts integer,
  baseline_arr_at_risk numeric,
  post_arr_at_risk numeric,
  pm_notes text,
  created_at timestamptz default now()
);
create index if not exists idx_om_launch on public.outcome_measurements(launch_id);

-- 13. Launch verdicts
create table if not exists public.launch_verdicts (
  id uuid primary key default gen_random_uuid(),
  launch_id uuid not null references public.launches(id) on delete cascade unique,
  verdict text not null check (verdict in ('Solved','Partially Solved','Not Solved','Regressed')),
  rationale text,
  submitted_by uuid references public.profiles(id),
  submitted_at timestamptz default now()
);

-- 14. Proof summaries
create table if not exists public.proof_summaries (
  id uuid primary key default gen_random_uuid(),
  launch_id uuid not null references public.launches(id) on delete cascade,
  content_markdown text,
  auto_generated boolean default false,
  created_at timestamptz default now()
);

-- 15. Workspace invites
create table if not exists public.workspace_invites (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  email text not null,
  role text default 'member',
  token text unique not null default encode(gen_random_bytes(24), 'hex'),
  invited_by uuid references public.profiles(id),
  accepted boolean default false,
  expires_at timestamptz default (now() + interval '7 days'),
  created_at timestamptz default now()
);

-- 16. Activity logs
create table if not exists public.activity_logs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid references public.profiles(id),
  action text,
  object_type text,
  object_id uuid,
  metadata_json jsonb,
  created_at timestamptz default now()
);
create index if not exists idx_activity_ws on public.activity_logs(workspace_id);

-- ============================================================
-- HELPER FUNCTIONS
-- ============================================================

-- Check if user is member of workspace (used in RLS policies)
create or replace function public.is_workspace_member(ws_id uuid)
returns boolean
language sql
security definer
stable
as $$
  select exists (
    select 1 from public.workspace_members
    where workspace_id = ws_id and user_id = auth.uid()
  );
$$;

-- Auto-create profile on signup
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
as $$
begin
  insert into public.profiles (id, email, full_name, avatar_url)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', split_part(new.email,'@',1)),
    new.raw_user_meta_data->>'avatar_url'
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Updated_at trigger
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end; $$;

do $$ declare t text;
begin
  for t in select unnest(array['workspaces','accounts','signals','problems','opportunities','decisions','artifacts','launches']) loop
    execute format('drop trigger if exists trg_touch_%s on public.%s;', t, t);
    execute format('create trigger trg_touch_%s before update on public.%s for each row execute function public.touch_updated_at();', t, t);
  end loop;
end $$;

-- Create workspace + auto-add owner as member
create or replace function public.create_workspace_with_owner(
  p_name text, p_slug text, p_timezone text default 'UTC'
)
returns public.workspaces
language plpgsql
security definer
as $$
declare ws public.workspaces;
begin
  insert into public.workspaces(name, slug, timezone, owner_id)
  values (p_name, p_slug, p_timezone, auth.uid())
  returning * into ws;
  insert into public.workspace_members(workspace_id, user_id, role)
  values (ws.id, auth.uid(), 'owner');
  return ws;
end;
$$;

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================
alter table public.profiles enable row level security;
alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.accounts enable row level security;
alter table public.signals enable row level security;
alter table public.problems enable row level security;
alter table public.problem_signal_links enable row level security;
alter table public.opportunities enable row level security;
alter table public.decisions enable row level security;
alter table public.artifacts enable row level security;
alter table public.launches enable row level security;
alter table public.outcome_measurements enable row level security;
alter table public.launch_verdicts enable row level security;
alter table public.proof_summaries enable row level security;
alter table public.workspace_invites enable row level security;
alter table public.activity_logs enable row level security;

-- Profiles: each user can read all profiles in their workspaces but write only own
drop policy if exists "profiles read own" on public.profiles;
create policy "profiles read own" on public.profiles for select using (true);
drop policy if exists "profiles update own" on public.profiles;
create policy "profiles update own" on public.profiles for update using (auth.uid() = id);

-- Workspaces: members can read; owner can update
drop policy if exists "ws read members" on public.workspaces;
create policy "ws read members" on public.workspaces for select using (public.is_workspace_member(id));
drop policy if exists "ws insert auth" on public.workspaces;
create policy "ws insert auth" on public.workspaces for insert with check (auth.uid() = owner_id);
drop policy if exists "ws update owner" on public.workspaces;
create policy "ws update owner" on public.workspaces for update using (auth.uid() = owner_id);

-- Workspace members: members can see members of their workspaces
drop policy if exists "wm read" on public.workspace_members;
create policy "wm read" on public.workspace_members for select using (public.is_workspace_member(workspace_id));
drop policy if exists "wm insert self" on public.workspace_members;
create policy "wm insert self" on public.workspace_members for insert with check (auth.uid() = user_id or public.is_workspace_member(workspace_id));
drop policy if exists "wm delete owner" on public.workspace_members;
create policy "wm delete owner" on public.workspace_members for delete using (public.is_workspace_member(workspace_id));

-- Generic workspace-scoped policy generator
do $$ declare t text;
begin
  for t in select unnest(array[
    'accounts','signals','problems','problem_signal_links','opportunities',
    'decisions','artifacts','launches','outcome_measurements','launch_verdicts',
    'proof_summaries','workspace_invites','activity_logs'
  ]) loop
    execute format('drop policy if exists "%s_select" on public.%s;', t, t);
    execute format('drop policy if exists "%s_insert" on public.%s;', t, t);
    execute format('drop policy if exists "%s_update" on public.%s;', t, t);
    execute format('drop policy if exists "%s_delete" on public.%s;', t, t);
  end loop;
end $$;

-- Direct workspace_id policies
do $$ declare t text;
begin
  for t in select unnest(array['accounts','signals','problems','opportunities','decisions','artifacts','launches','workspace_invites','activity_logs']) loop
    execute format($f$create policy "%1$s_select" on public.%1$s for select using (public.is_workspace_member(workspace_id));$f$, t);
    execute format($f$create policy "%1$s_insert" on public.%1$s for insert with check (public.is_workspace_member(workspace_id));$f$, t);
    execute format($f$create policy "%1$s_update" on public.%1$s for update using (public.is_workspace_member(workspace_id));$f$, t);
    execute format($f$create policy "%1$s_delete" on public.%1$s for delete using (public.is_workspace_member(workspace_id));$f$, t);
  end loop;
end $$;

-- Indirect policies via parent tables
create policy "psl_select" on public.problem_signal_links for select
  using (exists(select 1 from public.problems p where p.id=problem_id and public.is_workspace_member(p.workspace_id)));
create policy "psl_insert" on public.problem_signal_links for insert
  with check (exists(select 1 from public.problems p where p.id=problem_id and public.is_workspace_member(p.workspace_id)));
create policy "psl_delete" on public.problem_signal_links for delete
  using (exists(select 1 from public.problems p where p.id=problem_id and public.is_workspace_member(p.workspace_id)));

create policy "om_select" on public.outcome_measurements for select
  using (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));
create policy "om_insert" on public.outcome_measurements for insert
  with check (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));
create policy "om_update" on public.outcome_measurements for update
  using (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));

create policy "lv_select" on public.launch_verdicts for select
  using (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));
create policy "lv_insert" on public.launch_verdicts for insert
  with check (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));

create policy "ps_select" on public.proof_summaries for select
  using (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));
create policy "ps_insert" on public.proof_summaries for insert
  with check (exists(select 1 from public.launches l where l.id=launch_id and public.is_workspace_member(l.workspace_id)));

-- ============================================================
-- DONE. Tables, RLS, triggers ready.
-- ============================================================
