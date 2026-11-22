"""Field types and base models shared by every feature's schemas."""

from typing import Annotated, ClassVar, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

# An identifier-like name: "orders", "mobile-app", "user_api".
# No spaces or capitals, so "Orders" and "orders" can never both exist.
SlugName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$"),
    Field(description="Lowercase letters, digits, '-' and '_'", examples=["orders"]),
]


class PartialUpdate(BaseModel):
    """Base for PATCH bodies: every field optional, only sent fields change.

    Leaving a field out means "don't change it". Sending null would mean
    "erase it", which none of our columns allow, so it is rejected (422);
    except for the fields a subclass names in `nullable`, where null means
    "back to the platform default".
    """

    nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        for field in self.model_fields_set - self.nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# A person's email, as GoTrue holds it: lowercase, checked for shape only.
# Not EmailStr: email-validator refuses addresses GoTrue accepts (e.g. *.test),
# and the real check is whether the address is (or becomes) a user.
PersonEmail = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
    Field(examples=["dev@example.com"]),
]
