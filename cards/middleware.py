import time

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class IdleSessionTimeoutMiddleware:
    """Log authenticated users out after a period without activity."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_timeout = getattr(settings, "SESSION_IDLE_TIMEOUT", 300)
        self.login_url = reverse("login")

        static_url = settings.STATIC_URL
        if not static_url.startswith("/"):
            static_url = f"/{static_url}"
        self.ignored_prefixes = (static_url, "/media/")
        self.public_prefixes = (
            self.login_url,
            reverse("signup"),
            reverse("password_reset"),
            reverse("password_reset_done"),
            "/accounts/password-reset/confirm/",
            reverse("password_reset_complete"),
            reverse("idle-logout"),
            reverse("tab-session-expired"),
            "/admin/",
        )

    def __call__(self, request):
        if request.path_info.startswith(self.ignored_prefixes):
            return self.get_response(request)

        if request.user.is_authenticated:
            now = time.time()
            last_activity = request.session.get("last_activity")
            try:
                last_activity = float(last_activity)
            except (TypeError, ValueError):
                last_activity = None

            if (
                last_activity is not None
                and now - last_activity > self.idle_timeout
            ):
                logout(request)
                if request.path_info.startswith(self.public_prefixes):
                    if request.path_info == self.login_url:
                        return redirect(f"{self.login_url}?reason=idle")
                    return self.get_response(request)
                return redirect(f"{self.login_url}?reason=idle")

            request.session["last_activity"] = now

        return self.get_response(request)
