"""Exercise private backup storage, failed copies, isolation and recovery."""
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import backup_archive
import operations
from test_refinement_workflow import MemoryDatabase, RefinementWorkflowTests


def payload(org_id=2):
    return {"meta":{"organization_id":org_id,"exported_at":"2026-10-06T12:00:00+00:00"},
            "organization":{"id":org_id,"name":"Geïsoleerd testbedrijf"},"settings":[],
            "users":[{"id":2,"email":"example@test.invalid"}],"customers":[],"support_tickets":[],
            "cases":[{"id":1,"organization_id":org_id}],"notes":[],"audit":[],"pilots":[],
            "pilot_snapshots":[],"attachments":[{"id":1,"case_id":1,"filename":"test.png",
              "storage_path":f"org/{org_id}/test.png","size_bytes":5}]}


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.db=MemoryDatabase({"backup_runs":[],"organizations":[{"id":2},{"id":3}]})
        self.db.files={"org/2/test.png":b"photo","org/3/test.png":b"photo"}
        self.clock=lambda:"2026-10-06T12:00:00+00:00"
        self.manager=operations.BackupManager(self.db,payload,"test-source",self.clock)

    def test_saved_copy_is_verified_downloadable_and_recoverable(self):
        result=self.manager.create(2,"manual/isolated-request-12345")
        self.assertEqual(result["status"],"complete")
        raw=self.manager.download(2,result["id"])
        with tempfile.TemporaryDirectory() as temp:
            restored=backup_archive.restore_to_new_folder(raw,Path(temp)/"recovery")
            self.assertEqual(restored["attachments"],1)
            self.assertEqual((Path(temp)/"recovery/attachments/1/test.png").read_bytes(),b"photo")
        self.assertEqual(self.manager.summary(2)["status"],"current")
        self.assertNotIn("storage_path",self.manager.list_runs(2)[0])
        self.assertNotIn("sha256",self.manager.list_runs(2)[0])

    def test_duplicate_request_does_not_store_a_second_archive(self):
        a=self.manager.create(2,"daily/2026-10-06")
        b=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(a,b)
        self.assertEqual(len(self.db.rows["backup_runs"]),1)

    def test_other_company_cannot_list_or_download_the_copy(self):
        result=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(self.manager.list_runs(3),[])
        with self.assertRaises(LookupError):self.manager.download(3,result["id"])

    def test_cross_company_export_and_unsafe_storage_reference_fail(self):
        self.manager.export=lambda org:payload(3)
        self.assertEqual(self.manager.create(2,"daily/2026-10-06")["status"],"failed")
        self.assertEqual(self.db.files,{"org/2/test.png":b"photo","org/3/test.png":b"photo"})
        self.manager.export=payload
        result=self.manager.create(2,"manual/isolated-request-12345")
        self.db.rows["backup_runs"][-1]["storage_path"]="org/3/2.zip"
        with self.assertRaises(ValueError):self.manager.download(2,result["id"])

    def test_missing_attachment_fails_without_a_partial_archive(self):
        del self.db.files["org/2/test.png"]
        result=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(result["error_code"],"attachment_failed")
        self.assertEqual(self.manager.summary(2)["status"],"failed")
        self.assertFalse(any(name.endswith(".zip") for name in self.db.files))

    def test_corrupt_read_back_is_never_marked_complete(self):
        original=self.db.download
        self.db.download=lambda name:b"corrupt" if name.endswith(".zip") else original(name)
        result=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(result["error_code"],"verification_failed")
        self.assertFalse(any(name.endswith(".zip") for name in self.db.files))
        self.assertEqual(self.manager.summary(2)["retained_versions"],0)

    def test_tampered_saved_archive_cannot_be_downloaded(self):
        result=self.manager.create(2,"daily/2026-10-06")
        self.db.files[f"org/2/{result['id']}.zip"]=b"changed"
        with self.assertRaises(ValueError):self.manager.download(2,result["id"])

    def test_failed_request_can_retry_and_complete_requests_remain_idempotent(self):
        self.db.files["org/2/test.png"]=b"bad"
        a=self.manager.create(2,"daily/2026-10-06")
        self.db.files["org/2/test.png"]=b"photo"
        b=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(a["id"],b["id"])
        self.assertEqual(b["status"],"complete")

    def test_retention_prunes_only_own_older_verified_archives(self):
        self.manager.create(3,"daily/2026-10-06")
        for index in range(9):self.manager.create(2,f"manual/isolated-request-{index:05d}")
        self.assertEqual(len([r for r in self.db.rows["backup_runs"] if r["organization_id"]==2 and r["status"]=="complete"]),7)
        self.assertEqual(len([r for r in self.db.rows["backup_runs"] if r["organization_id"]==2 and r["status"]=="expired"]),2)
        self.assertEqual(self.manager.summary(3)["retained_versions"],1)

    def test_failed_new_copy_never_prunes_existing_versions(self):
        self.manager.create(2,"daily/2026-10-06")
        before=set(self.db.files)
        self.db.files["org/2/test.png"]=b"bad"
        self.manager.create(2,"manual/isolated-request-12345")
        self.assertEqual(set(self.db.files),before)
        self.assertEqual(self.manager.summary(2)["retained_versions"],1)

    def test_expired_or_interrupted_runs_are_visible_and_do_not_claim_success(self):
        result=self.manager.create(2,"daily/2026-10-06")
        self.manager.clock=lambda:"2026-10-08T12:00:00+00:00"
        self.assertEqual(self.manager.summary(2)["status"],"overdue")
        self.db.rows["backup_runs"][0]["status"]="running"
        self.assertEqual(self.manager.summary(2)["status"],"interrupted")
        retry=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(retry["id"],result["id"])
        self.assertEqual(retry["status"],"complete")

    def test_size_and_total_budget_fail_without_extra_storage(self):
        with patch.object(operations,"MAX_STORED_BYTES",10):
            self.assertEqual(self.manager.create(2,"daily/2026-10-06")["error_code"],"size_limit")
        with patch.object(operations,"MAX_BACKUP_BUDGET",10):
            self.assertEqual(self.manager.create(2,"manual/isolated-request-12345")["error_code"],"storage_budget")
        self.assertFalse(any(name.endswith(".zip") for name in self.db.files))

    def test_actual_storage_including_unregistered_objects_is_bounded(self):
        self.manager.capacity=lambda:{"total_bytes":operations.MAX_STORAGE_USAGE,"backup_bytes":0}
        result=self.manager.create(2,"daily/2026-10-06")
        self.assertEqual(result["error_code"],"storage_budget")
        self.assertFalse(any(name.endswith(".zip") for name in self.db.files))

    def test_daily_uses_separate_company_snapshots_and_can_be_repeated(self):
        self.assertEqual([r["status"] for r in self.manager.daily()],["complete","complete"])
        self.manager.daily()
        self.assertEqual(len(self.db.rows["backup_runs"]),2)

    def test_pagination_does_not_omit_rows_after_rest_page_limit(self):
        self.db.rows["organizations"]=[{"id":i+1} for i in range(1201)]
        self.assertEqual(len(self.manager._all("organizations","id")),1201)

    def test_scheduler_token_is_required_and_compared_exactly(self):
        valid="test-secret-value-"*4
        self.assertTrue(operations.token_valid(valid,valid))
        for candidate in ("",valid+"x",None,"test-secret","niet-ascii-☃"):
            self.assertFalse(operations.token_valid(candidate,valid))
        self.assertFalse(operations.token_valid("short","short"))


class OperationsHTTPTests(RefinementWorkflowTests):
    def setUp(self):
        super().setUp()
        self.ns["backup_manager"]=lambda:operations.BackupManager(self.db,self._snapshot,self.ns["BUCKET"])
        self.started=[]
        self.ns["start_backup_job"]=lambda *args:self.started.append(args)

    def _snapshot(self,org_id):
        previous=self.ns["current_org_id"]()
        try:
            self.ns["_set_org_context"](org_id)
            return self.ns["export_payload"]()
        finally:self.ns["_set_org_context"](previous)

    def test_organization_backups_enforce_roles_and_scope(self):
        self.ns["backup_manager"]().create(2,"daily/2026-10-06")
        admin=self.client(2)
        result=self.request(admin,"/api/backups")
        self.assertEqual(result[0],200)
        bid=result[1]["runs"][0]["id"]
        self.assertEqual(self.request(admin,f"/api/backups/{bid}/download")[0],200)
        for uid,expected in ((None,401),(3,403),(4,403),(1,404)):
            self.assertEqual(self.request(self.client(uid),f"/api/backups/{bid}/download")[0],expected)
        self.assertEqual(self.request(self.client(2),"/api/owner/backups")[0],403)
        self.assertEqual(self.request(self.client(1),"/api/owner/backups")[0],200)

    def test_owner_can_inspect_backups_but_not_write_in_foreign_company(self):
        owner=self.client(1)
        self.request(owner,"/api/owner/context",{"organization_id":2})
        self.assertEqual(self.request(owner,"/api/backups")[0],200)
        self.assertEqual(self.request(owner,"/api/backups",{"request_key":"test-request-123456"})[0],403)
        self.assertEqual(self.started,[])

    def test_manual_backup_requires_valid_request_and_rejects_other_company_id(self):
        admin=self.client(2)
        self.assertEqual(self.request(admin,"/api/backups",{"request_key":"test-request-123456"})[0],202)
        self.assertEqual(self.started,[(2,"manual/test-request-123456")])
        self.assertEqual(self.request(admin,"/api/backups",{"request_key":"test-request-123456","organization_id":3})[0],400)
        self.assertEqual(self.request(self.client(3),"/api/backups",{"request_key":"test-request-123456"})[0],403)

    def test_daily_endpoint_requires_separate_scheduler_secret(self):
        anonymous=self.client()
        self.assertEqual(self.request(anonymous,"/api/internal/daily-backup",{})[0],401)
        with patch.dict(self.ns["os"].environ,{"OPERATIONS_TOKEN":"isolated-test-secret-"*4}):
            self.assertEqual(self.request(anonymous,"/api/internal/daily-backup",{},headers={"X-Werkstuur-Operations":"wrong"})[0],401)
            self.assertEqual(self.request(anonymous,"/api/internal/daily-backup",{},headers={"X-Werkstuur-Operations":"isolated-test-secret-"*4})[0],202)
        self.assertEqual(self.started,[()])

    def test_readiness_is_factual_and_missing_company_admin_is_visible(self):
        owner=self.client(1)
        self.assertEqual(self.request(self.client(4),"/api/pilot-readiness")[0],403)
        status,result,_=self.request(owner,"/api/pilot-readiness")
        self.assertEqual(status,200)
        self.assertFalse(result["complete"])
        self.assertFalse(next(c for c in result["checks"] if c["label"]=="Eigen bedrijfsbeheerder")["ready"])
        self.assertNotIn("intake_token",str(result))


if __name__=="__main__":unittest.main()
