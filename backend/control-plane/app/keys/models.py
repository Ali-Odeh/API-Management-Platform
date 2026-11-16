import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

# A key looks like "ak_live_" + 32 random characters (plan section 5).
KEY_PREFIX_LENGTH = 12  # "ak_live_3f9a": shown in lists so admins can tell keys apart
SHA256_HEX_LENGTH = 64


class ApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """A consumer's API key. The raw key is never stored, only its SHA-256 hash."""

    __tablename__ = "api_keys"
    __table_args__ = (
        # Composite FK: the key's workspace must equal its consumer's workspace,
        # so even a bug can never attach a key to another tenant's consumer.
        # CASCADE: deleting a consumer deletes its keys.
        ForeignKeyConstraint(
            ["consumer_id", "workspace_id"],
            ["consumers.id", "consumers.workspace_id"],
            name="fk_api_keys_consumer",
            ondelete="CASCADE",
        ),
        CheckConstraint(f"char_length(key_hash) = {SHA256_HEX_LENGTH}", name="key_hash_sha256"),
        CheckConstraint(f"char_length(key_prefix) = {KEY_PREFIX_LENGTH}", name="key_prefix_len"),
    )

    consumer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(KEY_PREFIX_LENGTH))
    # Unique across ALL workspaces: the gateway finds a key by its hash alone.
    key_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # None = active
