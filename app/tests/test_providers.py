# -*- coding: utf-8 -*-
"""Provider（BYOK）HTTP 层测试：需要 MySQL（集成），网络用 mock，不真实调 LLM。

跑：python -m pytest app/tests/test_providers.py -v -m integration
验证：任何接口不回显 key；未配置 404；测试接口能拦下错误配置。
"""
import time

import pytest
import openai
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.provider import UserProvider

pytestmark = pytest.mark.integration
client = TestClient(app)


# ---- 假 OpenAI 客户端：只模拟 client.chat.completions.create ----
class _Create:
    def __init__(self, raises=None):
        self._raises = raises

    def create(self, **kwargs):
        if self._raises:
            raise self._raises
        return type("R", (), {"id": "ok"})()


class _Chat:
    def __init__(self, raises=None):
        self.completions = _Create(raises)


class _FakeOpenAI:
    def __init__(self, raises=None, **kwargs):
        self.chat = _Chat(raises)


@pytest.fixture
def auth():
    """注册临时用户、登录，返回 (鉴权头, 用户名)，跑完清理。"""
    uname = f"byok_t_{int(time.time())}"
    r = client.post("/auth/register", json={"name": uname, "password": "123456"})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", data={"username": uname, "password": "123456"})
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    try:
        yield h, uname
    finally:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.name == uname).first()
            if u:
                db.query(UserProvider).filter(UserProvider.user_id == u.id).delete()
                db.query(User).filter(User.id == u.id).delete()
                db.commit()
        finally:
            db.close()


def test_requires_login():
    """未登录访问 /providers/me -> 401。"""
    assert client.get("/providers/me").status_code == 401


def test_not_configured_404(auth):
    h, _ = auth
    assert client.get("/providers/me", headers=h).status_code == 404


def test_put_then_get_has_no_key(auth):
    h, _ = auth
    payload = {"base_url": "https://api.example.com", "api_key": "sk-secret", "model": "m1"}
    r = client.put("/providers/me", headers=h, json=payload)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "api_key" not in out and "encrypted_api_key" not in out
    assert out["base_url"] == "https://api.example.com" and out["model"] == "m1"

    # 再 GET 也无 key
    r = client.get("/providers/me", headers=h)
    assert r.status_code == 200 and "api_key" not in r.json()

    # 库里存的是密文，不是明文
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.name == auth[1]).first()
        row = db.query(UserProvider).filter(UserProvider.user_id == u.id).first()
        assert row.encrypted_api_key != "sk-secret"
        assert row.encrypted_api_key.startswith("gAAAAA")
    finally:
        db.close()


def test_test_success(auth, monkeypatch):
    h, _ = auth
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _FakeOpenAI())
    r = client.post("/providers/me/test", headers=h,
                    json={"base_url": "https://api.example.com", "api_key": "sk", "model": "m1"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_test_failure(auth, monkeypatch):
    h, _ = auth
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _FakeOpenAI(raises=RuntimeError("boom")))
    r = client.post("/providers/me/test", headers=h,
                    json={"base_url": "https://api.example.com", "api_key": "sk", "model": "m1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["error"]


def test_delete(auth):
    h, _ = auth
    payload = {"base_url": "https://api.example.com", "api_key": "sk", "model": "m1"}
    client.put("/providers/me", headers=h, json=payload)
    assert client.delete("/providers/me", headers=h).status_code == 204
    assert client.get("/providers/me", headers=h).status_code == 404
