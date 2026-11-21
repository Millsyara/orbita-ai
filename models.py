from sqlalchemy import String, Integer, DateTime, ForeignKey, Float, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from datetime import datetime
from typing import Optional, List
from zoneinfo import ZoneInfo


Moscow = ZoneInfo("Europe/Moscow") 


class Base(DeclarativeBase):
    pass


class UserBase(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True
    )

    token: Mapped[str] = mapped_column(
        String
    )

    username: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True
    )

    email: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True
    )

    password: Mapped[str] = mapped_column(
        String
    )

    status: Mapped[str] = mapped_column(
        String,
        default="verified"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(Moscow),
        index=True
    )

    verification_token: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True
    )

    verification_token_expires: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    password_reset_token: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True
    )

    password_reset_token_expires: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        index=True
    )

    messages: Mapped[List["MessageBase"]] = relationship(
        back_populates="user"
    )

    agent_state: Mapped[Optional["AgentStateBase"]] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class MessageBase(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        nullable=False
    )

    content: Mapped[str] = mapped_column(
        String
    )

    content_type: Mapped[str] = mapped_column(
        String,
        default="text"
    )

    is_user: Mapped[bool] = mapped_column(
        default=True
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(Moscow),
        index=True
    )

    user: Mapped["UserBase"] = relationship(
        back_populates="messages"
    )


class AgentStateBase(Base):
    __tablename__ = "agent_states"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        unique=True,
        index=True,
        nullable=False,
    )

    phase: Mapped[str] = mapped_column(String, default="collect")

    destination: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    check_in: Mapped[Optional[str]] = mapped_column(String, nullable=True)      
    check_out: Mapped[Optional[str]] = mapped_column(String, nullable=True)     
    max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    children_ages: Mapped[list] = mapped_column(JSON, default=list)
    rooms_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    shortlist: Mapped[list] = mapped_column(JSON, default=list)                 
    excluded_ids: Mapped[list] = mapped_column(JSON, default=list)            
    last_search_ids: Mapped[list] = mapped_column(JSON, default=list)

    pending_booking: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    confirmed_booking: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(Moscow),
        onupdate=lambda: datetime.now(Moscow),
        index=True,
    )

    user: Mapped["UserBase"] = relationship(back_populates="agent_state")