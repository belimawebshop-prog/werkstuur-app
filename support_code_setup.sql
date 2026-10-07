-- Apply after support_access_setup.sql. Legacy owner-initiated grants stay closed.
alter table public.support_access_requests
  add column flow text not null default 'legacy' check (flow in ('legacy','customer_code')),
  add column initiated_by bigint,
  add column accepted_at timestamptz,
  add column code_nonce text check (code_nonce ~ '^[0-9a-f]{64}$'),
  add column code_hash text check (code_hash ~ '^[0-9a-f]{64}$'),
  add column code_expires_at timestamptz,
  add column code_attempts integer not null default 0 check (code_attempts between 0 and 5),
  add column activated_session_hash text check (activated_session_hash ~ '^[0-9a-f]{64}$'),
  add foreign key (initiated_by,organization_id) references public.users(id,organization_id),
  add check (flow <> 'customer_code' or initiated_by is not null),
  add check (status <> 'accepted' or (accepted_at is not null and code_nonce is not null and code_hash is not null
    and code_expires_at is not null and code_expires_at=accepted_at+interval '10 minutes')),
  add check (flow <> 'customer_code' or status <> 'approved' or activated_session_hash is not null);
alter table public.support_access_requests drop constraint support_access_requests_status_check;
alter table public.support_access_requests add constraint support_access_requests_status_check
  check (status in ('pending','accepted','approved','rejected','revoked','cancelled','expired','locked'));
drop index public.support_access_one_open;
create unique index support_access_one_open on public.support_access_requests(organization_id,requester_id)
  where flow='customer_code' and status in ('pending','accepted','approved');
create index support_access_initiator on public.support_access_requests(initiated_by,organization_id);
alter table public.support_access_events drop constraint support_access_events_action_check;
alter table public.support_access_events add constraint support_access_events_action_check
  check (action in ('requested','accepted','activated','code_failed','locked','approved','rejected','revoked','cancelled','expired','opened','left'));

create function public.werkstuur_support_public(r public.support_access_requests)
returns jsonb language sql immutable security invoker set search_path='' as $$
  select to_jsonb(r)-array['request_key','code_nonce','code_hash','activated_session_hash'];
$$;
revoke all on function public.werkstuur_support_public(public.support_access_requests) from public,anon,authenticated;
grant execute on function public.werkstuur_support_public(public.support_access_requests) to service_role;

-- Close the old protocol so an older deployment cannot bypass the code step.
create or replace function public.werkstuur_support_access_check(p_actor_id bigint,p_organization_id bigint)
returns jsonb language sql stable security invoker set search_path='' as $$ select jsonb_build_object('grant',null); $$;
create or replace function public.werkstuur_support_access_change(
 p_actor_id bigint,p_action text,p_organization_id bigint default null,p_request_id bigint default null,
 p_version integer default null,p_reason text default '',p_duration_minutes integer default 60,p_request_key text default '')
returns jsonb language sql security invoker set search_path='' as $$
 select jsonb_build_object('error','Het klantbedrijf vraagt zelf support aan. De klantcode is verplicht.','http_status',403,'code','customer_initiated_required');
$$;

create function public.werkstuur_support_access_check(p_actor_id bigint,p_organization_id bigint,p_session_hash text)
returns jsonb language sql stable security invoker set search_path='' as $check$
 select jsonb_build_object('grant',(
  select jsonb_build_object('id',r.id,'organization_id',r.organization_id,'scope',r.scope,
   'expires_at',r.expires_at,'approved_minutes',r.approved_minutes,'approved_by',a.display_name)
  from public.support_access_requests r
  join public.users u on u.id=r.requester_id and u.active and u.is_platform_owner and u.organization_id<>r.organization_id
  join public.users a on a.id=r.initiated_by and a.id=r.decided_by and a.organization_id=r.organization_id
   and a.active and a.role='admin' and not a.is_platform_owner
  join public.organizations o on o.id=r.organization_id and o.status not in ('suspended','archived')
  join public.sessions s on s.token_hash=r.activated_session_hash and s.user_id=u.id and s.expires_at>now()
  where r.requester_id=p_actor_id and r.organization_id=p_organization_id and r.flow='customer_code'
   and r.status='approved' and r.expires_at>now() and r.activated_session_hash=p_session_hash
  order by r.id desc limit 1
 ));
$check$;
revoke all on function public.werkstuur_support_access_check(bigint,bigint,text) from public,anon,authenticated;
grant execute on function public.werkstuur_support_access_check(bigint,bigint,text) to service_role;

create function public.werkstuur_support_code_change(
 p_actor_id bigint,p_action text,p_request_id bigint default null,p_version integer default null,
 p_reason text default '',p_duration_minutes integer default 60,p_request_key text default '',p_consent boolean default false,
 p_code_nonce text default '',p_code_hash text default '',p_session_hash text default '')
returns jsonb language plpgsql security invoker set search_path='' as $change$
declare actor public.users%rowtype; owner public.users%rowtype; org public.organizations%rowtype;
 request public.support_access_requests%rowtype; old_request public.support_access_requests%rowtype;
 event_action text; prior_org bigint;
begin
 if p_action is null or p_action not in ('request','accept','reject','cancel','activate','revoke') then return jsonb_build_object('error','Ongeldig besluit.','http_status',400,'code','invalid_request'); end if;
 select * into actor from public.users where id=p_actor_id and active;
 if not found then return jsonb_build_object('error','Geen geldig account.','http_status',403,'code','forbidden'); end if;
 if p_action='request' then
  if actor.is_platform_owner or actor.role<>'admin' or p_consent is distinct from true then
   return jsonb_build_object('error','Alleen de eigen bedrijfsbeheerder kan inzage aanvragen en toestaan.','http_status',403,'code','forbidden');
  end if;
  select * into org from public.organizations where id=actor.organization_id and status not in ('suspended','archived');
  if not found then return jsonb_build_object('error','Deze organisatie is niet actief.','http_status',409,'code','organization_unavailable'); end if;
  select * into owner from public.users where active and is_platform_owner and organization_id<>org.id order by id limit 1;
  if not found then return jsonb_build_object('error','Werkstuur support is niet beschikbaar.','http_status',409,'code','support_unavailable'); end if;
  if p_reason is null or length(btrim(p_reason)) not between 10 and 1000 or p_duration_minutes is null or p_duration_minutes not in (15,30,60)
   or p_request_key is null or p_request_key !~ '^[A-Za-z0-9-]{16,80}$' then
   return jsonb_build_object('error','Ongeldig supportverzoek.','http_status',400,'code','invalid_request');
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('werkstuur-support-code:'||org.id,0));
  select * into request from public.support_access_requests where initiated_by=actor.id and request_key=p_request_key and flow='customer_code';
  if found then
   if request.reason<>btrim(p_reason) or request.requested_minutes<>p_duration_minutes then
    return jsonb_build_object('error','Deze aanvraagcode hoort bij een ander verzoek.','http_status',409,'code','request_key_conflict');
   end if;
   return jsonb_build_object('request',public.werkstuur_support_public(request),'created',false);
  end if;
  for old_request in select * from public.support_access_requests where organization_id=org.id and flow='customer_code'
   and status in ('pending','accepted','approved') for update
  loop
   if (old_request.status='pending' and old_request.request_expires_at>now())
    or (old_request.status='accepted' and old_request.code_expires_at>now())
    or (old_request.status='approved' and public.werkstuur_support_access_check(old_request.requester_id,org.id,old_request.activated_session_hash)->'grant'<>'null'::jsonb) then
    return jsonb_build_object('error','Er is al een open supportverzoek voor dit bedrijf.','http_status',409,'code','request_already_open');
   end if;
   update public.support_access_requests set status='expired',ended_at=now(),code_nonce=null,code_hash=null,version=version+1 where id=old_request.id;
   insert into public.support_access_events(request_id,organization_id,requester_id,actor_id,actor_name,action)
    values(old_request.id,org.id,old_request.requester_id,actor.id,actor.display_name,'expired');
  end loop;
  insert into public.support_access_requests(organization_id,requester_id,initiated_by,decided_by,flow,reason,requested_minutes,request_key)
   values(org.id,owner.id,actor.id,actor.id,'customer_code',btrim(p_reason),p_duration_minutes,p_request_key) returning * into request;
  event_action:='requested';
 else
  -- Same session-before-request lock order as context switching.
  if p_action='activate' then
   select active_organization_id into prior_org from public.sessions where token_hash=p_session_hash and user_id=actor.id and expires_at>now() for update;
   if not found then return jsonb_build_object('error','Je sessie is verlopen.','http_status',401,'code','unauthorized'); end if;
  end if;
  select * into request from public.support_access_requests where id=p_request_id and flow='customer_code' for update;
  if not found then return jsonb_build_object('error','Verzoek niet gevonden.','http_status',404,'code','not_found'); end if;
  if actor.is_platform_owner then
   if request.requester_id<>actor.id then return jsonb_build_object('error','Verzoek niet gevonden.','http_status',404,'code','not_found'); end if;
   if p_action not in ('accept','reject','cancel','activate') then return jsonb_build_object('error','Ongeldig besluit.','http_status',403,'code','forbidden'); end if;
  elsif actor.role<>'admin' or actor.organization_id<>request.organization_id or p_action not in ('cancel','revoke') then
   return jsonb_build_object('error','Alleen de eigen bedrijfsbeheerder kan dit verzoek intrekken.','http_status',403,'code','forbidden');
  end if;
  if p_version is null or p_version<>request.version then return jsonb_build_object('error','Dit verzoek is intussen gewijzigd. Vernieuw het overzicht.','http_status',409,'code','version_conflict'); end if;
  if p_action in ('accept','reject') and (request.status<>'pending' or request.request_expires_at<=now())
   or p_action='activate' and (request.status<>'accepted' or request.code_expires_at is null or request.code_expires_at<=now())
   or p_action='revoke' and request.status<>'approved'
   or p_action='cancel' and request.status not in ('pending','accepted','approved') then
   return jsonb_build_object('error','Dit verzoek of de code is gesloten of verlopen. Vraag opnieuw support aan.','http_status',409,'code','request_closed');
  end if;
  if p_action in ('accept','activate') then
   if not exists(select 1 from public.organizations where id=request.organization_id and status not in ('suspended','archived'))
    or not exists(select 1 from public.users where id=request.initiated_by and organization_id=request.organization_id and active and role='admin' and not is_platform_owner) then
    return jsonb_build_object('error','De organisatie of bedrijfsbeheerder is niet beschikbaar.','http_status',409,'code','organization_unavailable');
   end if;
  end if;
  if p_action='accept' then
   if p_duration_minutes is null or p_duration_minutes not in (15,30,60) or p_duration_minutes>request.requested_minutes
    or p_code_nonce is null or p_code_nonce !~ '^[0-9a-f]{64}$' or p_code_hash is null or p_code_hash !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('error','Ongeldige duur of beveiligde code.','http_status',400,'code','invalid_request');
   end if;
   update public.support_access_requests set status='accepted',approved_minutes=p_duration_minutes,accepted_at=now(),
    code_nonce=p_code_nonce,code_hash=p_code_hash,code_expires_at=now()+interval '10 minutes',code_attempts=0,version=version+1 where id=request.id returning * into request;
   event_action:='accepted';
  elsif p_action='activate' then
   if p_code_nonce is distinct from request.code_nonce or p_code_hash is distinct from request.code_hash or request.code_attempts>=5 then
    update public.support_access_requests set code_attempts=least(5,code_attempts+1),version=version+1,
     status=case when code_attempts+1>=5 then 'locked' else status end,
     code_nonce=case when code_attempts+1>=5 then null else code_nonce end,
     code_hash=case when code_attempts+1>=5 then null else code_hash end,
     ended_at=case when code_attempts+1>=5 then now() else ended_at end where id=request.id returning * into request;
    event_action:=case when request.status='locked' then 'locked' else 'code_failed' end;
    insert into public.support_access_events(request_id,organization_id,requester_id,actor_id,actor_name,action,detail)
     values(request.id,request.organization_id,request.requester_id,actor.id,actor.display_name,event_action,jsonb_build_object('attempts',request.code_attempts));
    return jsonb_build_object('error',case when request.status='locked' then 'Vijf verkeerde pogingen. De klant moet opnieuw support aanvragen.' else 'De klantcode klopt niet. Controleer de acht cijfers bij de klant.' end,
     'http_status',case when request.status='locked' then 429 else 400 end,'code',case when request.status='locked' then 'code_locked' else 'code_invalid' end);
   end if;
   update public.support_access_requests set status='approved',decided_at=now(),expires_at=now()+make_interval(mins=>approved_minutes),
    activated_session_hash=p_session_hash,code_nonce=null,code_hash=null,version=version+1 where id=request.id returning * into request;
   update public.sessions set active_organization_id=request.organization_id where token_hash=p_session_hash and user_id=actor.id;
   insert into public.support_access_events(request_id,organization_id,requester_id,actor_id,actor_name,action)
    values(request.id,request.organization_id,request.requester_id,actor.id,actor.display_name,'activated');
   event_action:='opened';
  elsif p_action='reject' then
   update public.support_access_requests set status='rejected',ended_by=actor.id,ended_at=now(),version=version+1 where id=request.id returning * into request;
   event_action:='rejected';
  else
   event_action:=case when p_action='revoke' then 'revoked' else 'cancelled' end;
   update public.support_access_requests set status=event_action,ended_by=actor.id,ended_at=now(),code_nonce=null,code_hash=null,version=version+1 where id=request.id returning * into request;
  end if;
 end if;
 insert into public.support_access_events(request_id,organization_id,requester_id,actor_id,actor_name,action,detail)
  values(request.id,request.organization_id,request.requester_id,actor.id,actor.display_name,event_action,
   jsonb_build_object('version',request.version,'scope',request.scope,'expires_at',request.expires_at));
 return jsonb_build_object('request',public.werkstuur_support_public(request),'created',p_action='request');
end;
$change$;
revoke all on function public.werkstuur_support_code_change(bigint,text,bigint,integer,text,integer,text,boolean,text,text,text) from public,anon,authenticated;
grant execute on function public.werkstuur_support_code_change(bigint,text,bigint,integer,text,integer,text,boolean,text,text,text) to service_role;

create or replace function public.werkstuur_support_context(p_actor_id bigint,p_session_hash text,p_organization_id bigint)
returns jsonb language plpgsql security invoker set search_path='' as $context$
declare actor public.users%rowtype; org public.organizations%rowtype; request public.support_access_requests%rowtype; previous_org bigint;
begin
 select * into actor from public.users where id=p_actor_id and active and is_platform_owner;
 if not found then return jsonb_build_object('error','Geen eigenaarstoegang.','http_status',403,'code','forbidden'); end if;
 select active_organization_id into previous_org from public.sessions where user_id=actor.id and token_hash=p_session_hash and expires_at>now() for update;
 if not found then return jsonb_build_object('error','Je sessie is verlopen.','http_status',401,'code','unauthorized'); end if;
 select * into org from public.organizations where id=p_organization_id;
 if not found then return jsonb_build_object('error','Organisatie niet gevonden.','http_status',404,'code','not_found'); end if;
 if org.id<>actor.organization_id then
  select * into request from public.support_access_requests where requester_id=actor.id and organization_id=org.id and flow='customer_code'
   and status='approved' and activated_session_hash=p_session_hash and expires_at>now() order by id desc limit 1 for share;
  if not found or public.werkstuur_support_access_check(actor.id,org.id,p_session_hash)->'grant'='null'::jsonb then
   return jsonb_build_object('error','Accordeer eerst het klantverzoek en voer de eenmalige klantcode in.','http_status',403,'code','support_access_required');
  end if;
  if previous_org is distinct from org.id then insert into public.support_access_events(request_id,organization_id,requester_id,actor_id,actor_name,action)
   values(request.id,org.id,actor.id,actor.id,actor.display_name,'opened'); end if;
 elsif previous_org is distinct from actor.organization_id then
  select * into request from public.support_access_requests where requester_id=actor.id and organization_id=previous_org order by id desc limit 1;
  if found then insert into public.support_access_events(request_id,organization_id,requester_id,actor_id,actor_name,action)
   values(request.id,previous_org,actor.id,actor.id,actor.display_name,'left'); end if;
 end if;
 update public.sessions set active_organization_id=org.id where user_id=actor.id and token_hash=p_session_hash;
 return jsonb_build_object('ok',true,'organization',to_jsonb(org));
end;
$context$;
-- Explicit privileges also protect against Supabase's older default grants.
revoke all on public.support_access_requests from public,anon,authenticated,service_role;
grant select,insert,update on public.support_access_requests to service_role;
revoke all on public.support_access_events from public,anon,authenticated,service_role;
grant select,insert on public.support_access_events to service_role;
revoke all on function public.werkstuur_support_context(bigint,text,bigint) from public,anon,authenticated;
grant execute on function public.werkstuur_support_context(bigint,text,bigint) to service_role;

create or replace function public.werkstuur_export_organization(p_organization_id bigint,p_app_version text)
returns jsonb language sql stable security invoker set search_path=public,pg_temp as $snapshot$
  select jsonb_build_object(
    'meta',jsonb_build_object('app_name','Werkstuur','app_version',p_app_version,'exported_at',now(),
      'contains_passwords',false,'contains_attachment_bytes',false,'organization_id',p_organization_id),
    'organization',(select to_jsonb(o) from public.organizations o where o.id=p_organization_id),
    'settings',coalesce((select jsonb_agg(jsonb_build_object('key',r.key,'value',r.value) order by r.key) from public.organization_settings r where r.organization_id=p_organization_id),'[]'::jsonb),
    'users',coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'email',r.email,'display_name',r.display_name,'role',r.role,'active',r.active,'created_at',r.created_at) order by r.id) from public.users r where r.organization_id=p_organization_id),'[]'::jsonb),
    'customers',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.customer_records r where r.organization_id=p_organization_id),'[]'::jsonb),
    'support_tickets',coalesce((select jsonb_agg(to_jsonb(r)-'request_key'-'fingerprint' order by r.id) from public.support_tickets r where r.organization_id=p_organization_id),'[]'::jsonb),
    'support_access_requests',coalesce((select jsonb_agg(to_jsonb(r)-array['request_key','code_nonce','code_hash','activated_session_hash'] order by r.id) from public.support_access_requests r where r.organization_id=p_organization_id),'[]'::jsonb),
    'support_access_events',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.support_access_events r where r.organization_id=p_organization_id),'[]'::jsonb),
    'pilots',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.pilots r where r.organization_id=p_organization_id),'[]'::jsonb),
    'pilot_snapshots',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.pilot_snapshots r where r.organization_id=p_organization_id),'[]'::jsonb),
    'cases',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.cases r where r.organization_id=p_organization_id),'[]'::jsonb),
    'notes',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.notes r where r.organization_id=p_organization_id),'[]'::jsonb),
    'attachments',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.attachments r where r.organization_id=p_organization_id),'[]'::jsonb),
    'audit',coalesce((select jsonb_agg(to_jsonb(r) order by r.id) from public.audit r where r.organization_id=p_organization_id),'[]'::jsonb)
  );
$snapshot$;
revoke all on function public.werkstuur_export_organization(bigint,text) from public,anon,authenticated;
grant execute on function public.werkstuur_export_organization(bigint,text) to service_role;
