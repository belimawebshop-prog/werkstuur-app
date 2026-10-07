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
 (-91000,-91001,'isolated-support-owner2-91000@test.invalid','ISOLATED Other Owner','admin','not-a-password-hash',true,true);
-- Synthetic hash has no known token; all rows are rolled back.
insert into public.sessions(token_hash,user_id,active_organization_id,expires_at,created_at)
 values('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',-91001,-91001,now()+interval '2 hours',now());
insert into public.sessions(token_hash,user_id,active_organization_id,expires_at,created_at) values
 (repeat('b',64),-91001,-91001,now()+interval '2 hours',now()),
 (repeat('c',64),-91000,-91001,now()+interval '2 hours',now());
do $tests$
declare r jsonb; checked jsonb; same jsonb; rid bigint; i integer; latest integer; nonce text:=repeat('a',64); hash text:=repeat('b',64); events_before bigint;
begin
 checked:=public.werkstuur_support_context(-91001,repeat('a',64),-91002);
 assert checked->>'code'='support_access_required', 'unapproved context remains closed';
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'no grant before request';
 checked:=public.werkstuur_support_access_change(-91001,'request',p_organization_id=>-91002,p_reason=>'ISOLATED old owner request',p_duration_minutes=>15,p_request_key=>'isolated-old-protocol-123');
 assert checked->>'code'='customer_initiated_required', 'legacy initiation disabled';
 checked:=public.werkstuur_support_code_change(-91001,'request',p_reason=>'ISOLATED customer request',p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert (checked->>'http_status')::int=403, 'owner cannot initiate';
 checked:=public.werkstuur_support_code_change(-91003,'request',p_reason=>'ISOLATED planner request',p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert (checked->>'http_status')::int=403, 'planner cannot authorize company access';
 checked:=public.werkstuur_support_code_change(-91004,'request',p_reason=>'ISOLATED tech request',p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert (checked->>'http_status')::int=403, 'technician cannot authorize company access';
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED company request',p_request_key=>'isolated-code-test-001');
 assert (checked->>'http_status')::int=403, 'consent must be explicit';
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'short',p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert (checked->>'http_status')::int=400, 'reason must be meaningful';
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED company request',p_request_key=>'isolated-code-test-001',p_duration_minutes=>null,p_consent=>true);
 assert (checked->>'http_status')::int=400, 'null duration cannot bypass validation';
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED company request',p_request_key=>null,p_consent=>true);
 assert (checked->>'http_status')::int=400, 'null request key cannot bypass validation';
 update public.organizations set status='suspended' where id=-91002;
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED company request',p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert checked->>'code'='organization_unavailable', 'paused company cannot initiate';
 update public.organizations set status='active' where id=-91002;
 r:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED customer-approved issue',p_duration_minutes=>30,p_request_key=>'isolated-code-test-001',p_consent=>true);
 rid:=(r->'request'->>'id')::bigint;
 assert (r->>'created')::boolean, 'customer request created';
 assert (r->'request'->>'organization_id')::bigint=-91002, 'company derived from actor';
 assert (r->'request'->>'requester_id')::bigint=-91001, 'assigned to active owner';
 assert (r->'request'->>'initiated_by')::bigint=-91002, 'customer approver recorded';
 assert not (r->'request') ? 'request_key', 'idempotency key stays private';
 assert (select count(*) from public.support_access_events where request_id=rid and action='requested')=1, 'customer request audited';
 same:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED customer-approved issue',p_duration_minutes=>30,p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert not (same->>'created')::boolean, 'request retry is idempotent';
 assert same->'request'->>'id'=r->'request'->>'id', 'request retry keeps identity';
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED changed description',p_duration_minutes=>30,p_request_key=>'isolated-code-test-001',p_consent=>true);
 assert checked->>'code'='request_key_conflict', 'request key cannot be reused for other content';
 checked:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED second open issue',p_duration_minutes=>30,p_request_key=>'isolated-code-test-002',p_consent=>true);
 assert checked->>'code'='request_already_open', 'one open request per company';
 checked:=public.werkstuur_support_code_change(-91002,'accept',p_request_id=>rid,p_version=>1,p_code_nonce=>nonce,p_code_hash=>hash);
 assert (checked->>'http_status')::int=403, 'customer cannot self-accorder';
 checked:=public.werkstuur_support_code_change(-91000,'accept',p_request_id=>rid,p_version=>1,p_code_nonce=>nonce,p_code_hash=>hash);
 assert (checked->>'http_status')::int=404, 'other owner cannot accorder';
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>60,p_code_nonce=>nonce,p_code_hash=>hash);
 assert (checked->>'http_status')::int=400, 'owner cannot extend customer duration';
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>null,p_code_hash=>hash);
 assert (checked->>'http_status')::int=400, 'missing nonce rejected';
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>nonce,p_code_hash=>hash);
 assert checked->'request'->>'status'='accepted', 'owner acceptance creates code stage';
 assert not (checked->'request') ?| array['code_nonce','code_hash','activated_session_hash'], 'acceptance never returns verifiers';
 assert (select code_expires_at=accepted_at+interval '10 minutes' from public.support_access_requests where id=rid), 'code lifetime fixed to ten minutes';
 assert (select decided_at is null and expires_at is null from public.support_access_requests where id=rid), 'acceptance starts no lease';
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'accepted still cannot read';
 assert public.werkstuur_support_access_check(-91001,-91002)->'grant'='null'::jsonb, 'legacy check cannot grant';
 checked:=public.werkstuur_support_context(-91001,repeat('a',64),-91002);
 assert checked->>'code'='support_access_required', 'context cannot bypass code';
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>2,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>'no-session');
 assert (checked->>'http_status')::int=401, 'expired or absent session cannot activate';
 checked:=public.werkstuur_support_code_change(-91000,'activate',p_request_id=>rid,p_version=>2,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('c',64));
 assert (checked->>'http_status')::int=404, 'another owner cannot use the code';
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>1,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->>'code'='version_conflict', 'stale decision cannot activate';
 update public.users set role='planner' where id=-91002;
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>2,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->>'code'='organization_unavailable', 'approver role loss blocks code';
 update public.users set role='admin' where id=-91002;
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>2,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->'request'->>'status'='approved', 'valid code starts lease';
 assert (select active_organization_id=-91002 from public.sessions where token_hash=repeat('a',64)), 'activation switches context atomically';
 assert (select code_nonce is null and code_hash is null from public.support_access_requests where id=rid), 'code consumed at activation';
 assert (select expires_at=decided_at+interval '15 minutes' from public.support_access_requests where id=rid), 'lease starts at activation for accepted duration';
 assert not (checked->'request') ?| array['code_nonce','code_hash','activated_session_hash'], 'activation response contains no verifiers';
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'<>'null'::jsonb, 'correct session can read';
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('b',64))->'grant'='null'::jsonb, 'second browser cannot reuse grant';
 assert public.werkstuur_support_access_check(-91000,-91002,repeat('c',64))->'grant'='null'::jsonb, 'second owner cannot reuse grant';
 assert public.werkstuur_support_access_check(-91001,-91003,repeat('a',64))->'grant'='null'::jsonb, 'grant never crosses companies';
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>3,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->>'code'='request_closed', 'code replay blocked';
 select count(*) into events_before from public.support_access_events where request_id=rid and action='opened';
 checked:=public.werkstuur_support_context(-91001,repeat('a',64),-91002);
 assert (select count(*) from public.support_access_events where request_id=rid and action='opened')=events_before, 'opening same context does not duplicate events';
 assert not (public.werkstuur_export_organization(-91002,'ISOLATED test')->'support_access_requests'->0) ?| array['code_nonce','code_hash','activated_session_hash','request_key'], 'snapshot redacts all private protocol data';
 update public.users set role='planner' where id=-91002;
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'approver role loss closes reads';
 update public.users set role='admin' where id=-91002;
 update public.organizations set status='suspended' where id=-91002;
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'pause closes reads';
 update public.organizations set status='active' where id=-91002;
 checked:=public.werkstuur_support_code_change(-91005,'revoke',p_request_id=>rid,p_version=>3);
 assert (checked->>'http_status')::int=403, 'other company cannot revoke';
 checked:=public.werkstuur_support_code_change(-91002,'revoke',p_request_id=>rid,p_version=>3);
 assert checked->'request'->>'status'='revoked', 'company can revoke immediately';
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'revocation immediately closes reads';
 checked:=public.werkstuur_support_context(-91001,repeat('a',64),-91001);
 assert (checked->>'ok')::boolean, 'home recovery never requires customer code';
 -- Persistent wrong-code counter: all failures commit as normal RPC responses.
 r:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED code guessing test',p_duration_minutes=>15,p_request_key=>'isolated-code-test-003',p_consent=>true);rid:=(r->'request'->>'id')::bigint;
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>nonce,p_code_hash=>hash);
 for i in 1..5 loop
  checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>i+1,p_code_nonce=>nonce,p_code_hash=>repeat('c',64),p_session_hash=>repeat('a',64));
  assert (select code_attempts=i from public.support_access_requests where id=rid), 'each wrong code increments persisted counter';
  assert checked->>'code'=case when i=5 then 'code_locked' else 'code_invalid' end, 'fifth wrong code locks';
 end loop;
 assert (select status='locked' and code_nonce is null and code_hash is null from public.support_access_requests where id=rid), 'lock erases code material';
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>7,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->>'code'='request_closed', 'correct code cannot unlock exhausted request';
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'locked request gives no access';
 r:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED expired code test',p_duration_minutes=>15,p_request_key=>'isolated-code-test-004',p_consent=>true);rid:=(r->'request'->>'id')::bigint;
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>nonce,p_code_hash=>hash);
 update public.support_access_requests set accepted_at=now()-interval '11 minutes',code_expires_at=now()-interval '1 minute' where id=rid;
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>2,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->>'code'='request_closed', 'expired code cannot activate';
 r:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED customer cancellation',p_duration_minutes=>15,p_request_key=>'isolated-code-test-005',p_consent=>true);rid:=(r->'request'->>'id')::bigint;
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>nonce,p_code_hash=>hash);
 checked:=public.werkstuur_support_code_change(-91002,'cancel',p_request_id=>rid,p_version=>2);
 assert checked->'request'->>'status'='cancelled', 'company cancels before code';
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>3,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 assert checked->>'code'='request_closed', 'cancellation prevents activation';
 r:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED expired lease test',p_duration_minutes=>15,p_request_key=>'isolated-code-test-006',p_consent=>true);rid:=(r->'request'->>'id')::bigint;
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>nonce,p_code_hash=>hash);
 checked:=public.werkstuur_support_code_change(-91001,'activate',p_request_id=>rid,p_version=>2,p_code_nonce=>nonce,p_code_hash=>hash,p_session_hash=>repeat('a',64));
 update public.support_access_requests set decided_at=now()-interval '16 minutes',expires_at=now()-interval '1 minute' where id=rid;
 assert public.werkstuur_support_access_check(-91001,-91002,repeat('a',64))->'grant'='null'::jsonb, 'expired lease closes immediately';
 r:=public.werkstuur_support_code_change(-91002,'request',p_reason=>'ISOLATED expired request test',p_duration_minutes=>15,p_request_key=>'isolated-code-test-007',p_consent=>true);rid:=(r->'request'->>'id')::bigint;
 update public.support_access_requests set created_at=now()-interval '25 hours',request_expires_at=now()-interval '1 hour' where id=rid;
 checked:=public.werkstuur_support_code_change(-91001,'accept',p_request_id=>rid,p_version=>1,p_duration_minutes=>15,p_code_nonce=>nonce,p_code_hash=>hash);
 assert checked->>'code'='request_closed', 'expired pending request cannot accorder';
 assert not has_table_privilege('anon','public.support_access_requests','select'), 'anonymous cannot read codes';
 assert not has_table_privilege('authenticated','public.support_access_requests','select'), 'browser direct table reads denied';
 assert not has_table_privilege('service_role','public.support_access_events','delete'), 'history stays append-only';
 assert not has_function_privilege('anon','public.werkstuur_support_code_change(bigint,text,bigint,integer,text,integer,text,boolean,text,text,text)','execute'), 'anonymous cannot call protocol';
 assert not has_function_privilege('authenticated','public.werkstuur_support_access_check(bigint,bigint,text)','execute'), 'browser cannot grant itself reads';
 assert (select bool_and(not prosecdef) from pg_proc where proname in ('werkstuur_support_code_change','werkstuur_support_access_check','werkstuur_support_context')), 'protocol functions never bypass RLS';
end;
$tests$;
rollback;
select 'customer support code checks passed; all fixtures rolled back' as result;
