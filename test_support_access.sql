-- Transaction-only fixtures. No real customer or consent persists, even on failure.
begin;
set local statement_timeout='20s';
insert into public.organizations(id,name,slug,status,plan) values
 (-91001,'ISOLATED Support Owner','isolated-support-owner-91001','active','internal'),
 (-91002,'ISOLATED Support Company','isolated-support-company-91002','active','pilot'),
 (-91003,'ISOLATED Other Company','isolated-support-other-91003','active','pilot');
insert into public.users(id,organization_id,email,display_name,role,password_hash,active,is_platform_owner) values
 (-91001,-91001,'isolated-support-owner-91001@test.invalid','ISOLATED Owner','admin','not-a-password-hash',true,true),
 (-91002,-91002,'isolated-support-admin-91002@test.invalid','ISOLATED Admin','admin','not-a-password-hash',true,false),
 (-91003,-91002,'isolated-support-planner-91003@test.invalid','ISOLATED Planner','planner','not-a-password-hash',true,false),
 (-91004,-91002,'isolated-support-tech-91004@test.invalid','ISOLATED Technician','technician','not-a-password-hash',true,false),
 (-91005,-91003,'isolated-support-otheradmin-91005@test.invalid','ISOLATED Other Admin','admin','not-a-password-hash',true,false),
 (-91006,-91001,'isolated-support-owner2-91006@test.invalid','ISOLATED Other Owner','admin','not-a-password-hash',true,true);
-- Non-hex marker cannot match any SHA-256 authentication token.
insert into public.sessions(token_hash,user_id,active_organization_id,expires_at,created_at)
 values('isolated-unusable-support-session',-91001,-91001,now()+interval '2 hours',now());
do $tests$
declare r jsonb; same jsonb; rid bigint; checked jsonb; events_before integer;
begin
  checked:=public.werkstuur_support_context(-91001,'isolated-unusable-support-session',-91002);
  assert (checked->>'http_status')::int=403, 'no consent must deny open';
  assert (public.werkstuur_support_access_check(-91001,-91002)->'grant')='null'::jsonb, 'no consent must deny reads';
  r:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED check only fictional software data',p_duration_minutes=>60,p_request_key=>'isolated-consent-request-key-1');
  assert r->>'error' is null, 'owner request must succeed';rid:=(r->'request'->>'id')::bigint;
  assert r->'request'->>'status'='pending', 'request must not approve itself';
  assert (public.werkstuur_support_access_check(-91001,-91002)->'grant')='null'::jsonb, 'pending must deny reads';
  same:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED check only fictional software data',p_duration_minutes=>60,p_request_key=>'isolated-consent-request-key-1');
  assert (same->>'created')::boolean=false and (same->'request'->>'id')::bigint=rid, 'retry must be idempotent';
  checked:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED different reason',p_duration_minutes=>60,p_request_key=>'isolated-consent-request-key-1');
  assert checked->>'code'='request_key_conflict', 'different retry must conflict';
  checked:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED duplicate pending',p_duration_minutes=>60,p_request_key=>'isolated-consent-request-key-2');
  assert checked->>'code'='request_already_open', 'duplicate pending must not create another grant';
  foreach rid in array array[rid] loop
    checked:=public.werkstuur_support_access_change(-91001,'approve',p_request_id=>rid,p_version=>1);
    assert (checked->>'http_status')::int=403, 'owner self-approval must fail';
    checked:=public.werkstuur_support_access_change(-91003,'approve',p_request_id=>rid,p_version=>1);
    assert (checked->>'http_status')::int=403, 'planner approval must fail';
    checked:=public.werkstuur_support_access_change(-91004,'approve',p_request_id=>rid,p_version=>1);
    assert (checked->>'http_status')::int=403, 'technician approval must fail';
    checked:=public.werkstuur_support_access_change(-91005,'approve',p_request_id=>rid,p_version=>1);
    assert (checked->>'http_status')::int=403, 'other company admin approval must fail';
    checked:=public.werkstuur_support_access_change(-91002,'approve',p_request_id=>rid,p_version=>1,p_duration_minutes=>120);
    assert (checked->>'http_status')::int=400, 'excess duration must fail';
    checked:=public.werkstuur_support_access_change(-91002,'approve',p_request_id=>rid,p_version=>1,p_duration_minutes=>30);
    assert checked->>'error' is null and checked->'request'->>'status'='approved', 'company admin approval must succeed';
    assert (checked->'request'->>'approved_minutes')::int=30, 'shorter approved duration must win';
    checked:=public.werkstuur_support_access_check(-91001,-91002);
    assert (checked->'grant'->>'id')::bigint=rid, 'approved subject must receive grant';
    assert (public.werkstuur_support_access_check(-91006,-91002)->'grant')='null'::jsonb, 'another owner must not reuse grant';
    assert (public.werkstuur_support_access_check(-91001,-91003)->'grant')='null'::jsonb, 'grant must not cross companies';
    checked:=public.werkstuur_support_access_change(-91002,'approve',p_request_id=>rid,p_version=>1,p_duration_minutes=>30);
    assert checked->>'code'='version_conflict', 'stale approval must not extend expiry';
    checked:=public.werkstuur_support_context(-91001,'isolated-unusable-support-session',-91002);
    assert (checked->>'ok')::boolean, 'approved subject must be able to open';
    select count(*) into events_before from public.support_access_events where request_id=rid and action='opened';
    assert events_before=1, 'opening must be logged';
    checked:=public.werkstuur_support_context(-91001,'isolated-unusable-support-session',-91002);
    assert (select count(*) from public.support_access_events where request_id=rid and action='opened')=events_before, 'same context must not duplicate log';
    update public.users set role='planner' where id=-91002;
    assert (public.werkstuur_support_access_check(-91001,-91002)->'grant')='null'::jsonb, 'approver role loss must close access';
    update public.users set role='admin' where id=-91002;
    update public.organizations set status='suspended' where id=-91002;
    assert (public.werkstuur_support_access_check(-91001,-91002)->'grant')='null'::jsonb, 'paused company must close access';
    update public.organizations set status='active' where id=-91002;
    checked:=public.werkstuur_support_access_change(-91005,'revoke',p_request_id=>rid,p_version=>2);
    assert (checked->>'http_status')::int=403, 'another company must not revoke';
    checked:=public.werkstuur_support_access_change(-91002,'revoke',p_request_id=>rid,p_version=>2);
    assert checked->'request'->>'status'='revoked', 'own admin must be able to revoke';
    assert (public.werkstuur_support_access_check(-91001,-91002)->'grant')='null'::jsonb, 'revocation must immediately close reads';
    checked:=public.werkstuur_support_context(-91001,'isolated-unusable-support-session',-91002);
    assert (checked->>'http_status')::int=403, 'revocation must close context opening';
    checked:=public.werkstuur_support_context(-91001,'isolated-unusable-support-session',-91001);
    assert (checked->>'ok')::boolean, 'home recovery must always work';
    assert (select count(*) from public.support_access_events where request_id=rid and action='left')=1, 'leaving must be logged';
  end loop;
  r:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED expiry and rejection test',p_duration_minutes=>15,p_request_key=>'isolated-consent-request-key-3');
  rid:=(r->'request'->>'id')::bigint;
  checked:=public.werkstuur_support_access_change(-91002,'approve',p_request_id=>rid,p_version=>1,p_duration_minutes=>30);
  assert (checked->>'http_status')::int=400, 'admin cannot extend requested duration';
  checked:=public.werkstuur_support_access_change(-91002,'reject',p_request_id=>rid,p_version=>1);
  assert checked->'request'->>'status'='rejected', 'admin can reject';
  r:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED approved expiry test',p_duration_minutes=>15,p_request_key=>'isolated-consent-request-key-4');rid:=(r->'request'->>'id')::bigint;
  checked:=public.werkstuur_support_access_change(-91002,'approve',p_request_id=>rid,p_version=>1,p_duration_minutes=>15);
  update public.support_access_requests set decided_at=now()-interval '16 minutes',expires_at=now()-interval '1 minute' where id=rid;
  assert (public.werkstuur_support_access_check(-91001,-91002)->'grant')='null'::jsonb, 'expiry must close reads';
  checked:=public.werkstuur_support_context(-91001,'isolated-unusable-support-session',-91002);
  assert (checked->>'http_status')::int=403, 'expiry must close opening';
  r:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,
    p_reason=>'ISOLATED pending expiry test',p_duration_minutes=>15,p_request_key=>'isolated-consent-request-key-5');rid:=(r->'request'->>'id')::bigint;
  update public.support_access_requests set created_at=now()-interval '25 hours',request_expires_at=now()-interval '1 hour' where id=rid;
  checked:=public.werkstuur_support_access_change(-91002,'approve',p_request_id=>rid,p_version=>1,p_duration_minutes=>15);
  assert checked->>'code'='request_closed', 'expired request cannot be approved';
  assert not has_table_privilege('anon','public.support_access_requests','select'), 'anonymous table read forbidden';
  assert not has_table_privilege('authenticated','public.support_access_requests','update'), 'browser updates forbidden';
  assert not has_function_privilege('anon','public.werkstuur_support_access_change(bigint,text,bigint,bigint,integer,text,integer,text)','execute'), 'anonymous RPC forbidden';
  assert not has_function_privilege('authenticated','public.werkstuur_support_context(bigint,text,bigint)','execute'), 'browser context RPC forbidden';
  assert not has_table_privilege('service_role','public.support_access_events','delete'), 'history must be append-only';
end;
$tests$;
rollback;
select 'support consent checks passed; all fixtures rolled back' as result;
