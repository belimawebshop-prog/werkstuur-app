"""Intake regressions against the real HTTP handler and isolated private storage."""
import base64
import unittest
from unittest.mock import patch
import analysis_engine as engine
import test_refinement_workflow as fixtures

class IntakeQualityTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.RefinementWorkflowTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.RefinementWorkflowTests.tearDownClass.__func__)
    setUp = fixtures.RefinementWorkflowTests.setUp
    request = fixtures.RefinementWorkflowTests.request
    client = fixtures.RefinementWorkflowTests.client

    def intake(self, **changes):
        return {"token":"isolated-test-intake", "privacy_acknowledged":True,
                "customer":"TEST Klant", "city":"Testplaats", "email":"customer@test.invalid",
                "type":"Laadpaal", "problem":"Laden start niet", "extra":{"manufacturer":"Easee"}, **changes}

    def test_missing_or_invalid_contact_never_creates_case_or_mail(self):
        invalid = [{"customer":" "}, {"city":" "}, {"email":"geen-email"},
                   {"email":"", "phone":""}, {"phone":"abc"}, {"phone":"123"},
                   {"email":"x@test.invalid\nBcc: other@test.invalid"}, {"customer":["Naam"]},
                   {"city":"x"*101}, {"email":"x"*255}, {"intake_seconds":True},
                   {"type":["Laadpaal"]}, {"extra":[]}]
        for fields in invalid:
            with self.subTest(fields=list(fields)):
                status,data,_=self.request(self.client(), "/api/public-intake", self.intake(**fields))
                self.assertEqual(status,400,data)
                self.assertIn("error",data)
        self.assertEqual(self.db.rows["cases"],[])
        self.assertEqual(self.db.rows["attachments"],[])
        self.assertEqual(self.mail,[])
        self.assertEqual(self.server_errors,[])

    def test_email_only_and_international_phone_only_are_accepted_and_trimmed(self):
        for fields in ({"customer":"  TEST Klant  ","city":"  Testplaats  "},
                       {"email":"", "phone":" +31 (0)6 1234 5678 "}):
            status,data,_=self.request(self.client(), "/api/public-intake", self.intake(**fields))
            self.assertEqual(status,201,data)
            self.assertEqual(self.db.rows["cases"][-1]["customer"],"TEST Klant")
            self.assertEqual(self.db.rows["cases"][-1]["city"],"Testplaats")
        self.assertEqual(self.db.rows["cases"][-1]["phone"],"+31 (0)6 1234 5678")

    def test_invalid_photo_is_rejected_before_case_creation(self):
        for photo in ({"name":"x.png","type":"image/png","data_base64":"not base64"},
                      {"name":"x.html","type":"text/html","data_base64":base64.b64encode(b"test").decode()},
                      {"name":"x.png","type":["image/png"],"data_base64":base64.b64encode(b"test").decode()},
                      {"name":123,"type":"image/png","data_base64":base64.b64encode(b"test").decode()},
                      {"name":["x.png"],"type":"image/png","data_base64":base64.b64encode(b"test").decode()},
                      {"name":"x"*501,"type":"image/png","data_base64":base64.b64encode(b"test").decode()},
                      {"name":"x\x00.png","type":"image/png","data_base64":base64.b64encode(b"test").decode()},
                      {"name":"x.png","type":"image/png","data_base64":base64.b64encode(b"x"*(5*1024*1024+1)).decode()}):
            status,_,_=self.request(self.client(), "/api/public-intake", self.intake(file=photo))
            self.assertEqual(status,400)
        self.assertEqual(self.db.rows["cases"],[])
        self.assertEqual(self.mail,[])


    def test_photo_storage_failure_still_returns_received_reference(self):
        photo={"name":"test.png","type":"image/png","data_base64":base64.b64encode(b"synthetic-photo").decode()}
        with patch.object(self.db.storage,"from_",side_effect=RuntimeError("isolated upload unavailable")):
            status,data,_=self.request(self.client(), "/api/public-intake", self.intake(file=photo))
        self.assertEqual(status,201,data)
        self.assertIs(data["attachment_saved"],False)
        self.assertEqual(len(self.db.rows["cases"]),1)
        self.assertEqual(data["case_no"],self.db.rows["cases"][0]["case_no"])
        self.assertEqual(self.db.rows["attachments"],[])

    def test_photo_is_confirmed_only_after_private_attachment_storage(self):
        photo={"name":"test.png","type":"image/png","data_base64":base64.b64encode(b"synthetic-photo").decode()}
        status,data,_=self.request(self.client(), "/api/public-intake", self.intake(file=photo))
        self.assertEqual(status,201,data)
        self.assertIs(data["attachment_saved"],True)
        self.assertEqual(len(self.db.rows["attachments"]),1)
        case=self.db.rows["cases"][0]
        self.assertIn("Foto toegevoegd: ja",case["facts"])
        self.assertTrue(self.db.rows["attachments"][0]["storage_path"].startswith("org/2/cases/"))

    def test_bad_json_shape_returns_user_error(self):
        for payload in ([],"not an object",None):
            if payload is None:payload=123
            self.assertEqual(self.request(self.client(),"/api/public-intake",payload)[0],400)
        self.assertEqual(self.db.rows["cases"],[])
        self.assertEqual(self.server_errors,[])

    def test_simultaneous_millisecond_does_not_duplicate_references(self):
        with patch("time.time",return_value=1791300000.123):
            references=[]
            for _ in range(4):
                status,data,_=self.request(self.client(), "/api/public-intake", self.intake())
                self.assertEqual(status,201)
                references.append(data["case_no"])
        self.assertEqual(len(set(references)),4)

    def test_legacy_case_is_reassessed_without_inventing_answers(self):
        old={"type":"Zonnepanelen / omvormer","problem":"Geen productie", "manufacturer":"SolarEdge", "facts":[],"score":82,"dispatch":"Oud advies"}
        current=engine.for_case(old)
        self.assertEqual(current["type"],"Zonnepanelen")
        self.assertIn("Model",current["missing"])
        self.assertLess(current["score"],82)
        self.assertTrue(current["warnings"])
        self.assertEqual(old["score"],82)
        self.assertEqual(old["type"],"Zonnepanelen / omvormer")

    def test_planner_can_repair_unsupported_case_but_technician_and_owner_cannot(self):
        self.db.rows["cases"].append({"id":91,"organization_id":2,"type":"Oud onbekend type","customer":"TEST","problem":"Bestaande klacht","facts":[],"version":1,"assigned_to":4})
        body={"type":"Zonnepanelen","problem":"Geen productie","extra":{"manufacturer":"SolarEdge"},"version":1}
        self.assertEqual(self.request(self.client(4),"/api/cases/91/analysis",body)[0],403)
        owner=self.client(1)
        self.assertEqual(self.request(owner,"/api/owner/context",{"organization_id":2})[0],200)
        self.assertEqual(self.request(owner,"/api/cases/91/analysis",body)[0],403)
        status,data,_=self.request(self.client(3),"/api/cases/91/analysis",body)
        self.assertEqual(status,200,data)
        self.assertEqual(data["type"],"Zonnepanelen")
        self.assertEqual(data["version"],2)
        self.assertFalse(data["analysis"].get("unavailable",False))

if __name__=="__main__":unittest.main()
