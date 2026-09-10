"""仅候选Owner读取入口；不提供认证或恢复的假成功动作。"""
import math
import secrets
import threading
import time
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from src.api.auth import get_current_user


class EmptyAction(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ChallengeImages:
    def __init__(self, repository, *, clock=time.time):
        self.repository = repository
        self.clock = clock
        self._records = {}
        self._lock = threading.Lock()

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('invalid_clock')
        return value

    def discard(self, owner, request_id):
        with self._lock:
            self._records.pop((owner, request_id), None)

    def _prune(self):
        now = self._now()
        for key, record in list(self._records.items()):
            try:
                request = self.repository.get_request(*key)
                expired = request['state'] != 'pending' or now >= min(record[1], request['expires'])
            except PermissionError:
                expired = True
            if expired:
                self._records.pop(key, None)

    def _pending(self, owner, request_id):
        request = self.repository.get_request(owner, request_id)
        if request['state'] != 'pending' or self._now() >= request['expires']:
            raise PermissionError('challenge_unavailable')
        return request

    def publish(self, owner, request_id, content, media_type, *, ttl=60):
        """仅服务端受控Driver调用；没有HTTP写入口。"""
        signatures = {'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff', 'image/webp': b'RIFF'}
        if type(content) is not bytes or not 0 < len(content) <= 1048576:
            raise ValueError('invalid_image')
        if media_type not in signatures or not content.startswith(signatures[media_type]):
            raise ValueError('invalid_image')
        if media_type == 'image/webp' and content[8:12] != b'WEBP':
            raise ValueError('invalid_image')
        if type(ttl) not in (int, float) or not math.isfinite(ttl) or not 0 < ttl <= 120:
            raise ValueError('invalid_expiry')
        with self._lock:
            self._prune()
            request = self._pending(owner, request_id)
            if (owner, request_id) not in self._records and len(self._records) >= 32:
                raise ValueError('challenge_capacity')
            generation = secrets.token_urlsafe(24)
            expires = min(self._now() + ttl, request['expires'])
            self._records[(owner, request_id)] = (generation, expires, content, media_type)
            return {'generation': generation, 'expires': expires}

    def read(self, owner, request_id, generation):
        with self._lock:
            self._prune()
            self._pending(owner, request_id)
            record = self._records.get((owner, request_id))
            if record is None or record[0] != generation or self._now() >= record[1]:
                raise PermissionError('challenge_unavailable')
            return record[2], record[3]


def router_for(repository, images):
    router = APIRouter(prefix='/api/authenticated-sources')

    @router.get('/websites')
    def websites(user=Depends(get_current_user)):
        return {'items': [{'website': site, 'status': 'unavailable', 'methods': []} for site in ('xiaohongshu', 'bilibili')]}

    @router.get('/requests/{request_id}')
    def get_request(request_id: str, user=Depends(get_current_user)):
        try:
            return repository.get_request(user['user_id'], request_id)
        except PermissionError:
            raise HTTPException(404, '待办不存在') from None

    @router.post('/requests/{request_id}/cancel')
    def cancel(request_id: str, body: EmptyAction | None = None, user=Depends(get_current_user)):
        try:
            state = repository.cancel(user['user_id'], request_id)
            if state == 'cancelled':
                images.discard(user['user_id'], request_id)
            return {'state': state}
        except PermissionError:
            raise HTTPException(404, '待办不存在') from None

    @router.get('/requests/{request_id}/challenge/{generation}')
    def challenge(request_id: str, generation: str, user=Depends(get_current_user)):
        try:
            content, media_type = images.read(user['user_id'], request_id, generation)
        except (PermissionError, ValueError):
            raise HTTPException(404, '挑战图像不可用', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}) from None
        return Response(content, media_type=media_type, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return router
