"""Customer-initiated, code-confirmed support. All grants are session-bound."""
from datetime import datetime, timezone
import hashlib
import hmac
import re
import secrets

PRIVATE_FIELDS = {'request_key', 'code_nonce', 'code_hash', 'activated_session_hash'}

def public_request(row):
    return {k: v for k, v in row.items() if k not in PRIVATE_FIELDS}

class AccessError(Exception):
    def __init__(self, message, status=403, code='support_access_required'):
        super().__init__(message)
        self.status, self.code = status, code

def effective_status(row, now=None):
    status = row.get('status')
    if status not in ('pending', 'accepted', 'approved'):
        return status
    field = {'approved':'expires_at', 'accepted':'code_expires_at', 'pending':'request_expires_at'}[status]
    try:
        expires = datetime.fromisoformat(str(row.get(field)).replace('Z', '+00:00'))
        if expires.tzinfo is None or expires <= (now or datetime.now(timezone.utc)):
            return 'expired'
    except (TypeError, ValueError):
        return 'expired'
    return status

def positive_id(value):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise AccessError('Ongeldige aanvraag of organisatie.', 400, 'invalid_request')
    return int(value)

def duration(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (15, 30, 60):
        raise AccessError('Kies 15, 30 of 60 minuten.', 400, 'invalid_request')
    return value

class AccessManager:
    def __init__(self, db, code_secret=b''):
        self.db, self.code_secret = db, code_secret

    def rpc(self, name, params):
        result = self.db.rpc(name, params).execute().data
        if not isinstance(result, dict):
            raise RuntimeError('toestemmingscontrole niet beschikbaar')
        if result.get('error'):
            raise AccessError(result['error'], int(result.get('http_status', 403)), result.get('code', 'support_access_required'))
        if isinstance(result.get('request'), dict):
            result['request'] = public_request(result['request'])
        return result

    def grant(self, actor_id, organization_id, session_hash=''):
        return self.rpc('werkstuur_support_access_check', {
            'p_actor_id': actor_id, 'p_organization_id': organization_id,
            'p_session_hash': session_hash}).get('grant')

    def _mac(self, text):
        if not isinstance(self.code_secret, bytes) or len(self.code_secret) < 32:
            raise RuntimeError('beveiligde supportcodes niet beschikbaar')
        return hmac.new(self.code_secret, text.encode('ascii'), hashlib.sha256).digest()

    def code_for(self, nonce):
        if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{64}', nonce):
            raise RuntimeError('beveiligde supportcode niet beschikbaar')
        return f"{int.from_bytes(self._mac('display|'+nonce), 'big') % 100000000:08d}"

    def code_digest(self, nonce, code):
        self.code_for(nonce)  # Validate nonce and server key; never trust a browser nonce.
        return self._mac('verify|'+nonce+'|'+code).hex()

    def request(self, actor, body):
        raise AccessError('Het klantbedrijf vraagt zelf support aan via Ondersteuning.', 403, 'customer_initiated_required')

    def customer_request(self, actor, body):
        if set(body) != {'reason', 'duration_minutes', 'request_key', 'consent'} or body.get('consent') is not True:
            raise AccessError('Bevestig de tijdelijke alleen-lezen inzage.', 400, 'invalid_request')
        reason, key = body['reason'], body['request_key']
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', reason):
            raise AccessError('Beschrijf het probleem in 10 tot 1000 tekens.', 400, 'invalid_request')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', key):
            raise AccessError('Ongeldige aanvraagcode.', 400, 'invalid_request')
        return self.rpc('werkstuur_support_code_change', {'p_actor_id': actor['id'], 'p_action':'request',
            'p_reason':reason.strip(), 'p_duration_minutes':duration(body['duration_minutes']),
            'p_request_key':key, 'p_consent':True})

    def decide(self, actor, request_id, body, owner=False):
        if set(body)-{'action','version','duration_minutes'} or 'action' not in body or 'version' not in body:
            raise AccessError('Ongeldige velden in het besluit.', 400, 'invalid_request')
        action = body['action']
        if action not in (('accept','reject','cancel') if owner else ('cancel','revoke')):
            raise AccessError('Je kunt dit besluit niet nemen.', 403, 'forbidden')
        params = {'p_actor_id':actor['id'], 'p_action':action, 'p_request_id':positive_id(request_id),
                  'p_version':positive_id(body['version']), 'p_duration_minutes':duration(body.get('duration_minutes',60))}
        if action == 'accept':
            nonce = secrets.token_hex(32)
            params.update(p_code_nonce=nonce, p_code_hash=self.code_digest(nonce, self.code_for(nonce)))
        return self.rpc('werkstuur_support_code_change', params)

    def activate(self, actor, session_hash, request_id, body):
        if set(body) != {'code','version'} or not isinstance(body['code'], str):
            raise AccessError('Vul de acht cijfers van de klantcode in.', 400, 'invalid_request')
        code = re.sub(r'[ -]', '', body['code'])
        if not re.fullmatch('[0-9]{8}', code):
            raise AccessError('Vul de acht cijfers van de klantcode in.', 400, 'invalid_request')
        rid, version = positive_id(request_id), positive_id(body['version'])
        rows = self.db.table('support_access_requests').select('code_nonce,status,flow').eq('id',rid).eq('requester_id',actor['id']).limit(1).execute().data or []
        if not rows or rows[0].get('status') != 'accepted' or rows[0].get('flow') != 'customer_code':
            raise AccessError('Dit verzoek heeft geen actieve toegangscode.', 409, 'request_closed')
        nonce = rows[0]['code_nonce']
        return self.rpc('werkstuur_support_code_change', {'p_actor_id':actor['id'], 'p_action':'activate',
            'p_request_id':rid, 'p_version':version, 'p_code_nonce':nonce,
            'p_code_hash':self.code_digest(nonce,code), 'p_session_hash':session_hash})

    def context(self, actor, session_hash, organization_id):
        return self.rpc('werkstuur_support_context', {'p_actor_id':actor['id'],
            'p_session_hash':session_hash, 'p_organization_id':positive_id(organization_id)})

    def listing(self, actor, owner=False, session_hash=''):
        query = self.db.table('support_access_requests').select('*')
        query = query.eq('requester_id',actor['id']) if owner else query.eq('organization_id',actor['organization_id'])
        rows = query.order('id',desc=True).limit(250).execute().data or []
        orgs = {r['id']:r['name'] for r in (self.db.table('organizations').select('id,name').execute().data or [])} if owner else {}
        people, public_rows = {}, []
        for raw in rows:
            row = public_request(raw)
            row['effective_status'] = effective_status(raw)
            for key in ('requester_id','decided_by','initiated_by'):
                uid = raw.get(key)
                if uid and uid not in people:
                    found = self.db.table('users').select('id,display_name').eq('id',uid).limit(1).execute().data or []
                    people[uid] = found[0].get('display_name','Gebruiker') if found else 'Voormalige gebruiker'
            row.update(requester_name=people.get(raw.get('requester_id'),'Werkstuur'),
                decided_by_name=people.get(raw.get('decided_by')),initiated_by_name=people.get(raw.get('initiated_by')),
                organization_name=orgs.get(raw['organization_id'],actor.get('organization_name','Je bedrijf')))
            if row['effective_status'] == 'approved':
                valid = self.grant(raw['requester_id'],raw['organization_id'],raw.get('activated_session_hash',''))
                if not valid:row['effective_status']='invalidated'
                elif owner and raw.get('activated_session_hash') != session_hash:row['effective_status']='other_session'
            if raw.get('flow') == 'customer_code' and row['effective_status'] == 'accepted' and not owner and raw.get('initiated_by') == actor['id']:
                row['code'] = self.code_for(raw['code_nonce'])
            public_rows.append(row)
        events = self.db.table('support_access_events').select('*')
        events = events.eq('requester_id',actor['id']) if owner else events.eq('organization_id',actor['organization_id'])
        return {'requests':public_rows,'events':events.order('id',desc=True).limit(250).execute().data or [],
                'server_time':datetime.now(timezone.utc).isoformat()}
