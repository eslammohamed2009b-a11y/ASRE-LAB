-- Retire the legacy founder programme while preserving the account RPC signature.
-- Historical columns and sequence remain inert for compatibility with applied migration 013.
update public.asre_accounts
set founding_user_number = null,
    usage_access = 'standard',
    usage_access_period = 'standard'
where founding_user_number is not null
   or usage_access is distinct from 'standard'
   or usage_access_period is distinct from 'standard';

create or replace function public.provision_asre_account(
  requested_user_id uuid,
  requested_email text default null
) returns setof public.asre_accounts
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if requested_user_id is distinct from auth.uid()
     and coalesce(auth.role(), '') <> 'service_role' then
    raise exception 'account provisioning is owner scoped' using errcode = '42501';
  end if;

  insert into public.profiles(id)
  values (requested_user_id)
  on conflict (id) do nothing;

  insert into public.asre_accounts(
    user_id, email, founding_user_number, usage_access, usage_access_period
  ) values (
    requested_user_id, requested_email, null, 'standard', 'standard'
  ) on conflict (user_id) do nothing;

  update public.asre_accounts
  set email = requested_email,
      founding_user_number = null,
      usage_access = 'standard',
      usage_access_period = 'standard',
      updated_at = now()
  where user_id = requested_user_id
    and (
      (requested_email is not null and email is distinct from requested_email)
      or founding_user_number is not null
      or usage_access is distinct from 'standard'
      or usage_access_period is distinct from 'standard'
    );

  return query select * from public.asre_accounts where user_id = requested_user_id;
end $$;

revoke all on public.asre_accounts from anon, authenticated;
grant select on public.asre_accounts to authenticated;
drop policy if exists asre_accounts_owner_select on public.asre_accounts;
create policy asre_accounts_owner_select on public.asre_accounts for select
using (user_id = auth.uid());

revoke all on function public.provision_asre_account(uuid,text) from public, anon;
grant execute on function public.provision_asre_account(uuid,text) to authenticated, service_role;
