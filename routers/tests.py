
# Requirements:
#     pip install pytest pytest-asyncio httpx aiosqlite

# pytest.ini:
#     [pytest]
#     asyncio_mode = auto

# Run:
#     pytest tests.py -v
#     pytest tests.py -v --tb=short
#     pytest tests.py -v -k "Auth"        # только auth
#     pytest tests.py -v -k "AgentAPI"    # только HTTP-эндпоинты агента
#     pytest tests.py -v -k "Tools"       # только юнит-тесты тулов
#     pytest tests.py -v -k "E2E"         # только полный e2e


import os

os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("API_URL", "http://test.local/v1")
os.environ.setdefault("MODEL", "test-model")
os.environ.setdefault("USE_SMTP", "False")  # регистрация сразу verified, дебаг

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.responses import Response as SResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import auth as auth_module
import agent as agent_module
from auth import router as auth_router
from agent import router as agent_router
from database import async_db_manager
from models import Base, UserBase, MessageBase, AgentStateBase

from agent import (
    AgentState,
    _is_explicit_confirmation,
    _is_explicit_rejection,
    _trim_history_inplace,
    _validate_and_normalize,
    commit_booking,
    find_others,
    get_hotel_details,
    prepare_booking,
    reset_state,
    run_turn,
    save_shortlist,
    search_hotels,
    update_preferences,
)

pytestmark = pytest.mark.asyncio


class FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = MagicMock()
        self.function.name = name
        self.function.arguments = arguments


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none=False, **kw):
        d = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        if exclude_none:
            d = {k: v for k, v in d.items() if v is not None}
        return d


def _make_completion(content=None, tool_calls=None, finish_reason="stop"):
    msg = FakeMessage(content=content, tool_calls=tool_calls)
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _patch_llm(*responses):
    return patch.object(
        agent_module.ai_client.chat.completions,
        "create",
        AsyncMock(side_effect=list(responses)),
    )


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_maker(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def patch_db(monkeypatch, session_maker):
    monkeypatch.setattr(async_db_manager, "AsyncSessionLocal", session_maker)
    yield


@pytest_asyncio.fixture
async def client(monkeypatch, patch_db):

    def fake_template_response(self, *args, **kwargs):
        name = kwargs.get("name") or (args[1] if len(args) > 1 else "unknown")
        status = kwargs.get("status_code", 200)
        return SResponse(
            content=f"[template:{name}]",
            status_code=status,
            media_type="text/html",
        )

    monkeypatch.setattr(Jinja2Templates, "TemplateResponse", fake_template_response)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(agent_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_user(session_maker):
    user = UserBase(
        username="alice",
        email="alice@example.com",
        password="hashed",
        token="token-alice",
        status="verified",
        created_at=datetime.now(),
    )
    async with session_maker() as s:
        s.add(user)
        await s.commit()
        await s.refresh(user)
        s.expunge(user)
    return user


@pytest_asyncio.fixture
async def auth_cookie(auth_user):
    return {"auth_token": auth_user.token}


async def _seed_agent_state(session_maker, user_id, **overrides):
    defaults = dict(
        user_id=user_id,
        phase="collect",
        destination="Сочи",
        check_in="2030-06-01",
        check_out="2030-06-05",
        max_price=10000,
        currency="RUB",
        children_ages=[],
        rooms_amount=1,
    )
    defaults.update(overrides)
    async with session_maker() as s:
        s.add(AgentStateBase(**defaults))
        await s.commit()


class TestAuthPages:
    async def test_register_page(self, client):
        r = await client.get("/register")
        assert r.status_code == 200
        assert "register.html" in r.text

    async def test_login_page(self, client):
        r = await client.get("/login")
        assert r.status_code == 200
        assert "login.html" in r.text


class TestRegister:
    async def test_register_success_sets_cookie_and_persists(
        self, client, session_maker
    ):
        r = await client.post(
            "/register",
            data={
                "username": "newuser",
                "email": "new@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert "auth_token" in r.cookies

        from sqlalchemy import select
        async with session_maker() as s:
            res = await s.execute(select(UserBase).where(UserBase.username == "newuser"))
            u = res.scalar_one_or_none()
            assert u is not None
            assert u.email == "new@example.com"
            assert u.status == "verified"  # USE_SMTP=False
            assert u.token  

    async def test_register_duplicate_username(self, client, auth_user):
        r = await client.post(
            "/register",
            data={
                "username": "alice", 
                "email": "other@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 422
        assert "Пользователь с таким именем" in r.text

    async def test_register_duplicate_email(self, client, auth_user):
        r = await client.post(
            "/register",
            data={
                "username": "bob123",
                "email": "alice@example.com", 
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 422
        assert "Пользователь с таким email" in r.text

    async def test_register_both_duplicates(self, client, auth_user):
        r = await client.post(
            "/register",
            data={
                "username": "alice",
                "email": "alice@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 422
        assert "и именем пользователя и email" in r.text

    async def test_register_password_mismatch(self, client):
        r = await client.post(
            "/register",
            data={
                "username": "bob123",
                "email": "bob@example.com",
                "password": "secret123",
                "confirm_password": "different",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 422
        assert "Пароли не совпадают" in r.text

    async def test_register_short_password(self, client):
        r = await client.post(
            "/register",
            data={
                "username": "bob123",
                "email": "bob@example.com",
                "password": "1",
                "confirm_password": "1",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 422
        assert "Пароль должен содержать" in r.text

    async def test_register_invalid_email(self, client):
        r = await client.post(
            "/register",
            data={
                "username": "bob123",
                "email": "not-an-email",
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 422
        assert "Некорректный email" in r.text

    async def test_register_no_terms(self, client):
        r = await client.post(
            "/register",
            data={
                "username": "bob123",
                "email": "bob@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
        )
        assert r.status_code == 422
        assert "Необходимо согласие" in r.text


class TestLogin:
    async def _make_user(self, session_maker, *, status="verified",
                         password="secret123", username="bob",
                         email="bob@example.com"):
        from auth import hash_password
        async with session_maker() as s:
            u = UserBase(
                username=username,
                email=email,
                password=hash_password(password),
                token="initial-token",
                status=status,
                created_at=datetime.now(),
            )
            s.add(u)
            await s.commit()
            await s.refresh(u)
            s.expunge(u)
            return u

    async def test_login_success_by_username(self, client, session_maker):
        await self._make_user(session_maker)
        r = await client.post(
            "/login",
            data={"username_or_email": "bob", "password": "secret123"},
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert "auth_token" in r.cookies

    async def test_login_success_by_email(self, client, session_maker):
        await self._make_user(session_maker)
        r = await client.post(
            "/login",
            data={"username_or_email": "bob@example.com", "password": "secret123"},
        )
        assert r.status_code == 303
        assert "auth_token" in r.cookies

    async def test_login_wrong_password(self, client, session_maker):
        await self._make_user(session_maker)
        r = await client.post(
            "/login",
            data={"username_or_email": "bob", "password": "wrong-password"},
        )
        assert r.status_code == 422
        assert "Неверное имя пользователя/email или пароль" in r.text

    async def test_login_unknown_user(self, client):
        r = await client.post(
            "/login",
            data={"username_or_email": "ghost", "password": "whatever123"},
        )
        assert r.status_code == 422
        assert "Неверное имя" in r.text

    async def test_login_unverified(self, client, session_maker):
        await self._make_user(session_maker, status="unverified")
        r = await client.post(
            "/login",
            data={"username_or_email": "bob", "password": "secret123"},
        )
        assert r.status_code == 422
        assert "email_verify" in r.text

    async def test_login_too_short_password_validation(self, client):
        r = await client.post(
            "/login",
            data={"username_or_email": "x", "password": "1"},
        )
        assert r.status_code == 422
        assert (
            "Пароль должен содержать" in r.text
            or "Неверное" in r.text
        )


class TestLogout:
    async def test_logout_post(self, client):
        r = await client.post("/logout")
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_logout_get_clears_cookie(self, client):
        r = await client.get("/logout")
        assert r.status_code == 200
        sc = r.headers.get("set-cookie", "")
        assert "auth_token=" in sc


class TestVerifyEmail:
    async def _seed_unverified(self, session_maker, token, expires):
        async with session_maker() as s:
            s.add(UserBase(
                username="v",
                email="v@example.com",
                password="x",
                token="t",
                status="unverified",
                created_at=datetime.now(),
                verification_token=token,
                verification_token_expires=expires,
            ))
            await s.commit()

    async def test_verify_email_success(self, client, session_maker):
        await self._seed_unverified(
            session_maker, "valid-token", datetime.now() + timedelta(hours=1)
        )
        r = await client.get("/verify-email?token=valid-token")
        assert r.status_code == 200
        assert "verification_success.html" in r.text

        from sqlalchemy import select
        async with session_maker() as s:
            res = await s.execute(select(UserBase).where(UserBase.email == "v@example.com"))
            u = res.scalar_one()
            assert u.status == "verified"
            assert u.verification_token is None

    async def test_verify_email_invalid_token(self, client):
        r = await client.get("/verify-email?token=does-not-exist")
        assert r.status_code == 200
        assert "verification_failed.html" in r.text

    async def test_verify_email_expired_token(self, client, session_maker):
        await self._seed_unverified(
            session_maker, "old-token", datetime.now() - timedelta(minutes=1)
        )
        r = await client.get("/verify-email?token=old-token")
        assert r.status_code == 200
        assert "verification_failed.html" in r.text


class TestAgentAuthorization:
    async def test_chat_requires_auth(self, client):
        r = await client.get("/chat")
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_chat_page_authorized(self, client, auth_cookie):
        r = await client.get("/chat", cookies=auth_cookie)
        assert r.status_code == 200
        assert "agent.html" in r.text

    async def test_send_message_unauthorized(self, client):
        r = await client.post("/api/send-message", json={"message": "hi"})
        assert r.status_code == 401

    async def test_get_state_unauthorized(self, client):
        r = await client.get("/api/state")
        assert r.status_code == 401

    async def test_get_history_unauthorized(self, client):
        r = await client.get("/api/history")
        assert r.status_code == 401

    async def test_select_hotel_unauthorized(self, client):
        r = await client.post("/api/select-hotel", json={"hotel_id": "x"})
        assert r.status_code == 401

    async def test_booking_confirm_unauthorized(self, client):
        r = await client.post("/api/booking/confirm")
        assert r.status_code == 401

    async def test_booking_cancel_unauthorized(self, client):
        r = await client.post("/api/booking/cancel")
        assert r.status_code == 401

    async def test_find_others_unauthorized(self, client):
        r = await client.post("/api/find-others")
        assert r.status_code == 401

    async def test_reset_unauthorized(self, client):
        r = await client.post("/api/reset")
        assert r.status_code == 401


class TestAgentAPI:
    async def test_state_empty(self, client, auth_cookie):
        r = await client.get("/api/state", cookies=auth_cookie)
        assert r.status_code == 200
        data = r.json()
        assert data["phase"] == "collect"
        assert data["preferences"]["destination"] is None
        assert data["shortlist"] == []
        assert data["pending_booking"] is None
        assert data["confirmed_booking"] is None

    async def test_history_empty(self, client, auth_cookie):
        r = await client.get("/api/history", cookies=auth_cookie)
        assert r.status_code == 200
        assert r.json() == {"messages": []}

    async def test_send_message_simple_reply(self, client, auth_cookie):
        with _patch_llm(_make_completion(content="Куда хотите поехать?")):
            r = await client.post(
                "/api/send-message",
                json={"message": "привет"},
                cookies=auth_cookie,
            )
        assert r.status_code == 200
        data = r.json()
        assert "Куда" in data["message"]
        assert data["state"]["phase"] == "collect"

    async def test_send_message_persists_tool_result_in_state(
        self, client, auth_cookie
    ):
        tool = FakeToolCall(
            "c1", "update_preferences", '{"destination": "Сочи"}'
        )
        with _patch_llm(
            _make_completion(content=None, tool_calls=[tool], finish_reason="tool_calls"),
            _make_completion(content="Отлично, Сочи!"),
        ):
            r = await client.post(
                "/api/send-message",
                json={"message": "Хочу в Сочи"},
                cookies=auth_cookie,
            )
        assert r.status_code == 200
        data = r.json()
        assert "Сочи" in data["message"]
        assert data["state"]["preferences"]["destination"] == "Сочи"

        r2 = await client.get("/api/state", cookies=auth_cookie)
        assert r2.json()["preferences"]["destination"] == "Сочи"

    async def test_send_message_validation_empty(self, client, auth_cookie):
        r = await client.post(
            "/api/send-message", json={"message": ""}, cookies=auth_cookie
        )
        assert r.status_code == 422

    async def test_send_message_validation_too_long(self, client, auth_cookie):
        r = await client.post(
            "/api/send-message",
            json={"message": "x" * 5000},
            cookies=auth_cookie,
        )
        assert r.status_code == 422

    async def test_send_message_llm_failure_returns_friendly_error(
        self, client, auth_cookie
    ):
        with patch.object(
            agent_module.ai_client.chat.completions,
            "create",
            AsyncMock(side_effect=RuntimeError("API down")),
        ):
            r = await client.post(
                "/api/send-message",
                json={"message": "hi"},
                cookies=auth_cookie,
            )
        assert r.status_code == 200
        assert "Техническая ошибка" in r.json()["message"]

    async def test_send_message_timeout(self, client, auth_cookie):
        async def slow(*args, **kwargs):
            await asyncio.sleep(2)
            return _make_completion(content="late")

        with patch.object(
            agent_module.ai_client.chat.completions, "create", slow
        ), patch("agent.TURN_TIMEOUT_SEC", 0.05):
            r = await client.post(
                "/api/send-message",
                json={"message": "hi"},
                cookies=auth_cookie,
            )
        assert r.status_code == 504
        assert r.json()["error"] == "agent_timeout"

    async def test_send_message_auto_commits_on_explicit_yes(
        self, client, auth_cookie, auth_user, session_maker
    ):
        await _seed_agent_state(
            session_maker,
            auth_user.id,
            phase="book",
            shortlist=[{"id": "sochi-001"}],
            pending_booking={
                "hotel_id": "sochi-001",
                "destination": "Сочи",
                "check_in": "2030-06-01",
                "check_out": "2030-06-05",
                "rooms": 1,
                "children_ages": [],
                "currency": "RUB",
                "total_price": 34000.0,
                "confirmed_by_user": False,
            },
        )
        with _patch_llm(_make_completion(content="Готово, бронирую!")):
            r = await client.post(
                "/api/send-message", json={"message": "да"}, cookies=auth_cookie
            )
        assert r.status_code == 200
        data = r.json()
        assert data["state"]["confirmed_booking"] is not None
        assert data["state"]["confirmed_booking"]["booking_reference"].startswith("BK-")
        assert data["state"]["pending_booking"] is None

    async def test_select_hotel_not_in_shortlist(self, client, auth_cookie):
        r = await client.post(
            "/api/select-hotel",
            json={"hotel_id": "nonexistent-id"},
            cookies=auth_cookie,
        )
        assert r.status_code == 200
        assert r.json()["result"]["error"] == "hotel_not_in_shortlist"

    async def test_select_hotel_success(
        self, client, auth_cookie, auth_user, session_maker
    ):
        await _seed_agent_state(
            session_maker, auth_user.id,
            phase="book", shortlist=[{"id": "sochi-001"}],
        )
        r = await client.post(
            "/api/select-hotel",
            json={"hotel_id": "sochi-001"},
            cookies=auth_cookie,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["result"]["need_confirmation"] is True
        assert data["state"]["pending_booking"]["hotel_id"] == "sochi-001"
        assert data["state"]["pending_booking"]["confirmed_by_user"] is False

    async def test_confirm_booking_without_pending(
        self, client, auth_cookie
    ):
        r = await client.post("/api/booking/confirm", cookies=auth_cookie)
        assert r.status_code == 400
        assert r.json()["error"] == "no_pending_booking"

    async def test_cancel_booking(self, client, auth_cookie):
        r = await client.post("/api/booking/cancel", cookies=auth_cookie)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["state"]["pending_booking"] is None

    async def test_find_others_no_destination(self, client, auth_cookie):
        r = await client.post("/api/find-others", cookies=auth_cookie)
        assert r.status_code == 400
        assert r.json()["error"] == "missing_destination"

    async def test_find_others_excludes_previous(
        self, client, auth_cookie, auth_user, session_maker
    ):
        await _seed_agent_state(
            session_maker, auth_user.id,
            phase="book",
            shortlist=[{"id": "sochi-001"}, {"id": "sochi-002"}],
        )
        r = await client.post("/api/find-others", cookies=auth_cookie)
        assert r.status_code == 200
        data = r.json()
        ids = [h["id"] for h in data["state"]["shortlist"]]
        assert "sochi-001" not in ids
        assert "sochi-002" not in ids
        assert len(ids) > 0

    async def test_reset_clears_state(
        self, client, auth_cookie, auth_user, session_maker
    ):
        await _seed_agent_state(
            session_maker, auth_user.id, phase="search",
            shortlist=[{"id": "sochi-001"}],
        )
        r = await client.post("/api/reset", cookies=auth_cookie)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        st = r.json()["state"]
        assert st["phase"] == "collect"
        assert st["preferences"]["destination"] is None
        assert st["shortlist"] == []


class TestValidation:
    def test_valid_date(self):
        cleaned, errs = _validate_and_normalize({"check_in": "2030-01-01"})
        assert cleaned == {"check_in": "2030-01-01"}
        assert errs == []

    def test_past_date(self):
        cleaned, errs = _validate_and_normalize({"check_in": "2000-01-01"})
        assert "check_in" not in cleaned
        assert any("past" in e for e in errs)

    def test_malformed_date(self):
        cleaned, errs = _validate_and_normalize({"check_out": "01/01/2030"})
        assert "check_out" not in cleaned
        assert any("invalid date" in e for e in errs)

    def test_unsupported_currency(self):
        cleaned, errs = _validate_and_normalize({"currency": "XYZ"})
        assert "currency" not in cleaned
        assert any("unsupported currency" in e for e in errs)

    def test_currency_normalized(self):
        cleaned, _ = _validate_and_normalize({"currency": "rub"})
        assert cleaned["currency"] == "RUB"

    def test_negative_price(self):
        _, errs = _validate_and_normalize({"max_price": -5})
        assert any("max_price" in e for e in errs)

    def test_children_ages_out_of_range(self):
        _, errs = _validate_and_normalize({"children_ages": [5, 99]})
        assert any("children_ages" in e for e in errs)

    def test_rooms_zero(self):
        _, errs = _validate_and_normalize({"rooms_amount": 0})
        assert any("rooms_amount" in e for e in errs)


class TestUpdatePreferences:
    def test_saves_and_transitions_phase(self):
        st = AgentState()
        update_preferences(
            st,
            destination="Сочи",
            check_in="2030-06-01",
            check_out="2030-06-05",
            max_price=10000,
            currency="RUB",
            rooms_amount=1,
        )
        assert st.destination == "Сочи"
        assert st.missing() == []
        assert st.phase == "search"

    def test_partial_keeps_phase_collect(self):
        st = AgentState()
        res = update_preferences(st, destination="Сочи")
        assert st.phase == "collect"
        assert "check_in" in res["missing"]

    def test_invalid_dates_return_error(self):
        st = AgentState()
        res = update_preferences(
            st,
            check_in="2030-06-05",
            check_out="2030-06-01",
        )
        assert any("check_out" in e for e in res["errors"])


class TestSearchAndDetails:
    def test_search_returns_list(self):
        st = AgentState(destination="Сочи", currency="RUB")
        res = search_hotels(st)
        assert "candidates" in res
        assert res["count"] > 0
        assert st.last_search_ids

    def test_search_missing_destination(self):
        st = AgentState()
        res = search_hotels(st)
        assert res["error"] == "missing_destination"

    def test_details_returns_hotel(self):
        st = AgentState(
            destination="Сочи", currency="RUB",
            max_price=10000, rooms_amount=1,
            check_in="2030-06-01", check_out="2030-06-05",
        )
        res = get_hotel_details(st, "sochi-001")
        assert "hotel" in res
        assert res["matches"] is True

    def test_details_not_found(self):
        st = AgentState()
        res = get_hotel_details(st, "no-such-id")
        assert res["error"] == "hotel_not_found"


class TestSaveShortlist:
    def test_rejects_unknown_ids(self):
        st = AgentState(last_search_ids={"sochi-001"})
        res = save_shortlist(st, ["ghost-id"])
        assert res["error"] == "unknown_hotel_ids"

    def test_accepts_known_ids(self):
        st = AgentState(last_search_ids={"sochi-001", "sochi-002"})
        res = save_shortlist(st, ["sochi-001"])
        assert res["phase"] == "book"
        assert st.shortlist == [{"id": "sochi-001"}]


class TestFindOthers:
    def test_without_shortlist(self):
        st = AgentState()
        res = find_others(st)
        assert res["error"] == "no_shortlist_to_exclude"

    def test_with_shortlist(self):
        st = AgentState(shortlist=[{"id": "sochi-001"}], phase="book")
        find_others(st)
        assert "sochi-001" in st.excluded_ids
        assert st.phase == "search"
        assert st.shortlist == []


class TestPrepareBooking:
    def _ready_state(self, **kw):
        st = AgentState(
            destination="Сочи",
            check_in="2030-06-01",
            check_out="2030-06-05",
            max_price=10000,
            currency="RUB",
            rooms_amount=1,
            shortlist=[{"id": "sochi-001"}],
            phase="book",
        )
        for k, v in kw.items():
            setattr(st, k, v)
        return st

    def test_not_in_shortlist(self):
        st = self._ready_state()
        res = prepare_booking(st, "sochi-002")
        assert res["error"] == "hotel_not_in_shortlist"

    def test_success_sets_pending(self):
        st = self._ready_state()
        res = prepare_booking(st, "sochi-001")
        assert res["need_confirmation"] is True
        assert st.pending_booking is not None
        assert st.pending_booking["confirmed_by_user"] is False


class TestCommitBooking:
    def _ready_state_with_pending(self):
        st = AgentState(
            destination="Сочи",
            check_in="2030-06-01",
            check_out="2030-06-05",
            max_price=10000,
            currency="RUB",
            rooms_amount=1,
            shortlist=[{"id": "sochi-001"}],
            phase="book",
        )
        prepare_booking(st, "sochi-001")
        return st

    def test_without_pending(self):
        res = commit_booking(AgentState())
        assert res["error"] == "no_pending_booking"

    def test_requires_confirmation(self):
        st = self._ready_state_with_pending()
        res = commit_booking(st)
        assert res["error"] == "confirmation_required"

    def test_success_after_confirmation(self):
        st = self._ready_state_with_pending()
        st.pending_booking["confirmed_by_user"] = True
        res = commit_booking(st)
        assert res["status"] == "confirmed"
        assert st.confirmed_booking["booking_reference"].startswith("BK-")
        assert st.phase == "done"
        assert st.pending_booking is None


class TestResetState:
    def test_reset_clears_everything(self):
        st = AgentState(
            destination="Сочи", check_in="2030-06-01", check_out="2030-06-05",
            max_price=10000, currency="RUB", rooms_amount=1,
            shortlist=[{"id": "sochi-001"}],
            excluded_ids={"sochi-002"},
            phase="book",
        )
        res = reset_state(st)
        assert res["phase"] == "collect"
        assert st.destination is None
        assert st.shortlist == []
        assert st.excluded_ids == set()


class TestConfirmationRegex:
    @pytest.mark.parametrize("msg", [
        "да", "Да", "ДА!", "yes", "Yes.", "confirm", "confirm it",
        "book it", "go ahead", "ок", "окей", "бронируй", "забронируйте",
        "подтверждаю", "подтверждаю бронь", "согласен", "согласна",
    ])
    def test_positive(self, msg):
        assert _is_explicit_confirmation(msg)

    @pytest.mark.parametrize("msg", [
        "нет", "no", "cancel", "stop", "не надо", "отмена", "отмени",
        "привет", "да нет наверное", "я хочу забронировать", "",
    ])
    def test_negative(self, msg):
        assert not _is_explicit_confirmation(msg)

    @pytest.mark.parametrize("msg", [
        "нет", "no", "cancel", "stop", "не надо", "отмена", "отмени",
        "не бронируй", "не бронируйте",
    ])
    def test_reject_positive(self, msg):
        assert _is_explicit_rejection(msg)

    @pytest.mark.parametrize("msg", ["да", "yes", "confirm", "привет"])
    def test_reject_negative(self, msg):
        assert not _is_explicit_rejection(msg)


class TestTrimHistory:
    def test_short_history_untouched(self):
        h = [{"role": "user", "content": "x"}] * 5
        _trim_history_inplace(h, max_items=10)
        assert len(h) == 5

    def test_trims_to_user_boundary(self):
        h = [{"role": "user", "content": str(i)} for i in range(100)]
        _trim_history_inplace(h, max_items=60)
        assert len(h) <= 60
        assert h[0]["role"] == "user"


class TestE2EWorkflow:
    # Сквозной сценарий: регистрация - /api/find-others - /api/select-hotel - /api/booking/confirm

    async def test_register_search_booking_flow(
        self, client, session_maker
    ):
        r = await client.post(
            "/register",
            data={
                "username": "flowuser",
                "email": "flow@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
                "agree_terms": "true",
            },
        )
        assert r.status_code == 303
        cookies = r.cookies
        assert "auth_token" in cookies

        from sqlalchemy import select
        async with session_maker() as s:
            res = await s.execute(select(UserBase).where(UserBase.username == "flowuser"))
            user = res.scalar_one()
            await _seed_agent_state(
                session_maker, user.id, phase="collect"
            )

        r = await client.post("/api/find-others", cookies=cookies)
        assert r.status_code == 200
        shortlist = r.json()["state"]["shortlist"]
        assert len(shortlist) > 0
        hotel_id = shortlist[0]["id"]

        r = await client.post(
            "/api/select-hotel",
            json={"hotel_id": hotel_id},
            cookies=cookies,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["result"]["need_confirmation"] is True
        assert data["state"]["pending_booking"]["hotel_id"] == hotel_id

        r = await client.post("/api/booking/confirm", cookies=cookies)
        assert r.status_code == 200
        data = r.json()
        assert data["result"]["status"] == "confirmed"
        cb = data["state"]["confirmed_booking"]
        assert cb is not None
        assert cb["booking_reference"].startswith("BK-")
        assert cb["hotel_id"] == hotel_id
        assert data["state"]["pending_booking"] is None
        assert data["state"]["phase"] == "done"

        r = await client.get("/api/state", cookies=cookies)
        assert r.json()["confirmed_booking"]["hotel_id"] == hotel_id