-- Server-authoritative, idempotent account provisioning and non-recycled founder ordinals.
create sequence if not exists public.asre_founding_user_ordinal_seq minvalue 1 no maxvalue no cycle;
revoke all on sequence public.asre_founding_user_ordinal_seq from public, anon, authenticated;

create table if not exists public.asre_accounts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text,
  founding_user_number bigint unique check (
    founding_user_number is null or founding_user_number between 1 and 1000
  ),
  usage_access text not null check (usage_access in ('unlimited','standard')),
  usage_access_period text not null check (usage_access_period in ('early_access','standard')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.asre_accounts enable row level security;
revoke all on public.asre_accounts from anon, authenticated;
grant select on public.asre_accounts to authenticated;
drop policy if exists asre_accounts_owner_select on public.asre_accounts;
create policy asre_accounts_owner_select on public.asre_accounts for select
using (user_id = auth.uid());

create or replace function public.provision_asre_account(
  requested_user_id uuid,
  requested_email text default null
) returns setof public.asre_accounts
language plpgsql security definer set search_path = public, pg_temp as $$
declare
  allocated bigint;
begin
  if requested_user_id is distinct from auth.uid()
     and coalesce(auth.role(), '') <> 'service_role' then
    raise exception 'account provisioning is owner scoped' using errcode = '42501';
  end if;

  perform pg_advisory_xact_lock(hashtext('asre-founding-user-allocation'));
  insert into public.profiles(id)
  values (requested_user_id)
  on conflict (id) do nothing;
  if not exists (select 1 from public.asre_accounts where user_id=requested_user_id) then
    allocated := nextval('public.asre_founding_user_ordinal_seq');
    if allocated > 1000 then allocated := null; end if;
    insert into public.asre_accounts(
      user_id,email,founding_user_number,usage_access,usage_access_period
    ) values (
      requested_user_id,requested_email,allocated,
      case when allocated is null then 'standard' else 'unlimited' end,
      case when allocated is null then 'standard' else 'early_access' end
    ) on conflict (user_id) do nothing;
  elsif requested_email is not null then
    update public.asre_accounts set email=requested_email,updated_at=now()
    where user_id=requested_user_id and email is distinct from requested_email;
  end if;
  return query select * from public.asre_accounts where user_id=requested_user_id;
end $$;

revoke all on function public.provision_asre_account(uuid,text) from public, anon;
grant execute on function public.provision_asre_account(uuid,text) to authenticated, service_role;
