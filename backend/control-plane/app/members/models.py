import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.models import TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceMixin
from app.core.permissions import Role

# Used by crud.py to turn a duplicate membership into a clear 409.
UQ_MEMBER = "uq_workspace_members_workspace_id_user_id"

_ROLES_SQL = ", ".join(f"'{role}'" for role in Role)


class WorkspaceMember(Base, UUIDPrimaryKeyMixin, TimestampMixin, WorkspaceMixin):
    """A person's membership of one workspace, with their role there.

    One person can belong to several workspaces with different roles. The
    role is read from here on every request, so a change applies at once.
    """

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name=UQ_MEMBER),
        CheckConstraint(f"role IN ({_ROLES_SQL})", name="role_known"),
    )

    # RESTRICT: people are disabled, never deleted (MT.1).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    # Who added them; NULL for the migrated admin and startup's first owner.
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
