from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langalpha.config import Settings
from supabase import Client, create_client


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: str
    email: str | None = None


class Authenticator(Protocol):
    def authenticate(self, access_token: str) -> AuthUser: ...


class SupabaseAuthenticator:
    """Validate bearer tokens with Supabase Auth before authorization decisions."""

    def __init__(self, settings: Settings, *, client: Client | None = None) -> None:
        self.client = client or create_client(
            settings.require_supabase_url(),
            settings.require_supabase_publishable_key(),
        )

    def authenticate(self, access_token: str) -> AuthUser:
        try:
            response = self.client.auth.get_user(access_token)
        except Exception as exc:
            raise AuthenticationError("invalid or expired access token") from exc
        user = getattr(response, "user", None) if response is not None else None
        user_id = getattr(user, "id", None)
        if not user_id:
            raise AuthenticationError("invalid or expired access token")
        return AuthUser(
            id=str(user_id),
            email=(str(user.email) if getattr(user, "email", None) else None),
        )
