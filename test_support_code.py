"""Real HTTP code flow with synthetic accounts; actual RPC tested in Postgres."""
from datetime import datetime, timedelta, timezone
import unittest
import support_access
import test_refinement_workflow as fixture

class SupportCodeUnitTests(unittest.TestCase):
    def test_secret_required_and_code_is_eight_digits(self):
        nonce='ab'*32
        with self.assertRaises(RuntimeError):support_access.AccessManager(None).code_for(nonce)
        a=support_access.AccessManager(None,b'a'*32);b=support_access.AccessManager(None,b'b'*32)
        self.assertRegex(a.code_for(nonce),r'^[0-9]{8}$')
        self.assertEqual(a.code_for(nonce),a.code_for(nonce))
        self.assertNotEqual(a.code_for(nonce),b.code_for(nonce))
        self.assertNotEqual(a.code_digest(nonce,a.code_for(nonce)),a.code_digest(nonce,'00000000'))
    def test_customer_request_is_strict_and_consent_is_explicit(self):
        manager=support_access.AccessManager(None,b'a'*32)
        body={'reason':'Fictief softwareprobleem','duration_minutes':30,'request_key':'isolated-code-request-123','consent':True}
        for bad in ({**body,'organization_id':3},{**body,'owner_id':1},{**body,'consent':1},{**body,'duration_minutes':True},{**body,'reason':'kort'}):
            with self.subTest(bad=bad),self.assertRaises(support_access.AccessError):manager.customer_request({'id':2},bad)
    def test_accepted_code_expiry_fails_closed(self):
        for stamp in (None,'invalid','2020-01-01T00:00:00Z'):
            self.assertEqual(support_access.effective_status({'status':'accepted','code_expires_at':stamp}),'expired')
    def test_public_rows_remove_all_verification_material(self):
        raw={name:'private' for name in support_access.PRIVATE_FIELDS};raw['id']=1
        self.assertEqual(support_access.public_request(raw),{'id':1})

class SupportCodeHTTPTests(unittest.TestCase):
    setUpClass=classmethod(fixture.RefinementWorkflowTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.RefinementWorkflowTests.tearDownClass.__func__)
    setUp=fixture.RefinementWorkflowTests.setUp
    request=fixture.RefinementWorkflowTests.request
    client=fixture.RefinementWorkflowTests.client
    def begin(self,accept=True):
        customer,owner=self.client(2),self.client(1)
        body={'reason':'Fictief probleem met opslaan','duration_minutes':30,'request_key':'isolated-customer-support-key-123','consent':True}
        status,result,_=self.request(customer,'/api/support-access',body);self.assertEqual(status,201)
        row=self.db.rows['support_access_requests'][0]
        if accept:
            status,result,_=self.request(owner,f'/api/owner/support-access/{row["id"]}',{'action':'accept','version':row['version'],'duration_minutes':15});self.assertEqual(status,200)
        return customer,owner,row
    def code(self,customer):return self.request(customer,'/api/support-access')[1]['requests'][0]['code']
    def activate(self,owner,row,code):return self.request(owner,f'/api/owner/support-access/{row["id"]}/activate',{'version':row['version'],'code':code})
    def test_only_company_admin_initiates_and_owner_cannot_initiate(self):
        body={'reason':'Fictief probleem met opslaan','duration_minutes':30,'request_key':'isolated-code-request-123','consent':True}
        for uid in (None,1,3,4,6):
            self.assertIn(self.request(self.client(uid),'/api/support-access',body)[0],(401,403))
        self.assertEqual(self.request(self.client(1),'/api/owner/support-access',{'organization_id':2,**body})[0],403)
        self.assertEqual(self.db.rows.get('support_access_requests',[]),[])
    def test_customer_request_is_idempotent_and_scoped(self):
        customer,owner,row=self.begin(False)
        body={'reason':row['reason'],'duration_minutes':30,'request_key':row['request_key'],'consent':True}
        self.assertEqual(self.request(customer,'/api/support-access',body)[0],200)
        self.assertEqual(len(self.db.rows['support_access_requests']),1)
        self.assertEqual(self.request(customer,'/api/support-access',{**body,'organization_id':3})[0],400)
        self.assertEqual(self.request(self.client(2),f'/api/owner/support-access/{row["id"]}',{'action':'accept','version':1})[0],403)
    def test_acceptance_still_denies_access_and_code_only_goes_to_initiator(self):
        customer,owner,row=self.begin()
        self.assertEqual(self.request(owner,'/api/owner/context',{'organization_id':2})[0],403)
        payload=self.request(owner,'/api/owner/support-access')[1]['requests'][0]
        self.assertNotIn('code',payload)
        self.assertTrue(support_access.PRIVATE_FIELDS.isdisjoint(payload))
        self.assertRegex(self.code(customer),r'^[0-9]{8}$')
        self.db.rows['users'].append({**self.db.rows['users'][1],'id':9,'email':'actor9@test.invalid'})
        self.assertNotIn('code',self.request(self.client(9),'/api/support-access')[1]['requests'][0])
        self.assertEqual(self.mail,[])
    def test_code_activates_read_only_and_is_consumed_once(self):
        customer,owner,row=self.begin();code=self.code(customer)
        status,result,_=self.activate(owner,row,code);self.assertEqual(status,200)
        self.assertTrue(support_access.PRIVATE_FIELDS.isdisjoint(result['request']))
        self.assertIsNone(row['code_nonce']);self.assertIsNone(row['code_hash'])
        self.assertEqual(self.request(owner,'/api/me')[1]['organization_id'],2)
        self.assertEqual(self.request(owner,'/api/cases')[0],200)
        self.assertEqual(self.request(owner,'/api/cases',{'customer':'TEST'})[0],403)
        self.assertEqual(self.activate(owner,row,code)[0],409)
    def test_five_wrong_codes_lock_request_and_survive_refresh(self):
        customer,owner,row=self.begin();correct=self.code(customer);wrong='00000000' if correct!='00000000' else '11111111'
        for attempt in range(1,6):
            status,result,_=self.activate(owner,row,wrong)
            self.assertEqual(status,429 if attempt==5 else 400)
            self.assertEqual(row['code_attempts'],attempt)
            self.request(owner,'/api/owner/support-access')
        self.assertEqual(row['status'],'locked');self.assertIsNone(row['code_nonce'])
        self.assertEqual(self.activate(owner,row,correct)[0],409)
        self.assertEqual(self.request(owner,'/api/owner/context',{'organization_id':2})[0],403)
    def test_expired_code_and_customer_cancellation_prevent_activation(self):
        customer,owner,row=self.begin();code=self.code(customer)
        row['code_expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.assertNotIn('code',self.request(customer,'/api/support-access')[1]['requests'][0])
        self.assertEqual(self.activate(owner,row,code)[0],409)
    def test_customer_can_revoke_and_other_browser_session_cannot_reuse_grant(self):
        customer,owner,row=self.begin();self.activate(owner,row,self.code(customer))
        other=self.client(1)
        self.assertEqual(self.request(other,'/api/owner/context',{'organization_id':2})[0],403)
        self.assertEqual(self.request(other,'/api/owner/support-access')[1]['requests'][0]['effective_status'],'other_session')
        self.assertEqual(self.request(customer,f'/api/support-access/{row["id"]}',{'action':'revoke','version':row['version']})[0],200)
        self.assertEqual(self.request(owner,'/api/cases')[0],403)
        self.assertEqual(self.request(owner,'/api/me')[1]['organization_id'],1)
    def test_company_cancellation_before_code_is_immediate(self):
        customer,owner,row=self.begin();code=self.code(customer)
        self.assertEqual(self.request(customer,f'/api/support-access/{row["id"]}',{'action':'cancel','version':row['version']})[0],200)
        self.assertEqual(self.activate(owner,row,code)[0],409)
    def test_code_input_requires_digits_and_cannot_inject_scope(self):
        customer,owner,row=self.begin()
        for body in ({'version':row['version'],'code':'1234567'}, {'version':row['version'],'code':'１２３４５６７８'}, {'version':row['version'],'code':'12345678','organization_id':3}):
            self.assertEqual(self.request(owner,f'/api/owner/support-access/{row["id"]}/activate',body)[0],400)
        self.assertEqual(row['code_attempts'],0)
    def test_export_never_contains_code_or_session_verifiers(self):
        customer,owner,row=self.begin();nonce,mac=row['code_nonce'],row['code_hash']
        payload=self.request(customer,'/api/export')[1]
        import json
        text=json.dumps(payload,default=str)
        self.assertNotIn(nonce,text);self.assertNotIn(mac,text)
        self.assertEqual(self.mail,[])

if __name__=='__main__':unittest.main()
