import time

from django.conf import settings


def session_ui(request):
    """Expose session-related values needed by the base template."""
    handshake = False
    handshake_expiry = request.session.get("tab_session_handshake")

    if request.user.is_authenticated and handshake_expiry is not None:
        try:
            handshake = float(handshake_expiry) > time.time()
        except (TypeError, ValueError):
            handshake = False

        if not handshake:
            request.session.pop("tab_session_handshake", None)

    email_is_verified = False
    email_needs_verification = False
    if request.user.is_authenticated and (request.user.email or "").strip():
        verification = getattr(request.user, "email_verification", None)
        email_is_verified = bool(verification and verification.is_verified)
        email_needs_verification = not email_is_verified

    return {
        "idle_timeout_seconds": getattr(
            settings,
            "SESSION_IDLE_TIMEOUT",
            300,
        ),
        "tab_session_handshake": handshake,
        "email_is_verified": email_is_verified,
        "email_needs_verification": email_needs_verification,
    }
