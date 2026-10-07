"""Customer-approved support access. The database is authoritative on every read.

No passwords, impersonation, browser tokens, emails or write grants are involved.
"""
from datetime import datetime, timezone
import re


class AccessError(Exception):
    def __init__(self, message, status=403, code="support_access_required"):
        super().__init__(message)
        self.status, self.code = status, code


def effective_status(row, now=None):
    status = row.get("status")
    if status not in ("pending", "approved"):
        return status
    deadline = row.get("expires_at" if status == "approved" else "request_expires_at")
    try:
        expires = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
        if expires.tzinfo is None or expires <= (now or datetime.now(timezone.utc)):
            return "expired"
    except (TypeError, ValueError):
        return "expired"
    return status


def positive_id(value):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise AccessError("Ongeldige aanvraag of organisatie.", 400, "invalid_request")
    return int(value)


class AccessManager:
    def __init__(self, db):
        self.db = db

    def rpc(self, name, params):
        result = self.db.rpc(name, params).execute().data
        if not isinstance(result, dict):
            raise RuntimeError("toestemmingscontrole niet beschikbaar")
        if result.get("error"):
            raise AccessError(result["error"], int(result.get("http_status", 403)),
                              result.get("code", "support_access_required"))
        return result

    def grant(self, actor_id, organization_id):
        result = self.rpc("werkstuur_support_access_check", {
            "p_actor_id": actor_id, "p_organization_id": organization_id})
        return result.get("grant")

    def request(self, actor, body):
        if set(body) != {"organization_id", "reason", "duration_minutes", "request_key"}:
            raise AccessError("Ongeldige velden in het toestemmingsverzoek.", 400, "invalid_request")
        oid = positive_id(body["organization_id"])
        reason = body["reason"]
        duration, key = body["duration_minutes"], body["request_key"]
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", reason):
            raise AccessError("Beschrijf de reden in 10 tot 1000 tekens.", 400, "invalid_request")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration not in (15, 30, 60):
            raise AccessError("Kies 15, 30 of 60 minuten.", 400, "invalid_request")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", key):
            raise AccessError("Ongeldige aanvraagcode.", 400, "invalid_request")
        return self.rpc("werkstuur_support_access_change", {
            "p_actor_id": actor["id"], "p_action": "request", "p_organization_id": oid,
            "p_reason": reason.strip(), "p_duration_minutes": duration, "p_request_key": key})

    def decide(self, actor, request_id, body, owner=False):
        if set(body) - {"action", "version", "duration_minutes"} or "action" not in body or "version" not in body:
            raise AccessError("Ongeldige velden in het besluit.", 400, "invalid_request")
        action = body["action"]
        if action not in (("cancel",) if owner else ("approve", "reject", "revoke")):
            raise AccessError("Je kunt dit besluit niet nemen.", 403, "forbidden")
        version = positive_id(body["version"])
        duration = body.get("duration_minutes", 60)
        if isinstance(duration, bool) or not isinstance(duration, int) or duration not in (15, 30, 60):
            raise AccessError("Kies 15, 30 of 60 minuten.", 400, "invalid_request")
        return self.rpc("werkstuur_support_access_change", {
            "p_actor_id": actor["id"], "p_action": action,
            "p_request_id": positive_id(request_id), "p_version": version,
            "p_duration_minutes": duration})

    def context(self, actor, session_hash, organization_id):
        return self.rpc("werkstuur_support_context", {
            "p_actor_id": actor["id"], "p_session_hash": session_hash,
            "p_organization_id": positive_id(organization_id)})

    def listing(self, actor, owner=False):
        query = self.db.table("support_access_requests").select("*")
        if owner:
            query = query.eq("requester_id", actor["id"])
        else:
            query = query.eq("organization_id", actor["organization_id"])
        rows = query.order("id", desc=True).limit(250).execute().data or []
        orgs = {r["id"]: r["name"] for r in (self.db.table("organizations").select("id,name").execute().data or [])} if owner else {}
        people = {}
        for row in rows:
            row.pop("request_key", None)
            row["effective_status"] = effective_status(row)
            for key in ("requester_id", "decided_by"):
                uid = row.get(key)
                if uid and uid not in people:
                    found = self.db.table("users").select("id,display_name").eq("id", uid).limit(1).execute().data or []
                    people[uid] = found[0].get("display_name", "Gebruiker") if found else "Voormalige gebruiker"
            row["requester_name"] = people.get(row.get("requester_id"), "Werkstuur")
            row["decided_by_name"] = people.get(row.get("decided_by"))
            row["organization_name"] = orgs.get(row["organization_id"], actor.get("organization_name", "Je bedrijf"))
            if row["effective_status"] == "approved" and not self.grant(row["requester_id"], row["organization_id"]):
                row["effective_status"] = "invalidated"
        events = self.db.table("support_access_events").select("*")
        events = events.eq("requester_id", actor["id"]) if owner else events.eq("organization_id", actor["organization_id"])
        return {"requests": rows, "events": events.order("id", desc=True).limit(250).execute().data or [],
                "server_time": datetime.now(timezone.utc).isoformat()}
