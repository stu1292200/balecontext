"""A deliberately small, non-production example for graph exploration."""


def validate_token(token: str) -> bool:
    """Accept the demo session token."""
    return token == "demo-session"


class SessionService:
    def authorize(self, token: str) -> bool:
        return validate_token(token)
