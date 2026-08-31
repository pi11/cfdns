from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    encrypted_token: Mapped[str] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)

    zones: Mapped[list[Zone]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Zone(TimestampMixin, Base):
    __tablename__ = "zones"
    __table_args__ = (UniqueConstraint("account_id", "cloudflare_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    cloudflare_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")

    account: Mapped[Account] = relationship(back_populates="zones")
    records: Mapped[list[DNSRecord]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )


class DNSRecord(TimestampMixin, Base):
    __tablename__ = "dns_records"
    __table_args__ = (
        UniqueConstraint("zone_id", "cloudflare_id"),
        Index("ix_dns_records_search", "name", "content", "cloudflare_comment"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"))
    cloudflare_id: Mapped[str] = mapped_column(String(64))
    record_type: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(253), index=True)
    content: Mapped[str] = mapped_column(Text, index=True)
    ttl: Mapped[int] = mapped_column(Integer, default=1)
    proxied: Mapped[bool | None] = mapped_column(Boolean)
    proxiable: Mapped[bool] = mapped_column(Boolean, default=False)
    cloudflare_comment: Mapped[str | None] = mapped_column(Text)
    local_comment: Mapped[str | None] = mapped_column(Text)
    data_json: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int | None] = mapped_column(Integer)
    ssl_check_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    zone: Mapped[Zone] = relationship(back_populates="records")
    ssl_results: Mapped[list[SSLCheckResult]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )

    @property
    def ssl_earliest_expiry(self) -> datetime | None:
        expirations = [
            result.certificate_expires_at
            for result in self.ssl_results
            if result.certificate_expires_at is not None
        ]
        return min(expirations) if expirations else None

    @property
    def ssl_display_status(self) -> str:
        if not self.ssl_results:
            return "pending"
        if any(result.status != "valid" for result in self.ssl_results):
            return "danger"
        earliest = self.ssl_earliest_expiry
        if earliest is None:
            return "danger"
        remaining = earliest - datetime.now(UTC)
        if remaining <= timedelta(days=14):
            return "danger" if remaining.total_seconds() <= 0 else "warning"
        if remaining <= timedelta(days=30):
            return "notice"
        return "ok"


class SSLCheckResult(Base):
    __tablename__ = "ssl_check_results"
    __table_args__ = (UniqueConstraint("record_id", "ip_address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("dns_records.id", ondelete="CASCADE"), index=True
    )
    ip_address: Mapped[str] = mapped_column(String(45))
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    record: Mapped[DNSRecord] = relationship(back_populates="ssl_results")
