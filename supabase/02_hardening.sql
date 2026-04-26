-- ============================================================
-- ASTRIX AI — HARDENING MIGRATION (run after schema.sql)
-- Seeds pricing_config with Dodo product IDs, plan_limits,
-- adds AI quota helper RPC, audit triggers, role enforcement.
-- Idempotent / safe to re-run.
-- ============================================================

-- 1. Seed plan_limits (Free / Starter / Growth / Scale)
insert into public.plan_limits(plan, max_members, max_signals_per_month, max_ai_calls_per_month, max_uploads_per_month, max_workspaces, ai_model_tier, features)
values
  ('free',     3,    500,   50,    5,  1, 'gemini',     '{"verdicts":true,"assistant":false,"digests":false}'::jsonb),
  ('starter',  10,   5000,  500,   25, 1, 'gemini',     '{"verdicts":true,"assistant":true,"digests":true}'::jsonb),
  ('growth',   25,   25000, 2500,  100,3, 'gemini-pro', '{"verdicts":true,"assistant":true,"digests":true,"compare_mode":true}'::jsonb),
  ('scale',    100,  200000,20000, 500,10,'grok',       '{"verdicts":true,"assistant":true,"digests":true,"compare_mode":true,"audit_logs":true,"sso":true}'::jsonb)
on conflict (plan) do update set
  max_members=excluded.max_members,
  max_signals_per_month=excluded.max_signals_per_month,
  max_ai_calls_per_month=excluded.max_ai_calls_per_month,
  max_uploads_per_month=excluded.max_uploads_per_month,
  max_workspaces=excluded.max_workspaces,
  ai_model_tier=excluded.ai_model_tier,
  features=excluded.features;

-- 2. Seed pricing_config with Dodo product IDs (stored in stripe_price_id column for compatibility)
-- First fix the unique constraint to be (plan, billing_period) instead of just (plan)
alter table public.pricing_config drop constraint if exists pricing_config_plan_key;
alter table public.pricing_config add constraint pricing_config_plan_period_key unique (plan, billing_period);
delete from public.pricing_config where plan in ('starter','growth','scale');
insert into public.pricing_config(plan, billing_period, stripe_price_id, currency, amount_minor, display_name, description, active) values
  ('starter','monthly','pdt_0NbFfMOQIsJF9X0LrtkH9','usd',5900,  'Starter (Monthly)','For small product teams getting started', true),
  ('starter','annual', 'pdt_0NbC7p1x3vArb3CYIqAT6','usd',58800, 'Starter (Annual)','Save 17% vs monthly', true),
  ('growth', 'monthly','pdt_0NbC3RvgjyFoZ6wJ7LLqP','usd',17900, 'Growth (Monthly)','For scaling product orgs', true),
  ('growth', 'annual', 'pdt_0NbC5NQsoxq2leqmoeDmB','usd',178900,'Growth (Annual)','Save 17% vs monthly', true),
  ('scale',  'monthly','pdt_0NcBuLfSXkF4hSJFs0AbV','usd',44900, 'Scale (Monthly)','For enterprise product teams', true),
  ('scale',  'annual', 'pdt_0NbFhELrTC3P4kNf8On24','usd',448900,'Scale (Annual)','Save 17% vs monthly', true);

-- 3. Bridge function: get current plan for a workspace (with founder_override + free fallback)
create or replace function public.get_workspace_plan(ws_id uuid)
returns text
language sql
stable
security definer
as $$
  select coalesce(
    (select case when founder_override or status in ('active','trialing') then plan else 'free' end
       from public.subscriptions where workspace_id = ws_id
       order by created_at desc limit 1),
    'free'
  );
$$;

-- 4. AI quota check & increment (atomic)
create or replace function public.check_and_increment_ai_quota(ws_id uuid)
returns jsonb
language plpgsql
security definer
as $$
declare
  plan_name text;
  monthly_limit int;
  used int;
  pstart date := date_trunc('month', current_date)::date;
  pend   date := (date_trunc('month', current_date) + interval '1 month - 1 day')::date;
begin
  plan_name := public.get_workspace_plan(ws_id);
  select max_ai_calls_per_month into monthly_limit from public.plan_limits where plan = plan_name;
  monthly_limit := coalesce(monthly_limit, 50);

  insert into public.ai_provider_usage(workspace_id, provider, period_start, period_end, calls_used, calls_limit)
  values (ws_id, 'gemini', pstart, pend, 0, monthly_limit)
  on conflict do nothing;

  update public.ai_provider_usage
     set calls_used = calls_used + 1, updated_at = now()
   where workspace_id = ws_id and provider = 'gemini' and period_start = pstart
   returning calls_used into used;

  if used > monthly_limit then
    update public.ai_provider_usage set calls_used = calls_used - 1
     where workspace_id = ws_id and provider = 'gemini' and period_start = pstart;
    return jsonb_build_object('allowed', false, 'used', used-1, 'limit', monthly_limit, 'plan', plan_name);
  end if;

  return jsonb_build_object('allowed', true, 'used', used, 'limit', monthly_limit, 'plan', plan_name);
end;
$$;

grant execute on function public.check_and_increment_ai_quota(uuid) to anon, authenticated, service_role;
grant execute on function public.get_workspace_plan(uuid) to anon, authenticated, service_role;
grant execute on function public.create_workspace_with_owner(text, text, text) to authenticated;

-- 5. Indexes for ai_provider_usage uniqueness
create unique index if not exists uq_ai_usage_ws_provider_period
  on public.ai_provider_usage(workspace_id, provider, period_start);

-- 6. Audit log helper
create or replace function public.log_activity(
  p_ws uuid, p_action text, p_obj_type text, p_obj_id uuid, p_meta jsonb default '{}'::jsonb
) returns void language plpgsql security definer as $$
begin
  insert into public.activity_logs(workspace_id, user_id, action, object_type, object_id, metadata_json)
  values (p_ws, auth.uid(), p_action, p_obj_type, p_obj_id, p_meta);
end; $$;

grant execute on function public.log_activity(uuid, text, text, uuid, jsonb) to authenticated, service_role;

-- 7. Role enforcement helper
create or replace function public.is_workspace_admin(ws_id uuid) returns boolean
language sql security definer stable as $$
  select exists (select 1 from public.workspace_members
                 where workspace_id = ws_id and user_id = auth.uid() and role in ('owner','admin'));
$$;
grant execute on function public.is_workspace_admin(uuid) to anon, authenticated;

-- 8. Tighten workspace update policy: only owners/admins
drop policy if exists "ws update owner" on public.workspaces;
create policy "ws update owner_admin" on public.workspaces for update
  using (public.is_workspace_admin(id));

-- 9. Subscriptions RLS (read-only for members; writes only via service role)
alter table public.subscriptions enable row level security;
drop policy if exists "subs_select" on public.subscriptions;
create policy "subs_select" on public.subscriptions for select using (public.is_workspace_member(workspace_id));

alter table public.ai_provider_usage enable row level security;
drop policy if exists "ai_usage_select" on public.ai_provider_usage;
create policy "ai_usage_select" on public.ai_provider_usage for select using (public.is_workspace_member(workspace_id));

alter table public.plan_limits enable row level security;
drop policy if exists "plan_limits_select" on public.plan_limits;
create policy "plan_limits_select" on public.plan_limits for select using (true);

alter table public.pricing_config enable row level security;
drop policy if exists "pricing_select" on public.pricing_config;
create policy "pricing_select" on public.pricing_config for select using (active = true);

alter table public.notifications enable row level security;
drop policy if exists "notif_select" on public.notifications;
create policy "notif_select" on public.notifications for select using (auth.uid() = user_id);
drop policy if exists "notif_update" on public.notifications;
create policy "notif_update" on public.notifications for update using (auth.uid() = user_id);

alter table public.usage_tracking enable row level security;
drop policy if exists "usage_select" on public.usage_tracking;
create policy "usage_select" on public.usage_tracking for select using (public.is_workspace_member(workspace_id));

alter table public.invitations enable row level security;
drop policy if exists "inv_select" on public.invitations;
create policy "inv_select" on public.invitations for select using (public.is_workspace_member(workspace_id));
drop policy if exists "inv_insert" on public.invitations;
create policy "inv_insert" on public.invitations for insert with check (public.is_workspace_admin(workspace_id));
drop policy if exists "inv_update" on public.invitations;
create policy "inv_update" on public.invitations for update using (public.is_workspace_admin(workspace_id));

alter table public.launch_reviews enable row level security;
drop policy if exists "lr_select" on public.launch_reviews;
create policy "lr_select" on public.launch_reviews for select using (public.is_workspace_member(workspace_id));
drop policy if exists "lr_insert" on public.launch_reviews;
create policy "lr_insert" on public.launch_reviews for insert with check (public.is_workspace_member(workspace_id));
drop policy if exists "lr_update" on public.launch_reviews;
create policy "lr_update" on public.launch_reviews for update using (public.is_workspace_member(workspace_id));

alter table public.reminder_jobs enable row level security;
drop policy if exists "rj_select" on public.reminder_jobs;
create policy "rj_select" on public.reminder_jobs for select using (public.is_workspace_member(workspace_id));

-- 10. Auto-create free subscription on workspace insert
create or replace function public.ensure_free_subscription() returns trigger
language plpgsql security definer as $$
begin
  insert into public.subscriptions(workspace_id, plan, status, current_period_start, current_period_end, payment_provider)
  values (new.id, 'free', 'active', now(), now() + interval '1 month', 'none')
  on conflict do nothing;
  return new;
end; $$;

drop trigger if exists trg_workspace_free_sub on public.workspaces;
create trigger trg_workspace_free_sub after insert on public.workspaces
  for each row execute function public.ensure_free_subscription();

-- ============================================================
-- DONE
-- ============================================================
