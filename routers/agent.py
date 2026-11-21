from fastapi import APIRouter, Request, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

import os
import re
import json
import secrets
import asyncio
import logging
from datetime import datetime, timezone, date
from dataclasses import dataclass, field, asdict
from typing import Callable

from openai import AsyncOpenAI

from models import UserBase, MessageBase, AgentStateBase
from database import async_db_manager

logger = logging.getLogger(__name__)

current_directory = os.path.dirname(os.path.abspath(__file__))
templates_directory = os.path.join(current_directory, "..", "templates")

router = APIRouter()
templates = Jinja2Templates(directory=templates_directory)


ai_client = AsyncOpenAI(
    api_key=os.environ.get("API_KEY"),
    base_url=os.environ.get("API_URL"),
)
MODEL = os.environ.get("MODEL")

TURN_TIMEOUT_SEC = 60
MAX_LOOP_STEPS = 10
MAX_HISTORY_ITEMS = 60
MAX_MESSAGE_LENGTH = 4000
SHORTLIST_SIZE = 5
SUPPORTED_CURRENCIES = {"USD", "EUR", "RUB", "GBP"}


_LOCKS: dict[int, asyncio.Lock] = {}


def _get_lock(user_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[user_id] = lock
    return lock


async def get_current_user(request: Request):
    token = request.cookies.get("auth_token")
    if not token:
        return None
    async with async_db_manager.AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserBase).where(UserBase.token == token)
        )
        user = result.scalar_one_or_none()
        if user:
            session.expunge(user)  
        return user


@dataclass
class AgentState:
    phase: str = "collect"          

    destination: str | None = None
    check_in: str | None = None
    check_out: str | None = None
    max_price: float | None = None
    currency: str | None = None
    children_ages: list[int] = field(default_factory=list)
    rooms_amount: int | None = None

    shortlist: list[dict] = field(default_factory=list)
    excluded_ids: set[str] = field(default_factory=set)
    last_search_ids: set[str] = field(default_factory=set)

    pending_booking: dict | None = None
    confirmed_booking: dict | None = None

    REQUIRED = ("destination", "check_in", "check_out",
                "max_price", "currency", "rooms_amount")

    def missing(self) -> list[str]:
        return [f for f in self.REQUIRED if getattr(self, f) in (None, "")]

    def snapshot(self) -> dict:
        d = asdict(self)
        d["excluded_ids"] = sorted(self.excluded_ids)
        d["last_search_ids"] = sorted(self.last_search_ids)
        return d


def _row_to_state(row) -> AgentState:
    return AgentState(
        phase=row.phase or "collect",
        destination=row.destination,
        check_in=row.check_in,
        check_out=row.check_out,
        max_price=row.max_price,
        currency=row.currency,
        children_ages=list(row.children_ages or []),
        rooms_amount=row.rooms_amount,
        shortlist=list(row.shortlist or []),
        excluded_ids=set(row.excluded_ids or []),
        last_search_ids=set(row.last_search_ids or []),
        pending_booking=dict(row.pending_booking) if row.pending_booking else None,
        confirmed_booking=dict(row.confirmed_booking) if row.confirmed_booking else None,
    )


def _apply_state_to_row(row: "AgentStateBase", state: AgentState) -> None:
    row.phase = state.phase
    row.destination = state.destination
    row.check_in = state.check_in
    row.check_out = state.check_out
    row.max_price = state.max_price
    row.currency = state.currency
    row.children_ages = list(state.children_ages)
    row.rooms_amount = state.rooms_amount
    row.shortlist = list(state.shortlist)
    row.excluded_ids = sorted(state.excluded_ids)
    row.last_search_ids = sorted(state.last_search_ids)
    row.pending_booking = dict(state.pending_booking) if state.pending_booking else None
    row.confirmed_booking = dict(state.confirmed_booking) if state.confirmed_booking else None


async def _load_state(user_id: int) -> AgentState:
    async with async_db_manager.AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentStateBase).where(AgentStateBase.user_id == user_id)
        )
        row = result.scalar_one_or_none()
    if not row:
        return AgentState()
    return _row_to_state(row)


async def _save_state(user_id: int, state: AgentState) -> None:
    async with async_db_manager.AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentStateBase).where(AgentStateBase.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AgentStateBase(user_id=user_id)
            session.add(row)
        _apply_state_to_row(row, state)
        await session.commit()


async def _persist_turn(user_id: int, state: AgentState, new_messages: list[dict]) -> None:
    """Атомарно: state + новые сообщения одной транзакцией."""
    async with async_db_manager.AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentStateBase).where(AgentStateBase.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AgentStateBase(user_id=user_id)
            session.add(row)
        _apply_state_to_row(row, state)

        for m in new_messages:
            role = m.get("role")
            if role == "user":
                session.add(MessageBase(
                    user_id=user_id,
                    content=m.get("content") or "",
                    content_type="text",
                    is_user=True,
                ))
            elif role == "assistant":
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    session.add(MessageBase(
                        user_id=user_id,
                        content=json.dumps(
                            {"content": m.get("content"), "tool_calls": tool_calls},
                            ensure_ascii=False,
                        ),
                        content_type="tool_calls",
                        is_user=False,
                    ))
                else:
                    session.add(MessageBase(
                        user_id=user_id,
                        content=m.get("content") or "",
                        content_type="text",
                        is_user=False,
                    ))
            elif role == "tool":
                session.add(MessageBase(
                    user_id=user_id,
                    content=json.dumps(
                        {"tool_call_id": m.get("tool_call_id"), "content": m.get("content")},
                        ensure_ascii=False,
                    ),
                    content_type="tool_result",
                    is_user=False,
                ))

        await session.commit()


async def load_history(user_id: int) -> list[dict]:
    """Загружает историю в формате OpenAI messages."""
    async with async_db_manager.AsyncSessionLocal() as session:
        result = await session.execute(
            select(MessageBase)
            .where(MessageBase.user_id == user_id)
            .order_by(MessageBase.timestamp, MessageBase.id)
        )
        rows = result.scalars().all()
    return [_row_to_history_item(row) for row in rows]


def _row_to_history_item(row) -> dict:
    if row.content_type == "tool_calls":
        try:
            data = json.loads(row.content)
        except json.JSONDecodeError:
            data = {"content": row.content, "tool_calls": []}
        item: dict = {"role": "assistant", "content": data.get("content")}
        if data.get("tool_calls"):
            item["tool_calls"] = data["tool_calls"]
        return item
    if row.content_type == "tool_result":
        try:
            data = json.loads(row.content)
        except json.JSONDecodeError:
            data = {"tool_call_id": "", "content": row.content}
        return {
            "role": "tool",
            "tool_call_id": data.get("tool_call_id", ""),
            "content": data.get("content", ""),
        }
    return {
        "role": "user" if row.is_user else "assistant",
        "content": row.content,
    }


async def delete_history(user_id: int) -> None:
    async with async_db_manager.AsyncSessionLocal() as session:
        await session.execute(
            delete(MessageBase).where(MessageBase.user_id == user_id)
        )
        await session.commit()


async def load_visible_history(user_id: int) -> list[dict]:
    """Только user/assistant text — для рендера на фронте."""
    async with async_db_manager.AsyncSessionLocal() as session:
        result = await session.execute(
            select(MessageBase)
            .where(MessageBase.user_id == user_id)
            .where(MessageBase.content_type == "text")
            .order_by(MessageBase.timestamp, MessageBase.id)
        )
        rows = result.scalars().all()
    return [
        {"role": "user" if r.is_user else "assistant", "content": r.content}
        for r in rows if r.content
    ]


def _validate_iso_date(value: str) -> str | None:
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return f"invalid date {value!r}, expected YYYY-MM-DD"
    if d < date.today():
        return f"date {value} is in the past"
    return None


def _validate_and_normalize(fields: dict) -> tuple[dict, list[str]]:
    cleaned: dict = {}
    errors: list[str] = []

    if fields.get("destination") is not None:
        dest = str(fields["destination"]).strip()
        if dest:
            cleaned["destination"] = dest
        else:
            errors.append("destination must be non-empty")

    for key in ("check_in", "check_out"):
        if fields.get(key) is not None:
            err = _validate_iso_date(fields[key])
            errors.append(err) if err else cleaned.update({key: fields[key]})

    if fields.get("max_price") is not None:
        try:
            p = float(fields["max_price"])
            errors.append("max_price must be > 0") if p <= 0 else cleaned.update({"max_price": p})
        except (TypeError, ValueError):
            errors.append("max_price must be a number")

    if fields.get("currency") is not None:
        cur = str(fields["currency"]).upper().strip()
        if cur not in SUPPORTED_CURRENCIES:
            errors.append(f"unsupported currency {cur!r}, use one of {sorted(SUPPORTED_CURRENCIES)}")
        else:
            cleaned["currency"] = cur

    if fields.get("rooms_amount") is not None:
        try:
            r = int(fields["rooms_amount"])
            errors.append("rooms_amount must be >= 1") if r < 1 else cleaned.update({"rooms_amount": r})
        except (TypeError, ValueError):
            errors.append("rooms_amount must be an integer")

    if fields.get("children_ages") is not None:
        try:
            ages = [int(a) for a in fields["children_ages"]]
            if any(a < 0 or a > 17 for a in ages):
                errors.append("children_ages must be in 0..17")
            else:
                cleaned["children_ages"] = ages
        except (TypeError, ValueError):
            errors.append("children_ages must be a list of integers")

    return cleaned, errors


def _public_pending(pending: dict | None) -> dict | None:
    if not pending:
        return None
    return {k: v for k, v in pending.items() if k != "confirmed_by_user"}


DEST_ALIASES = {
    "сочи": "Сочи", "sochi": "Сочи",
    "москва": "Москва", "moscow": "Москва",
    "санкт-петербург": "Санкт-Петербург", "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург", "saint petersburg": "Санкт-Петербург",
    "st. petersburg": "Санкт-Петербург",
}

MOCK_CATALOG: list[dict] = [
    {"id": "sochi-001", "destination": "Сочи", "name": "Radisson Blu Resort Sochi",
     "price": 8500.0, "currency": "RUB", "rooms_available": 12, "max_children": 3,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.7, "amenities": ["pool", "spa", "wifi"]},
    {"id": "sochi-002", "destination": "Сочи", "name": "Iberostar Sochi",
     "price": 6400.0, "currency": "RUB", "rooms_available": 8, "max_children": 2,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.4, "amenities": ["pool", "wifi"]},
    {"id": "sochi-003", "destination": "Сочи", "name": "Marins Park Hotel",
     "price": 4200.0, "currency": "RUB", "rooms_available": 20, "max_children": 4,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.1, "amenities": ["breakfast"]},
    {"id": "msk-001", "destination": "Москва", "name": "Ritz-Carlton Moscow",
     "price": 28000.0, "currency": "RUB", "rooms_available": 6, "max_children": 2,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.9, "amenities": ["spa", "restaurant"]},
    {"id": "msk-002", "destination": "Москва", "name": "Azimut City Hotel",
     "price": 5200.0, "currency": "RUB", "rooms_available": 40, "max_children": 3,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.2, "amenities": ["wifi", "breakfast"]},
    {"id": "spb-001", "destination": "Санкт-Петербург", "name": "Corinthia Hotel",
     "price": 12500.0, "currency": "RUB", "rooms_available": 15, "max_children": 3,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.8, "amenities": ["spa", "bar"]},
    {"id": "spb-002", "destination": "Санкт-Петербург", "name": "Hotel Astoria",
     "price": 9800.0, "currency": "RUB", "rooms_available": 10, "max_children": 2,
     "available_from": "2025-01-01", "available_to": "2030-12-31",
     "rating": 4.6, "amenities": ["restaurant", "wifi"]},
]

_GENERATED_HOTELS: dict[str, dict] = {}


def _generate_generic_hotel(dest: str, i: int, currency: str) -> dict:
    hid = f"gen-{dest[:3].lower()}-{i}-{currency.lower()}"
    hotel = {
        "id": hid,
        "destination": dest,
        "name": f"{dest} Grand Hotel #{i + 1}",
        "price": 3000.0 + i * 1000,
        "currency": currency,
        "rooms_available": max(1, 20 - i),
        "max_children": 4,
        "available_from": "2025-01-01",
        "available_to": "2030-12-31",
        "rating": round(4.0 + (i % 5) * 0.15, 1),
        "amenities": ["wifi", "breakfast"],
    }
    _GENERATED_HOTELS[hid] = hotel
    return hotel


def _normalize_dest(query: str) -> str:
    q = (query or "").strip().lower()
    return DEST_ALIASES.get(q, (query or "").strip())


def _api_search(dest: str, exclude: set[str], limit: int, currency: str = "RUB") -> list[dict]:
    normalized = _normalize_dest(dest)
    matches = [
        dict(h) for h in MOCK_CATALOG
        if h["destination"].lower() == normalized.lower()
        and h["id"] not in exclude
        and h["currency"] == currency
    ]
    i = 0
    while len(matches) < limit and i < 20:
        h = _generate_generic_hotel(normalized, i, currency)
        if h["id"] not in exclude:
            matches.append(dict(h))
        i += 1
    return matches[:limit]


def _api_details(hotel_id: str) -> dict | None:
    if hotel_id in _GENERATED_HOTELS:
        return dict(_GENERATED_HOTELS[hotel_id])
    for h in MOCK_CATALOG:
        if h["id"] == hotel_id:
            return dict(h)
    return None


def _matches(state: AgentState, details: dict | None) -> tuple[bool, list[str]]:
    if not details:
        return False, ["hotel not found"]
    reasons: list[str] = []

    if state.max_price is not None and details.get("price") is not None:
        if details.get("currency") != state.currency:
            reasons.append(
                f"currency mismatch: hotel={details.get('currency')}, user={state.currency}"
            )
        elif details["price"] > state.max_price:
            reasons.append(f"price {details['price']} > max {state.max_price}")

    if state.rooms_amount is not None and details.get("rooms_available") is not None:
        if details["rooms_available"] < state.rooms_amount:
            reasons.append(
                f"only {details['rooms_available']} rooms available, need {state.rooms_amount}"
            )

    if state.children_ages and details.get("max_children") is not None:
        if len(state.children_ages) > details["max_children"]:
            reasons.append(
                f"hotel allows max {details['max_children']} children, "
                f"user has {len(state.children_ages)}"
            )

    if state.check_in and state.check_out:
        try:
            ci = datetime.strptime(state.check_in, "%Y-%m-%d").date()
            co = datetime.strptime(state.check_out, "%Y-%m-%d").date()
            af_str, at_str = details.get("available_from"), details.get("available_to")
            if af_str and at_str:
                af = datetime.strptime(af_str, "%Y-%m-%d").date()
                at = datetime.strptime(at_str, "%Y-%m-%d").date()
                if ci < af or co > at:
                    reasons.append(f"dates {ci}..{co} outside availability {af}..{at}")
        except ValueError:
            reasons.append("invalid date in hotel details")

    return len(reasons) == 0, reasons


def _calculate_total(pending: dict) -> float | None:
    details = _api_details(pending["hotel_id"])
    if not details:
        return None
    try:
        ci = datetime.strptime(pending["check_in"], "%Y-%m-%d")
        co = datetime.strptime(pending["check_out"], "%Y-%m-%d")
        nights = max(1, (co - ci).days)
    except (ValueError, TypeError):
        nights = 1
    return round(details.get("price", 0) * nights * (pending.get("rooms") or 1), 2)


def _api_book(pending: dict) -> dict:
    return {
        "status": "confirmed",
        "booking_reference": f"BK-{secrets.token_hex(4).upper()}",
        "hotel_id": pending["hotel_id"],
        "destination": pending["destination"],
        "check_in": pending["check_in"],
        "check_out": pending["check_out"],
        "rooms": pending["rooms"],
        "children_ages": pending.get("children_ages", []),
        "currency": pending["currency"],
        "total_price": _calculate_total(pending),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def update_preferences(state: AgentState, **fields) -> dict:
    cleaned, errors = _validate_and_normalize(fields)
    for k, v in cleaned.items():
        if hasattr(state, k) and k != "phase":
            setattr(state, k, v)

    if state.check_in and state.check_out:
        try:
            ci = datetime.strptime(state.check_in, "%Y-%m-%d").date()
            co = datetime.strptime(state.check_out, "%Y-%m-%d").date()
            if co <= ci:
                errors.append("check_out must be strictly after check_in")
        except ValueError:
            pass

    if errors:
        return {"saved": cleaned, "errors": errors,
                "missing": state.missing(), "phase": state.phase}

    if state.phase == "collect" and not state.missing():
        state.phase = "search"

    return {"saved": cleaned, "missing": state.missing(), "phase": state.phase}


def _short(h: dict) -> dict:
    return {k: h.get(k) for k in ("id", "name", "price", "currency", "rating", "amenities")}


def search_hotels(state: AgentState, destination: str | None = None) -> dict:
    dest = (destination or state.destination or "").strip()
    if not dest:
        return {"error": "missing_destination", "hint": "ask the user for the destination first"}
    currency = state.currency or "RUB"
    candidates = _api_search(dest, exclude=state.excluded_ids,
                              limit=SHORTLIST_SIZE * 2, currency=currency)
    state.last_search_ids = {c["id"] for c in candidates}
    return {"candidates": [_short(c) for c in candidates], "count": len(candidates)}


def get_hotel_details(state: AgentState, hotel_id: str) -> dict:
    details = _api_details(hotel_id)
    if not details:
        return {"error": "hotel_not_found", "hotel_id": hotel_id}
    ok, reasons = _matches(state, details)
    return {"hotel": _short(details), "matches": ok, "reasons": reasons}


def save_shortlist(state: AgentState, hotel_ids: list[str]) -> dict:
    if not hotel_ids:
        return {"error": "empty_shortlist"}
    unknown = [hid for hid in hotel_ids if hid not in state.last_search_ids]
    if unknown:
        return {"error": "unknown_hotel_ids", "hotel_ids": unknown,
                "hint": "call search_hotels first and use IDs from its result"}
    state.shortlist = [{"id": hid} for hid in hotel_ids]
    state.phase = "book"
    return {"shortlist": state.shortlist, "phase": state.phase}


def find_others(state: AgentState) -> dict:
    if not state.shortlist:
        return {"error": "no_shortlist_to_exclude",
                "hint": "search first, then call find_others after the shortlist was shown"}
    state.excluded_ids.update(h["id"] for h in state.shortlist)
    state.shortlist.clear()
    state.pending_booking = None
    state.phase = "search"
    return {"excluded_ids": sorted(state.excluded_ids), "phase": state.phase}


def prepare_booking(state: AgentState, hotel_id: str) -> dict:
    shortlist_ids = {h["id"] for h in state.shortlist}
    if hotel_id not in shortlist_ids:
        return {"error": "hotel_not_in_shortlist", "hotel_id": hotel_id,
                "shortlist": sorted(shortlist_ids)}

    details = _api_details(hotel_id)
    ok, reasons = _matches(state, details)
    if not ok:
        return {"error": "hotel_does_not_match", "reasons": reasons}

    state.pending_booking = {
        "hotel_id": hotel_id,
        "destination": state.destination,
        "check_in": state.check_in,
        "check_out": state.check_out,
        "rooms": state.rooms_amount,
        "children_ages": state.children_ages,
        "currency": state.currency,
        "total_price": _calculate_total({
            "hotel_id": hotel_id,
            "check_in": state.check_in,
            "check_out": state.check_out,
            "rooms": state.rooms_amount,
        }),
        "confirmed_by_user": False,
    }
    return {"pending": _public_pending(state.pending_booking),
            "need_confirmation": True,
            "instruction": "ask the user to explicitly confirm the booking"}


def commit_booking(state: AgentState) -> dict:
    if not state.pending_booking:
        return {"error": "no_pending_booking"}
    if not state.pending_booking.get("confirmed_by_user"):
        return {"error": "confirmation_required",
                "hint": "ask the user to explicitly confirm before committing"}

    details = _api_details(state.pending_booking["hotel_id"])
    ok, reasons = _matches(state, details)
    if not ok:
        state.pending_booking = None
        return {"error": "pending_booking_no_longer_matches",
                "reasons": reasons,
                "hint": "call prepare_booking again with matching criteria"}

    result = _api_book(state.pending_booking)
    if not result or result.get("status") != "confirmed":
        return {"error": "booking_failed", "detail": result}

    state.confirmed_booking = result
    state.pending_booking = None
    state.phase = "done"
    return {"booking": result, "status": "confirmed"}


def reset_state(state: AgentState) -> dict:
    state.phase = "collect"
    state.destination = None
    state.check_in = None
    state.check_out = None
    state.max_price = None
    state.currency = None
    state.children_ages = []
    state.rooms_amount = None
    state.shortlist = []
    state.excluded_ids = set()
    state.last_search_ids = set()
    state.pending_booking = None
    state.confirmed_booking = None
    return {"phase": "collect", "missing": state.missing()}


TOOLS = [
    {"type": "function", "function": {
        "name": "update_preferences",
        "description": "Save the preference fields the user just mentioned. "
                       "Call after each new clarification.",
        "parameters": {"type": "object", "properties": {
            "destination": {"type": "string"},
            "check_in": {"type": "string", "description": "YYYY-MM-DD"},
            "check_out": {"type": "string", "description": "YYYY-MM-DD"},
            "max_price": {"type": "number"},
            "currency": {"type": "string",
                         "description": f"one of {sorted(SUPPORTED_CURRENCIES)}"},
            "children_ages": {"type": "array", "items": {"type": "integer"}},
            "rooms_amount": {"type": "integer", "minimum": 1},
        }}}},
    {"type": "function", "function": {
        "name": "search_hotels",
        "description": "Find hotels in the destination, excluding already shown IDs. "
                       "Then verify each candidate with get_hotel_details.",
        "parameters": {"type": "object", "properties": {
            "destination": {"type": "string"},
        }}}},
    {"type": "function", "function": {
        "name": "get_hotel_details",
        "description": "Get hotel details to verify it matches the user's criteria.",
        "parameters": {"type": "object", "required": ["hotel_id"],
                       "properties": {"hotel_id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "save_shortlist",
        "description": f"Save the final shortlist (up to {SHORTLIST_SIZE} hotels) to show the user.",
        "parameters": {"type": "object", "required": ["hotel_ids"],
                       "properties": {"hotel_ids": {
                           "type": "array", "items": {"type": "string"}}}}}},
    {"type": "function", "function": {
        "name": "find_others",
        "description": "User asks for 'other options'. Excludes currently shown ones.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "prepare_booking",
        "description": "Prepare a pending booking for a specific hotel. "
                       "Call after the user picked a hotel, before final confirmation.",
        "parameters": {"type": "object", "required": ["hotel_id"],
                       "properties": {"hotel_id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "commit_booking",
        "description": "Finalize the pending booking. Call ONLY after the user explicitly confirms.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "reset_state",
        "description": "Reset all preferences and start a new search. "
                       "Call only on explicit user request.",
        "parameters": {"type": "object", "properties": {}}}},
]

TOOL_IMPL: dict[str, Callable[..., dict]] = {
    "update_preferences": update_preferences,
    "search_hotels": search_hotels,
    "get_hotel_details": get_hotel_details,
    "save_shortlist": save_shortlist,
    "find_others": find_others,
    "prepare_booking": prepare_booking,
    "commit_booking": commit_booking,
    "reset_state": reset_state,
}

PHASE_TOOLS = {
    "collect": {"update_preferences", "reset_state"},
    "search":  {"search_hotels", "get_hotel_details", "save_shortlist",
                "find_others", "update_preferences", "reset_state"},
    "book":    {"prepare_booking", "commit_booking", "find_others",
                "update_preferences", "get_hotel_details", "reset_state"},
    "done":    {"reset_state"},
}


def build_system_prompt(state: AgentState) -> str:
    return (
        "You are a hotel search and booking assistant.\n"
        f"Current phase: {state.phase}\n"
        f"Current state (JSON): {json.dumps(state.snapshot(), ensure_ascii=False, default=str)}\n"
        f"Missing required fields: {state.missing()}\n\n"
        "Rules:\n"
        "1) While fields are missing — ask the user for them, calling update_preferences "
        "after each new clarification.\n"
        "2) Once all fields are collected — call search_hotels, verify candidates via "
        "get_hotel_details until you have up to N suitable hotels, then fix them via save_shortlist.\n"
        "3) On 'find other options' — call find_others and search again.\n"
        "4) Never book without explicit user confirmation. Call prepare_booking, wait for "
        "the user to say yes/confirm, then call commit_booking.\n"
        "5) Never invent hotel IDs; use only the ones returned by tools.\n"
        "6) Call reset_state only on explicit user request to start over.\n"
        "7) Reply in the user's language (Russian by default). Keep replies concise."
    )


def _trim_history_inplace(history: list, max_items: int = MAX_HISTORY_ITEMS) -> None:
    if len(history) <= max_items:
        return
    cut = len(history) - max_items
    while cut > 0 and history[cut].get("role") != "user":
        cut -= 1
    del history[:cut]


async def run_turn(state: AgentState, history: list, user_message: str,
                   max_steps: int = MAX_LOOP_STEPS) -> str:
    _trim_history_inplace(history)
    start_len = len(history)
    try:
        history.append({"role": "user", "content": user_message})

        for _ in range(max_steps):
            allowed = PHASE_TOOLS[state.phase]
            tools = [t for t in TOOLS if t["function"]["name"] in allowed]

            try:
                resp = await ai_client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "system", "content": build_system_prompt(state)}] + history,
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                )
            except Exception as e:
                logger.exception("LLM call failed")
                return f"Техническая ошибка при обращении к модели: {type(e).__name__}"

            msg = resp.choices[0].message
            finish_reason = resp.choices[0].finish_reason

            msg_dict = msg.model_dump(exclude_none=True)
            msg_dict.setdefault("content", None)
            history.append(msg_dict)

            if not msg.tool_calls:
                content = msg.content or ""
                if not content:
                    logger.warning("empty content, finish_reason=%s", finish_reason)
                    return "Не удалось сформировать ответ. Пожалуйста, уточните запрос."
                return content

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    result = {"error": "invalid_json_arguments", "raw": tc.function.arguments}
                else:
                    fn = TOOL_IMPL.get(tool_name)
                    if fn is None:
                        result = {"error": "unknown_tool", "tool": tool_name}
                    else:
                        try:
                            result = fn(state, **args)
                            if asyncio.iscoroutine(result):
                                result = await result
                        except TypeError as e:
                            result = {"error": "bad_arguments", "detail": str(e)}
                        except Exception as e:
                            logger.exception("tool %s failed", tool_name)
                            result = {"error": "tool_failed",
                                      "tool": tool_name, "detail": str(e)}

                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

        return "Превышено максимальное число шагов обработки. Уточните запрос."

    except BaseException:
        del history[start_len:]
        raise


CONFIRM_RE = re.compile(
    r"^\s*(да|подтверждаю(?:\s+бронь)?|бронируй(?:те)?|забронируй(?:те)?|"
    r"ок(?:ей)?|согласен|согласна|yes|confirm(?:\s+it)?|book\s+it|go\s+ahead)[\s.!?,]*$",
    re.IGNORECASE,
)
REJECT_RE = re.compile(
    r"^\s*(нет|не\s+надо|не\s+бронируй(?:те)?|отмена|отмени(?:те)?|no|cancel|stop)[\s.!?,]*$",
    re.IGNORECASE,
)


def _is_explicit_confirmation(message: str) -> bool:
    return len(message) <= 200 and bool(CONFIRM_RE.match(message))


def _is_explicit_rejection(message: str) -> bool:
    return len(message) <= 200 and bool(REJECT_RE.match(message))


def _enrich_shortlist_item(entry: dict) -> dict:
    details = _api_details(entry["id"]) or {}
    return {
        "id": entry["id"],
        "name": details.get("name"),
        "price": details.get("price"),
        "currency": details.get("currency"),
        "rating": details.get("rating"),
        "amenities": details.get("amenities", []),
    }


def _state_view(state: AgentState) -> dict:
    return {
        "phase": state.phase,
        "missing": state.missing(),
        "preferences": {
            "destination": state.destination,
            "check_in": state.check_in,
            "check_out": state.check_out,
            "max_price": state.max_price,
            "currency": state.currency,
            "children_ages": state.children_ages,
            "rooms_amount": state.rooms_amount,
        },
        "shortlist": [_enrich_shortlist_item(h) for h in state.shortlist],
        "pending_booking": _public_pending(state.pending_booking),
        "confirmed_booking": state.confirmed_booking,
    }


class SendMessagePayload(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)


class SelectHotelPayload(BaseModel):
    hotel_id: str = Field(..., min_length=1, max_length=100)


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="agent.html", context={"user": user})


@router.post("/api/send-message", response_class=JSONResponse)
async def send_message_to_agent(payload: SendMessagePayload, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)
        try:
            history = await load_history(user.id)
        except Exception:
            logger.exception("load_history failed")
            history = []
        _trim_history_inplace(history)
        original_len = len(history)

        auto_committed = False
        commit_error: str | None = None

        if state.pending_booking:
            if _is_explicit_rejection(payload.message):
                state.pending_booking["confirmed_by_user"] = False
            elif _is_explicit_confirmation(payload.message):
                state.pending_booking["confirmed_by_user"] = True
                try:
                    res = commit_booking(state)
                    if res.get("status") == "confirmed":
                        auto_committed = True
                    else:
                        commit_error = res.get("error") or "commit_failed"
                except Exception:
                    logger.exception("auto-commit on explicit confirmation failed")
                    commit_error = "commit_exception"
            else:
                state.pending_booking["confirmed_by_user"] = False

        user_message = payload.message
        if commit_error:
            user_message = (
                payload.message +
                f"\n\n[служебное: автоматическое подтверждение брони не удалось, "
                f"причина: {commit_error}. Сообщи пользователю и предложи выбрать отель заново.]"
            )

        try:
            reply = await asyncio.wait_for(
                run_turn(state, history, user_message),
                timeout=TURN_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            try:
                await _save_state(user.id, state)
            except Exception:
                logger.exception("save_state failed on timeout")
            return JSONResponse(
                {"error": "agent_timeout", "message": "Превышено время обработки запроса",
                 "state": _state_view(state)},
                status_code=504,
            )
        except Exception as e:
            try:
                await _save_state(user.id, state)
            except Exception:
                logger.exception("save_state failed on error")
            logger.exception("agent turn failed")
            return JSONResponse(
                {"error": "agent_error", "message": str(e),
                 "state": _state_view(state)},
                status_code=500,
            )

        new_messages = history[original_len:]
        try:
            await _persist_turn(user.id, state, new_messages)
        except Exception:
            logger.exception("_persist_turn failed")

        if auto_committed and state.confirmed_booking:
            if not reply or "брон" not in reply.lower():
                bk = state.confirmed_booking
                reply = (
                    f"Бронь подтверждена! Номер: {bk.get('booking_reference')}. "
                    f"Сумма: {bk.get('total_price')} {bk.get('currency')}."
                )

        return JSONResponse({"message": reply, "state": _state_view(state)})


@router.get("/api/state", response_class=JSONResponse)
async def get_state(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)
    return JSONResponse(_state_view(state))


@router.get("/api/history", response_class=JSONResponse)
async def get_history(request: Request):
    """Только user/assistant текст — для рендера чата на фронте."""
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        messages = await load_visible_history(user.id)
    except Exception:
        logger.exception("load_visible_history failed")
        messages = []
    return JSONResponse({"messages": messages})


@router.post("/api/select-hotel", response_class=JSONResponse)
async def select_hotel(payload: SelectHotelPayload, request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)
        result = prepare_booking(state, payload.hotel_id)
        try:
            await _save_state(user.id, state)
        except Exception:
            logger.exception("save_state failed")
        snapshot = _state_view(state)
    return JSONResponse({"result": result, "state": snapshot})


@router.post("/api/booking/confirm", response_class=JSONResponse)
async def confirm_booking_endpoint(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)
        if not state.pending_booking:
            return JSONResponse({"error": "no_pending_booking",
                                 "state": _state_view(state)}, status_code=400)
        state.pending_booking["confirmed_by_user"] = True
        try:
            result = commit_booking(state)
        except Exception:
            logger.exception("commit_booking failed")
            result = {"error": "commit_failed"}
        try:
            await _save_state(user.id, state)
        except Exception:
            logger.exception("save_state failed")
        snapshot = _state_view(state)
    return JSONResponse({"result": result, "state": snapshot})


@router.post("/api/booking/cancel", response_class=JSONResponse)
async def cancel_booking_endpoint(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)
        state.pending_booking = None
        try:
            await _save_state(user.id, state)
        except Exception:
            logger.exception("save_state failed")
        snapshot = _state_view(state)
    return JSONResponse({"ok": True, "state": snapshot})


@router.post("/api/find-others", response_class=JSONResponse)
async def find_others_endpoint(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)

        if state.phase == "done" or state.confirmed_booking:
            return JSONResponse({"error": "already_booked",
                                 "state": _state_view(state)}, status_code=400)

        find_others(state)
        if not state.destination:
            return JSONResponse({"error": "missing_destination",
                                 "state": _state_view(state)}, status_code=400)
        if not state.currency:
            state.currency = "RUB"

        candidates = _api_search(state.destination, exclude=state.excluded_ids,
                                  limit=SHORTLIST_SIZE * 3, currency=state.currency)
        state.last_search_ids = {c["id"] for c in candidates}

        good: list[str] = []
        for c in candidates:
            details = _api_details(c["id"])
            if details:
                ok, _ = _matches(state, details)
                if ok:
                    good.append(c["id"])
            if len(good) >= SHORTLIST_SIZE:
                break

        if good:
            save_shortlist(state, good)
        else:
            state.phase = "search"

        try:
            await _save_state(user.id, state)
        except Exception:
            logger.exception("save_state failed")

        snapshot = _state_view(state)
        found = len(good)

    return JSONResponse({"state": snapshot, "candidates_found": found})


@router.post("/api/reset", response_class=JSONResponse)
async def reset_endpoint(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    lock = _get_lock(user.id)
    async with lock:
        state = await _load_state(user.id)
        reset_state(state)
        try:
            await _save_state(user.id, state)
        except Exception:
            logger.exception("save_state failed")
        try:
            await delete_history(user.id)
        except Exception:
            logger.exception("delete_history failed")
        snapshot = _state_view(state)
    return JSONResponse({"ok": True, "state": snapshot})