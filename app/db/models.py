from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    threads: Mapped[list["ChatThread"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    records: Mapped[list["ChatRecord"]] = relationship(back_populates="user")


class ChatThread(Base):
    __tablename__ = "chat_threads"

    # 给前端使用的线程 ID，建议 UUID 字符串
    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # 给 LangGraph PostgresSaver 使用的真正 checkpoint thread_id
    # 这样即使两个用户传入相同 thread_id，也会被隔离
    checkpoint_thread_id: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        index=True,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(200), default="新对话")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped["User"] = relationship(back_populates="threads")
    records: Mapped[list["ChatRecord"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class ChatRecord(Base):
    __tablename__ = "chat_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("chat_threads.id"),
        nullable=False,
        index=True,
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    used_search: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped["User"] = relationship(back_populates="records")
    thread: Mapped["ChatThread"] = relationship(back_populates="records")
    sources: Mapped[list["SearchSource"]] = relationship(
        back_populates="chat_record",
        cascade="all, delete-orphan",
    )


class SearchSource(Base):
    __tablename__ = "search_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chat_records.id"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")

    chat_record: Mapped["ChatRecord"] = relationship(back_populates="sources")
