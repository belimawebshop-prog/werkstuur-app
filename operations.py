"""Private, verified organisation backups on the existing free infrastructure."""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
from datetime import datetime, timedelta, timezone

import backup_archive

BACKUP_BUCKET = "werkstuur-backups"
MAX_STORED_BYTES = 32 * 1024 * 1024
MAX_BACKUP_BUDGET = 200 * 1024 * 1024
RETENTION = 7
MAX_STORAGE_USAGE = 800 * 1024 * 1024
_LOCK = threading.Lock()
_ACTIVE = set()


def rows(response):
    return getattr(response, "data", None) or []


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def token_valid(supplied, expected):
    return bool(isinstance(expected, str) and len(expected) >= 40 and
                expected.isascii() and isinstance(supplied, str) and supplied.isascii() and
                hmac.compare_digest(supplied, expected))


class BackupManager:
    def __init__(self, db, export, source_bucket, clock=iso_now, capacity=None):
        self.db, self.export, self.source_bucket, self.clock = db, export, source_bucket, clock
        self.capacity = capacity

    def _all(self, table, columns="*", filters=(), order="id", desc=False):
        result, offset = [], 0
        while True:
            query = self.db.table(table).select(columns)
            for key, value in filters:
                query = query.eq(key, value)
            batch = rows(query.order(order, desc=desc).range(offset, offset + 499).execute())
            result.extend(batch)
            if len(batch) < 500:
                return result
            offset += 500

    def list_runs(self, org_id=None, limit=40):
        query = self.db.table("backup_runs").select(
            "id,organization_id,status,created_at,verified_at,size_bytes,file_count,error_code,expired_at")
        if org_id is not None:
            query = query.eq("organization_id", org_id)
        return rows(query.order("id", desc=True).limit(limit).execute())

    def summary(self, org_id=None):
        runs = self.list_runs(org_id)
        complete = [r for r in runs if r["status"] == "complete"]
        latest = complete[0] if complete else None
        recent_failure = next((r for r in runs if r["status"] == "failed"), None)
        status = "not_created"
        if latest:
            age = datetime.fromisoformat(self.clock()) - datetime.fromisoformat(latest["verified_at"])
            status = "current" if age < timedelta(hours=30) else "overdue"
        if recent_failure and (not latest or recent_failure["id"] > latest["id"]):
            status = "failed"
        if runs and runs[0]["status"] == "running":
            age = datetime.fromisoformat(self.clock()) - datetime.fromisoformat(runs[0]["created_at"])
            status = "running" if age < timedelta(hours=1) else "interrupted"
        return {"status": status, "latest_verified_at": latest.get("verified_at") if latest else None,
                "retained_versions": len(complete), "retention": RETENTION,
                "maximum_mb": 32, "runs": runs}

    def _reserve(self, org_id, request_key):
        if not re.fullmatch(r"[A-Za-z0-9/_-]{16,100}", request_key):
            raise ValueError("ongeldige aanvraagcode")
        existing = rows(self.db.table("backup_runs").select("*").eq("organization_id", org_id)
                        .eq("request_key", request_key).limit(1).execute())
        if existing:
            # A completed request is never repeated, even after its retained file expires.
            if existing[0]["status"] in ("complete", "expired"):
                return existing[0], False
            stale = datetime.fromisoformat(self.clock()) - datetime.fromisoformat(existing[0]["created_at"])
            if existing[0]["status"] == "running" and stale < timedelta(hours=1):
                return existing[0], False
            run = rows(self.db.table("backup_runs").update({"status": "running", "created_at": self.clock(),
                "error_code": None}).eq("id", existing[0]["id"]).execute())[0]
            return run, True
        run = rows(self.db.table("backup_runs").insert({"organization_id": org_id,
            "request_key": request_key, "status": "running", "created_at": self.clock(),
            "size_bytes": 0, "file_count": 0}).execute())[0]
        return run, True

    def create(self, org_id, request_key):
        key = (id(self.db), org_id)
        with _LOCK:
            if key in _ACTIVE:
                return {"status": "running", "organization_id": org_id}
            _ACTIVE.add(key)
        try:
            run, pending = self._reserve(org_id, request_key)
            if not pending:
                return {k: run.get(k) for k in ("id", "organization_id", "status")}
            path = f"org/{org_id}/{run['id']}.zip"
            stage = "snapshot_failed"
            uploaded = False
            try:
                payload = self.export(org_id)
                if payload.get("meta", {}).get("organization_id") != org_id:
                    raise ValueError("organisatieverschil")
                stage = "attachment_failed"
                raw = backup_archive.build_archive(payload, self.db.storage.from_(self.source_bucket).download)
                backup_archive.verify_archive(raw)
                if len(raw) > MAX_STORED_BYTES:
                    stage = "size_limit"
                    raise ValueError("de opgeslagen backup is groter dan 32 MB")
                used = sum(int(r.get("size_bytes") or 0) for r in
                           self._all("backup_runs", "id,size_bytes", (("status", "complete"),)))
                if used + len(raw) > MAX_BACKUP_BUDGET:
                    stage = "storage_budget"
                    raise ValueError("de private backupopslag heeft zijn budget bereikt")
                if self.capacity:
                    usage = self.capacity()
                    if (int(usage["backup_bytes"]) + len(raw) > MAX_BACKUP_BUDGET or
                            int(usage["total_bytes"]) + len(raw) > MAX_STORAGE_USAGE):
                        stage = "storage_budget"
                        raise ValueError("de private opslag heeft zijn budget bereikt")
                stage = "storage_failed"
                bucket = self.db.storage.from_(BACKUP_BUCKET)
                bucket.upload(path, raw, file_options={"content-type": "application/zip", "upsert": "true"})
                uploaded = True
                stage = "verification_failed"
                stored = bucket.download(path)
                digest = hashlib.sha256(raw).hexdigest()
                if not isinstance(stored, bytes) or hashlib.sha256(stored).hexdigest() != digest:
                    raise ValueError("opslagcontrole mislukt")
                checked, manifest, files = backup_archive.verify_archive(stored)
                if checked["meta"]["organization_id"] != org_id:
                    raise ValueError("organisatieverschil")
                stage = "metadata_failed"
                rows(self.db.table("backup_runs").update({"status": "complete", "storage_path": path,
                    "size_bytes": len(raw), "file_count": len(files), "sha256": digest,
                    "verified_at": self.clock(), "error_code": None}).eq("id", run["id"]).execute())
                # Expire old files only after a new archive was read back and fully verified.
                self._prune(org_id)
                return {"id": run["id"], "organization_id": org_id, "status": "complete"}
            except Exception:
                if uploaded and stage != "metadata_failed":
                    try:
                        self.db.storage.from_(BACKUP_BUCKET).remove([path])
                    except Exception:
                        pass
                self.db.table("backup_runs").update({"status": "failed", "error_code": stage}).eq("id", run["id"]).execute()
                return {"id": run["id"], "organization_id": org_id, "status": "failed", "error_code": stage}
        finally:
            with _LOCK:
                _ACTIVE.discard(key)

    def _prune(self, org_id):
        complete = self._all("backup_runs", filters=(("organization_id", org_id), ("status", "complete")), desc=True)
        for run in complete[RETENTION:]:
            # A cleanup failure does not invalidate the newly verified archive.
            try:
                path = run.get("storage_path") or ""
                if not path.startswith(f"org/{org_id}/"):
                    continue
                self.db.storage.from_(BACKUP_BUCKET).remove([path])
                self.db.table("backup_runs").update({"status": "expired", "storage_path": None,
                    "expired_at": self.clock()}).eq("id", run["id"]).eq("organization_id", org_id).execute()
            except Exception:
                continue

    def download(self, org_id, run_id):
        found = rows(self.db.table("backup_runs").select("*").eq("organization_id", org_id)
                     .eq("id", run_id).eq("status", "complete").limit(1).execute())
        if not found:
            raise LookupError("backup niet gevonden")
        run = found[0]
        path = run.get("storage_path") or ""
        if not path.startswith(f"org/{org_id}/"):
            raise ValueError("ongeldige opslagverwijzing")
        raw = self.db.storage.from_(BACKUP_BUCKET).download(path)
        if hashlib.sha256(raw).hexdigest() != run["sha256"]:
            raise ValueError("de backup doorstaat de integriteitscontrole niet")
        payload, manifest, files = backup_archive.verify_archive(raw)
        if payload["meta"]["organization_id"] != org_id:
            raise ValueError("organisatieverschil")
        return raw

    def daily(self):
        day = datetime.fromisoformat(self.clock()).date().isoformat()
        results = []
        for org in self._all("organizations", "id"):
            results.append(self.create(org["id"], "daily/" + day))
        return results
