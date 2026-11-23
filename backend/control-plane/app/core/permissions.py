"""Who may do what inside a workspace (PLAN.md section 6, decision 2).

Endpoints declare a permission, never a role: `require_permission(Permission.SERVICES_WRITE)`.
Which role holds which permission is this one constant map. It can move to
database tables later (custom roles) without touching a single endpoint.
"""

from enum import StrEnum


class Role(StrEnum):
    """A member's role in one workspace, highest first (decision 0)."""

    OWNER = "owner"
    API_ADMIN = "api_admin"
    VIEWER = "viewer"


class Permission(StrEnum):
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_WRITE = "workspace:write"
    WORKSPACE_DELETE = "workspace:delete"
    MEMBERS_READ = "members:read"
    MEMBERS_WRITE = "members:write"
    SERVICES_READ = "services:read"
    SERVICES_WRITE = "services:write"
    ROUTES_READ = "routes:read"
    ROUTES_WRITE = "routes:write"
    CONSUMERS_READ = "consumers:read"
    CONSUMERS_WRITE = "consumers:write"
    KEYS_READ = "keys:read"
    KEYS_WRITE = "keys:write"
    AUDIT_READ = "audit:read"
    USAGE_READ = "usage:read"
    PLANS_READ = "plans:read"
    PLANS_WRITE = "plans:write"
    APIS_READ = "apis:read"
    APIS_WRITE = "apis:write"

    @property
    def is_read(self) -> bool:
        return self.value.endswith(":read")


_VIEWER = frozenset(
    {
        Permission.WORKSPACE_READ,
        Permission.MEMBERS_READ,
        Permission.SERVICES_READ,
        Permission.ROUTES_READ,
        Permission.CONSUMERS_READ,
        Permission.KEYS_READ,
        Permission.USAGE_READ,
        Permission.PLANS_READ,
        Permission.APIS_READ,
    }
)
_API_ADMIN = _VIEWER | {
    Permission.SERVICES_WRITE,
    Permission.ROUTES_WRITE,
    Permission.CONSUMERS_WRITE,
    Permission.KEYS_WRITE,
    Permission.AUDIT_READ,
    Permission.PLANS_WRITE,
    Permission.APIS_WRITE,
}
_OWNER = _API_ADMIN | {
    Permission.MEMBERS_WRITE,
    Permission.WORKSPACE_WRITE,
    Permission.WORKSPACE_DELETE,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.API_ADMIN: _API_ADMIN,
    Role.OWNER: _OWNER,
}


def role_allows(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
