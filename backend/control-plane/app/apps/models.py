"""Apps: a developer's access to one workspace's APIs (P.2).

An app is backed by a consumer of its workspace, so the gateway, plans,
quotas and keys work unchanged: the app's keys are its consumer's keys, and
its plan is its consumer's plan. The app adds who owns it (a developer),
what it was made for (an API of the catalog) and whether the workspace has
let it in (`status`). Its consumer is active exactly while it is approved.
"""

import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin

NAME_MAX_LENGTH = 100
# How many apps one developer may have in one workspace. Open sign-up must
# not let a single developer use up a workspace's consumers.
MAX_APPS_PER_DEVELOPER = 10

UQ_APP_NAME = "uq_apps_workspace_id_developer_id_name"
FK_APP_API = "fk_apps_api"


class AppStatus(StrEnum):
    PENDING = "pending"  # waits for an API Admin (its plan requires approval)
    APPROVED = "approved"  # its consumer is active: its keys work
    REJECTED = "rejected"  # refused, or its access taken away later

    @property
    def consumer_active(self) -> bool:
        return self is AppStatus.APPROVED


class App(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "developer_id", "name", name=UQ_APP_NAME),
        # The consumer behind it, in the same workspace; deleting the consumer
        # (an admin's decision) deletes the app.
        ForeignKeyConstraint(
            ["consumer_id", "workspace_id"],
            ["consumers.id", "consumers.workspace_id"],
            name="fk_apps_consumer",
            ondelete="CASCADE",
        ),
        # The API it was made for, in the same workspace: an API with apps
        # can't be deleted (409).
        ForeignKeyConstraint(
            ["api_id", "workspace_id"],
            ["apis.id", "apis.workspace_id"],
            name=FK_APP_API,
            ondelete="RESTRICT",
        ),
    )

    developer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("developers.id", ondelete="RESTRICT"), index=True
    )
    consumer_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    api_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    status: Mapped[AppStatus] = mapped_column(
        Enum(
            AppStatus,
            name="status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        )
    )
