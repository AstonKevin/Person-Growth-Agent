# -*- coding: utf-8 -*-
"""管理后台（Admin）接口测试：需要 MySQL（集成），LLM 走 mock。

跑：python -m pytest app/tests/test_admin.py -v -m integration
验证：
- 401 未登录拦截
- 403 普通用户越权拦截
- 200 管理员查列表（含 has_provider、绝不泄露敏感数据）
- 200 管理员更新角色与状态（停用即时生效）
- 409 管理员自锁保护（禁止停用自身或降低自身权限）
- 200 综合用量大屏统计（汇总数、错误率、按模型聚合、趋势）
"""
import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.provider import UserProvider
from app.models.usage import UsageLog
from app.core.security import hash_password
from app.core.crypto import encrypt
from app.crud.usage import add_usage

pytestmark = pytest.mark.integration
client = TestClient(app)


@pytest.fixture
def users_fixture():
    """创建临时普通用户与管理员用户，跑完清理。"""
    ts = int(time.time() * 1000)
    u_norm_name = f"norm_{ts}"
    u_admin_name = f"admin_{ts}"

    db = SessionLocal()
    try:
        norm = User(
            name=u_norm_name,
            password_hash=hash_password("123456"),
            role="user",
            is_active=True,
        )
        admin = User(
            name=u_admin_name,
            password_hash=hash_password("123456"),
            role="admin",
            is_active=True,
        )
        db.add_all([norm, admin])
        db.commit()
        db.refresh(norm)
        db.refresh(admin)

        # 给普通用户配一条 provider，用于检验 has_provider 字段
        prov = UserProvider(
            user_id=norm.id,
            provider="openai",
            base_url="https://api.test.com",
            encrypted_api_key=encrypt("sk-test"),
            model="test-model",
            verified=True,
        )
        db.add(prov)

        # 写入两条 usage 记录以供 stats 聚合测试
        add_usage(
            db,
            user_id=norm.id,
            model="test-model",
            status="success",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=200,
        )
        add_usage(
            db,
            user_id=norm.id,
            model="test-model",
            status="error",
            latency_ms=50,
            error_code="TestError",
        )
        db.commit()

        norm_id = norm.id
        admin_id = admin.id
    finally:
        db.close()

    # 获取 token
    r_norm = client.post(
        "/auth/login", data={"username": u_norm_name, "password": "123456"}
    )
    norm_token = r_norm.json()["access_token"]
    norm_auth = {"Authorization": f"Bearer {norm_token}"}

    r_admin = client.post(
        "/auth/login", data={"username": u_admin_name, "password": "123456"}
    )
    admin_token = r_admin.json()["access_token"]
    admin_auth = {"Authorization": f"Bearer {admin_token}"}

    try:
        yield {
            "norm_id": norm_id,
            "norm_auth": norm_auth,
            "norm_token": norm_token,
            "admin_id": admin_id,
            "admin_auth": admin_auth,
            "admin_name": u_admin_name,
        }
    finally:
        db = SessionLocal()
        try:
            db.query(UsageLog).filter(UsageLog.user_id.in_([norm_id, admin_id])).delete(
                synchronize_session=False
            )
            db.query(UserProvider).filter(
                UserProvider.user_id.in_([norm_id, admin_id])
            ).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_([norm_id, admin_id])).delete(
                synchronize_session=False
            )
            db.commit()
        finally:
            db.close()


def test_requires_login():
    """未登录访问任何 /admin/* 接口均返回 401。"""
    assert client.get("/admin/users").status_code == 401
    assert client.get("/admin/stats").status_code == 401
    assert client.patch("/admin/users/1", json={"role": "admin"}).status_code == 401


def test_forbidden_for_regular_user(users_fixture):
    """普通用户（role='user'）访问管理后台接口返回 403 Forbidden。"""
    auth = users_fixture["norm_auth"]
    uid = users_fixture["norm_id"]

    r = client.get("/admin/users", headers=auth)
    assert r.status_code == 403
    assert r.json()["error_code"] == "FORBIDDEN"

    r = client.get("/admin/stats", headers=auth)
    assert r.status_code == 403
    assert r.json()["error_code"] == "FORBIDDEN"

    r = client.patch(f"/admin/users/{uid}", headers=auth, json={"role": "admin"})
    assert r.status_code == 403
    assert r.json()["error_code"] == "FORBIDDEN"


def test_admin_list_users(users_fixture):
    """管理员成功获取用户列表，带 has_provider 字段，绝不泄露敏感数据。"""
    auth = users_fixture["admin_auth"]
    r = client.get("/admin/users", headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)

    norm_in_list = next((u for u in data if u["id"] == users_fixture["norm_id"]), None)
    admin_in_list = next((u for u in data if u["id"] == users_fixture["admin_id"]), None)

    assert norm_in_list is not None
    assert norm_in_list["has_provider"] is True
    assert norm_in_list["role"] == "user"
    assert norm_in_list["is_active"] is True
    assert "password_hash" not in norm_in_list
    assert "encrypted_api_key" not in norm_in_list
    assert "api_key" not in norm_in_list

    assert admin_in_list is not None
    assert admin_in_list["has_provider"] is False
    assert admin_in_list["role"] == "admin"


def test_admin_patch_user_and_deactivate_instant_effect(users_fixture):
    """管理员修改普通用户状态，停用后该用户 token 立即失效 (401)。"""
    admin_auth = users_fixture["admin_auth"]
    norm_auth = users_fixture["norm_auth"]
    norm_id = users_fixture["norm_id"]

    # 1. 普通用户当前可访问 /users/me
    assert client.get("/users/me", headers=norm_auth).status_code == 200

    # 2. 管理员停用该用户
    r = client.patch(
        f"/admin/users/{norm_id}", headers=admin_auth, json={"is_active": False}
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # 3. 停用后，该用户的旧 token 请求立即被拒 (401)
    r_deact = client.get("/users/me", headers=norm_auth)
    assert r_deact.status_code == 401
    assert "停用" in r_deact.json()["message"]

    # 4. 管理员重新启用该用户并提升为 admin
    r_re = client.patch(
        f"/admin/users/{norm_id}",
        headers=admin_auth,
        json={"is_active": True, "role": "admin"},
    )
    assert r_re.status_code == 200
    assert r_re.json()["is_active"] is True
    assert r_re.json()["role"] == "admin"

    # 5. 重新生效
    assert client.get("/users/me", headers=norm_auth).status_code == 200


def test_admin_self_lock_prevention(users_fixture):
    """管理员禁止停用自身或降低自身权限（409 Conflict）。"""
    admin_auth = users_fixture["admin_auth"]
    admin_id = users_fixture["admin_id"]

    # 尝试停用自己
    r1 = client.patch(
        f"/admin/users/{admin_id}", headers=admin_auth, json={"is_active": False}
    )
    assert r1.status_code == 409
    assert r1.json()["error_code"] == "CONFLICT"

    # 尝试降级自己为普通用户
    r2 = client.patch(
        f"/admin/users/{admin_id}", headers=admin_auth, json={"role": "user"}
    )
    assert r2.status_code == 409
    assert r2.json()["error_code"] == "CONFLICT"


def test_admin_stats(users_fixture):
    """管理员访问 /admin/stats，指标齐全且统计聚合数字准确。"""
    admin_auth = users_fixture["admin_auth"]
    r = client.get("/admin/stats", headers=admin_auth)
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["total_users"] >= 2
    assert data["active_users"] >= 2
    assert data["total_requests"] >= 2
    assert data["success_requests"] >= 1
    assert data["error_requests"] >= 1
    assert 0.0 <= data["error_rate"] <= 1.0
    assert data["prompt_tokens"] >= 100
    assert data["completion_tokens"] >= 50

    # 模型分布
    models = {m["model"]: m for m in data["by_model"]}
    assert "test-model" in models
    assert models["test-model"]["count"] >= 2

    # 近期趋势
    assert isinstance(data["recent_trend"], list)
