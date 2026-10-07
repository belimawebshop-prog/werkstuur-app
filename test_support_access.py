"""Real HTTP privacy boundaries with isolated accounts; SQL is tested separately.

No real company, credentials, consent, support message or email is created.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import support_access
import test_refinement_workflow as fixture


class SupportAccessUnitTests(unittest.TestCase):
    def test_expiration_is_utc_and_fail_closed(self):
        now=datetime.now(timezone.utc)
        for value in (None,"wrong",now.replace(tzinfo=None).isoformat(),(now-timedelta(seconds=1)).isoformat(),now.isoformat()):
            self.assertEqual(support_access.effective_status({"status":"approved","expires_at":value},now),"expired")
        self.assertEqual(support_access.effective_status({"status":"approved","expires_at":(now+timedelta(seconds=1)).isoformat()},now),"approved")
        self.assertEqual(support_access.effective_status({"status":"revoked","expires_at":(now+timedelta(hours=1)).isoformat()},now),"revoked")

    def test_pending_requests_also_expire(self):
        self.assertEqual(support_access.effective_status({"status":"pending","request_expires_at":"2000-01-01T00:00:00Z"}),"expired")

    def test_invalid_rpc_response_does_not_grant_access(self):
        for data in (None,[],True,"approved"):
            db=SimpleNamespace(rpc=lambda *args:SimpleNamespace(execute=lambda:SimpleNamespace(data=data)))
            with self.assertRaises(RuntimeError):support_access.AccessManager(db).grant(1,2)

    def test_identity_fields_and_excess_duration_cannot_be_injected(self):
        manager=support_access.AccessManager(None)
        body={"organization_id":2,"reason":"Fictief softwareprobleem","duration_minutes":60,"request_key":"isolated-request-123456"}
        for changed in ({**body,"actor_id":2},{**body,"duration_minutes":True},{**body,"duration_minutes":120},{**body,"reason":"kort"},{**body,"organization_id":True},{**body,"request_key":"x"}):
            with self.subTest(changed=changed),self.assertRaises(support_access.AccessError):manager.request({"id":1},changed)

    def test_owner_cannot_approve_through_owner_decision_route(self):
        with self.assertRaises(support_access.AccessError) as error:
            support_access.AccessManager(None).decide({"id":1},1,{"action":"approve","version":1,"duration_minutes":60},owner=True)
        self.assertEqual(error.exception.status,403)


class SupportAccessHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.RefinementWorkflowTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.RefinementWorkflowTests.tearDownClass.__func__)
    setUp=fixture.RefinementWorkflowTests.setUp
    request=fixture.RefinementWorkflowTests.request
    client=fixture.RefinementWorkflowTests.client

    def open_support(self):
        owner=self.client(1);row=self.db.approve_support_fixture()
        self.assertEqual(self.request(owner,"/api/owner/context",{"organization_id":2})[0],200)
        return owner,row

    def test_open_without_permission_is_denied_and_keeps_home_context(self):
        owner=self.client(1)
        status,body,_=self.request(owner,"/api/owner/context",{"organization_id":2})
        self.assertEqual((status,body["code"]),(403,"support_access_required"))
        self.assertEqual(self.request(owner,"/api/me")[1]["organization_id"],1)
        self.assertEqual(self.mail,[])

    def test_foreign_operational_counts_are_hidden_without_permission(self):
        status,orgs,_=self.request(self.client(1),"/api/owner/organizations")
        self.assertEqual(status,200)
        foreign=next(o for o in orgs if o["id"]==2)
        self.assertFalse(foreign["can_view"]);self.assertTrue(foreign["can_request_access"])
        for key in ("cases","active_users","last_activity","onboarding_complete","active_pilot"):self.assertIsNone(foreign[key])

    def test_missing_company_admin_cannot_receive_request(self):
        _,orgs,_=self.request(self.client(1),"/api/owner/organizations")
        self.assertFalse(next(o for o in orgs if o["id"]==3)["can_request_access"])

    def test_only_real_company_admin_sees_company_permission_requests(self):
        self.db.approve_support_fixture()
        status,payload,_=self.request(self.client(2),"/api/support-access")
        self.assertEqual(status,200);self.assertEqual(len(payload["requests"]),1)
        self.assertNotIn("request_key",payload["requests"][0])
        for uid in (None,1,3,4,6):
            with self.subTest(uid=uid):self.assertIn(self.request(self.client(uid),"/api/support-access")[0],(401,403))

    def test_planner_technician_and_owner_cannot_approve_company_request(self):
        body={"action":"approve","version":1,"duration_minutes":60}
        for uid in (None,1,3,4,6):
            with self.subTest(uid=uid):self.assertIn(self.request(self.client(uid),"/api/support-access/1",body)[0],(401,403))
        self.assertEqual(self.request(self.client(1),"/api/owner/support-access/1",body)[0],403)

    def test_granted_read_access_redacts_intake_tokens_and_blocks_exports(self):
        owner,_=self.open_support()
        for path in ("/api/cases","/api/customers","/api/users","/api/accounts","/api/audit"):
            with self.subTest(path=path):self.assertEqual(self.request(owner,path)[0],200)
        for path in ("/api/settings","/api/onboarding-status"):
            self.assertIsNone(self.request(owner,path)[1]["intake_token"])
        for path in ("/api/export","/api/export?format=zip","/api/backups","/api/backups/1/download"):
            with self.subTest(path=path):self.assertEqual(self.request(owner,path)[0],403)
        self.assertEqual(self.mail,[])

    def test_granted_viewer_cannot_write_or_change_a_password(self):
        owner,_=self.open_support()
        for path,method in (("/api/cases","POST"),("/api/settings","PATCH"),("/api/accounts","POST"),("/api/customers","POST"),("/api/pilot-reset","POST"),("/api/change-password","POST")):
            with self.subTest(path=path):self.assertEqual(self.request(owner,path,{},method)[0],403)
        self.assertEqual(self.mail,[])

    def test_revocation_blocks_every_private_path_and_me_recovers_home(self):
        owner,row=self.open_support();row["status"]="revoked"
        for path in ("/api/cases","/api/customers","/api/users","/api/settings","/api/attachments/1","/api/cases/1/notes","/api/cases/1/attachments","/api/audit","/api/metrics","/api/pilot"):
            with self.subTest(path=path):self.assertEqual(self.request(owner,path)[0],403)
        status,me,_=self.request(owner,"/api/me")
        self.assertEqual(status,200);self.assertEqual(me["organization_id"],1);self.assertEqual(me["support_access_ended"],2)
        self.assertEqual(self.request(owner,"/api/cases",headers={"X-Werkstuur-Organization":"2"})[0],409)

    def test_expiry_is_checked_again_without_logging_out(self):
        owner,row=self.open_support();row["expires_at"]=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.assertEqual(self.request(owner,"/api/cases")[0],403)
        self.assertEqual(self.request(owner,"/api/owner/context",{"organization_id":1})[0],200)

    def test_losing_approver_role_or_company_active_status_ends_access(self):
        owner,row=self.open_support()
        self.db.rows["users"][1]["role"]="planner"
        self.assertEqual(self.request(owner,"/api/cases")[0],403)
        self.db.rows["users"][1]["role"]="admin";self.db.rows["organizations"][1]["status"]="suspended"
        self.assertEqual(self.request(owner,"/api/cases")[0],403)

    def test_permission_cannot_be_used_by_another_owner(self):
        self.db.approve_support_fixture()
        self.db.rows["users"].append({**self.db.rows["users"][0],"id":8,"email":"actor8@test.invalid"})
        self.assertEqual(self.request(self.client(8),"/api/owner/context",{"organization_id":2})[0],403)

    def test_attachment_read_rechecks_permission_after_storage_download(self):
        owner,row=self.open_support()
        self.db.rows["cases"]=[{"id":91,"organization_id":2,"type":"Laadpaal","problem":"Test"}]
        self.db.rows["attachments"]=[{"id":1,"case_id":91,"organization_id":2,"storage_path":"test.bin","filename":"test.bin"}]
        def download(key):row["status"]="revoked";return b"private synthetic bytes"
        self.db.download=download
        status,result,_=self.request(owner,"/api/attachments/1")
        self.assertEqual(status,403);self.assertNotIn("private synthetic",str(result))

    def test_rpc_failure_denies_access_instead_of_falling_back(self):
        owner,_=self.open_support()
        self.db.rpc=lambda *args,**kwargs:SimpleNamespace(execute=lambda:SimpleNamespace(data=None))
        self.assertEqual(self.request(owner,"/api/cases")[0],500)

    def test_explicit_home_context_needs_no_customer_approval(self):
        self.assertEqual(self.request(self.client(1),"/api/owner/context",{"organization_id":1})[0],200)


if __name__=="__main__":unittest.main()
