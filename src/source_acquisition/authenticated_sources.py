"""仅本机候选：显式Schema、注入Vault及内部Driver，不接真实网站。"""
import json
import re
import sqlite3
import uuid
import time
import math
import hashlib
from pathlib import Path
from contextlib import contextmanager


class AuthenticatedSources:
    def __init__(self,path,vault,*,drivers):
        self.path=Path(path).resolve()
        if not self.path.is_file(): raise ValueError("schema_not_ready")
        self.vault=vault
        self.drivers=dict(drivers)
        if not self.drivers or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v or len(k)>100 or len(v)>100 for k,v in self.drivers.items()): raise ValueError('invalid_driver_registry')
        with self._db() as conn:
            for table in ('authenticated_source_connections','source_reauthentication_requests'):
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    raise ValueError('schema_not_ready')

    @contextmanager
    def _db(self):
        conn=sqlite3.connect(self.path.as_uri()+"?mode=rw",uri=True,timeout=5)
        conn.row_factory=sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _identity(self,owner,website,account,*,discover=False):
        if website not in self.drivers: raise ValueError('unsupported_website')
        if any(not isinstance(value,str) or not value or len(value)>200 or any(ord(c)<32 for c in value) for value in ((owner,) if discover and account is None else (owner,account))):
            raise ValueError('invalid_identity')

    def connection(self,owner,website,account):
        self._identity(owner,website,account)
        with self._db() as conn:
            row=conn.execute('SELECT active_version,usable FROM authenticated_source_connections WHERE owner=? AND website=? AND account=?',(owner,website,account)).fetchone()
            return {'active_version':row['active_version'],'usable':bool(row['usable'])} if row else None

    def _encrypt_state(self, owner, website, account, version, encoded):
        # Vault认证密文同时绑定用途和账号版本，避免同一主密钥下的密文被跨连接替换。
        return self.vault.encrypt(json.dumps({
            'purpose':'authenticated-source-v1', 'owner':owner, 'website':website,
            'account':account, 'version':version, 'state':json.loads(encoded),
        }, ensure_ascii=False, allow_nan=False))

    def load_verified_state(self, owner, website, account, *, expected_version, driver):
        """仅内部受控Driver复用；调用者仍需持有原执行锁并核授权范围。"""
        self._identity(owner,website,account)
        if type(expected_version) is not int or expected_version<1: raise ValueError('invalid_version')
        if getattr(driver,'website',None)!=website or getattr(driver,'version',None)!=self.drivers[website]: raise ValueError('driver_mismatch')
        with self._db() as conn:
            row=conn.execute('SELECT * FROM authenticated_source_connections WHERE owner=? AND website=? AND account=?',(owner,website,account)).fetchone()
        if row is None or not row['usable'] or row['active_version']!=expected_version: return None
        envelope=json.loads(self.vault.decrypt(row['ciphertext']))
        expected={'purpose':'authenticated-source-v1','owner':owner,'website':website,'account':account,'version':expected_version}
        if not isinstance(envelope,dict) or set(envelope)!=set(expected)|{'state'} or any(envelope[key]!=value for key,value in expected.items()) or not isinstance(envelope['state'],dict):
            raise ValueError('state_binding_mismatch')
        state=envelope['state']
        encoded=json.dumps(state,ensure_ascii=False,allow_nan=False)
        result=driver.verify(owner,website,account,state)
        if json.dumps(state,ensure_ascii=False,allow_nan=False)!=encoded: raise ValueError('driver_mutated_state')
        if not isinstance(result,dict) or result.get('status') not in ('valid','invalid','unknown','blocked'): raise ValueError('invalid_driver_result')
        if result['status']=='valid' and result.get('account_id')!=account: raise ValueError('account_mismatch')
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=conn.execute('SELECT * FROM authenticated_source_connections WHERE owner=? AND website=? AND account=?',(owner,website,account)).fetchone()
            if current is None or not current['usable'] or current['active_version']!=expected_version or current['ciphertext']!=row['ciphertext']: return None
            # 网络未知不改凭据；明确失效也只停用刚验证的同一版本，保留旧密文。
            if result['status']=='invalid':
                conn.execute('UPDATE authenticated_source_connections SET usable=0 WHERE owner=? AND website=? AND account=?',(owner,website,account))
            return state if result['status']=='valid' else None

    def request(self,owner,website,account,status,binding,*,now,ttl,authorization_digest,idempotency_key,observed_version=None):
        self._identity(owner,website,account,discover=True)
        if status not in ('login_required','invalid','unknown','blocked','valid'): raise ValueError('invalid_status')
        if not isinstance(authorization_digest,str) or re.fullmatch('[0-9a-f]{64}',authorization_digest) is None: raise ValueError('invalid_authorization')
        binding={} if binding is None else binding
        if not isinstance(binding,dict) or set(binding) not in (set(),{'attempt_id'},{'task_id','revision','run_id'}): raise ValueError('invalid_binding')
        for key,value in binding.items():
            if key=='revision':
                if type(value) is not int or value<1: raise ValueError('invalid_binding')
            elif not isinstance(value,str) or not value or len(value)>200: raise ValueError('invalid_binding')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (now,ttl)): raise ValueError('invalid_time')
        if not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=200: raise ValueError('invalid_key')
        if observed_version is not None and (type(observed_version) is not int or observed_version<0): raise ValueError('invalid_version')
        if not 0<ttl<=600: raise ValueError('invalid_expiry')
        if status not in ('login_required','invalid'): return None
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            fingerprint=hashlib.sha256(json.dumps([website,account,status,binding,authorization_digest,self.drivers[website],ttl,observed_version],sort_keys=True).encode()).hexdigest()
            previous=conn.execute('SELECT request_id,request_hash,state FROM source_reauthentication_requests WHERE owner=? AND idempotency_key=?',(owner,idempotency_key)).fetchone()
            if previous:
                if previous['request_hash']!=fingerprint: raise ValueError('idempotency_conflict')
                return {'request_id':previous['request_id'],'state':previous['state']}
            version=0
            if account is not None:
                conn.execute('INSERT OR IGNORE INTO authenticated_source_connections VALUES (?,?,?,0,NULL,0)',(owner,website,account))
                version=conn.execute('SELECT active_version FROM authenticated_source_connections WHERE owner=? AND website=? AND account=?',(owner,website,account)).fetchone()[0]
                # 检测结果绑定受检版本；旧网络响应不能停用期间刚保存的新登录态。
                if version!=(0 if observed_version is None else observed_version): raise ValueError('connection_version_changed')
                conn.execute('UPDATE authenticated_source_connections SET usable=0 WHERE owner=? AND website=? AND account=?',(owner,website,account))
            identity=uuid.uuid4().hex
            conn.execute('INSERT INTO source_reauthentication_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(identity,idempotency_key,fingerprint,owner,website,account,version,json.dumps(binding,sort_keys=True),self.drivers[website],authorization_digest,'pending',now+ttl))
            return {'request_id':identity,'state':'pending'}

    def _request(self,conn,owner,identity):
        row=conn.execute('SELECT * FROM source_reauthentication_requests WHERE owner=? AND request_id=?',(owner,identity)).fetchone()
        if row is None: raise PermissionError('request_not_found')
        return row

    def get_request(self,owner,identity):
        with self._db() as conn:
            row=self._request(conn,owner,identity)
            return {'request_id':row['request_id'],'website':row['website'],'account':row['account'],'binding':json.loads(row['binding_json']),'state':row['state'],'expires':row['expires']}

    def cancel(self,owner,identity):
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._request(conn,owner,identity)
            # 连接验证成功不等于任务已领取；取消只撤回本待办，不撤销账号密文。
            conn.execute("UPDATE source_reauthentication_requests SET state='cancelled' WHERE owner=? AND request_id=? AND state IN ('pending','verified')",(owner,identity))
            return self._request(conn,owner,identity)['state']

    def confirm(self,owner,identity,state,driver,*,now):
        began=time.monotonic()
        raw_clock=now if callable(now) else lambda:now+time.monotonic()-began
        def clock():
            value=raw_clock()
            if type(value) not in (int,float) or not math.isfinite(value): raise ValueError('invalid_time')
            return value
        if not callable(now) and (type(now) not in (int,float) or not math.isfinite(now)): raise ValueError('invalid_time')
        # Driver仅由内部调用者注入；网络/运行绑定授权复核属于正式集成门。
        encoded=json.dumps(state,ensure_ascii=False,allow_nan=False)
        if not isinstance(state,dict) or len(encoded.encode('utf-8'))>65536: raise ValueError('invalid_state')
        with self._db() as conn:
            row=self._request(conn,owner,identity)
            if row['state']!='pending': return row['state']
            if clock()>=row['expires']:
                conn.execute("UPDATE source_reauthentication_requests SET state='expired' WHERE owner=? AND request_id=? AND state='pending'",(owner,identity))
                return self._request(conn,owner,identity)["state"]
        if self.drivers.get(row['website'])!=row['driver_version'] or getattr(driver,'website',None)!=row['website'] or getattr(driver,'version',None)!=row['driver_version']: raise ValueError('driver_mismatch')
        frozen=json.loads(encoded)
        result=driver.verify(owner,row['website'],row['account'],frozen)
        if json.dumps(frozen,ensure_ascii=False,allow_nan=False)!=encoded: raise ValueError('driver_mutated_state')
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=self._request(conn,owner,identity)
            if current['state']!='pending': return current['state']
            # 返回后重新核状态，取消/并发确认不能被迟到Driver覆盖。
            if clock()>=current['expires']:
                conn.execute("UPDATE source_reauthentication_requests SET state='expired' WHERE request_id=?",(identity,))
                return 'expired'
            if not isinstance(result,dict) or result.get('status') not in ('valid','invalid','unknown','blocked'): raise ValueError('invalid_driver_result')
            if result['status']!='valid': return result['status']
            account=result.get('account_id')
            self._identity(owner,current['website'],account)
            if current['account'] is not None and account!=current['account']: raise ValueError('account_mismatch')
            if current['account'] is None:
                # 首次发现只准插入；并发出现的既有账号绝不能被未知账号流程覆盖。
                changed=conn.execute('INSERT OR IGNORE INTO authenticated_source_connections VALUES (?,?,?,1,?,1)',(owner,current['website'],account,self._encrypt_state(owner,current['website'],account,1,encoded))).rowcount if current['expected_version']==0 else 0
                if not changed:
                    conn.execute("UPDATE source_reauthentication_requests SET state='account_conflict' WHERE request_id=?",(identity,))
                    return 'account_conflict'
                conn.execute('UPDATE source_reauthentication_requests SET account=? WHERE request_id=?',(account,identity))
            else:
                changed=conn.execute('UPDATE authenticated_source_connections SET active_version=active_version+1,usable=1,ciphertext=? WHERE owner=? AND website=? AND account=? AND active_version=?',(self._encrypt_state(owner,current['website'],account,current['expected_version']+1,encoded),owner,current['website'],account,current['expected_version'])).rowcount
            outcome='verified' if changed==1 else 'stale'
            conn.execute('UPDATE source_reauthentication_requests SET state=? WHERE request_id=?',(outcome,identity))
            return outcome

    def claim_resume(self,owner,identity):
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=self._request(conn,owner,identity)
            if row['state']!='verified' or not json.loads(row['binding_json']): return None
            connection=conn.execute('SELECT active_version,usable FROM authenticated_source_connections WHERE owner=? AND website=? AND account=?',(owner,row['website'],row['account'])).fetchone()
            if not connection['usable'] or connection['active_version']!=row['expected_version']+1: return None
            conn.execute("UPDATE source_reauthentication_requests SET state='claimed' WHERE request_id=?",(identity,))
            return json.loads(row['binding_json'])
