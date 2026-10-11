from .auth import SessionService, validate_token


def login(token: str) -> dict:
    if not validate_token(token):
        return {"status": "denied"}
    return {"status": "authenticated"}


def dashboard(token: str) -> bool:
    return SessionService().authorize(token)
