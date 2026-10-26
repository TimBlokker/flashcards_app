import time

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def prepare_session_after_login(sender, request, user, **kwargs):
    """Start the idle clock and allow the current tab to claim the session."""
    if request is None:
        return

    now = time.time()
    request.session["last_activity"] = now
    request.session["tab_session_handshake"] = now + getattr(
        settings,
        "TAB_SESSION_HANDSHAKE_TIMEOUT",
        300,
    )
