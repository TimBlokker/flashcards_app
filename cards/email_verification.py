import logging
import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import EmailVerification

logger = logging.getLogger(__name__)


def send_verification_email(request, user):
    """Create or refresh a verification token and email it to the user."""
    email = (user.email or "").strip()
    if not email:
        return False

    verification, _ = EmailVerification.objects.get_or_create(
        user=user,
        defaults={
            "email": email,
            "token": secrets.token_urlsafe(32),
            "sent_at": timezone.now(),
        },
    )
    if verification.is_verified:
        return True

    verification.email = email
    verification.token = secrets.token_urlsafe(32)
    verification.sent_at = timezone.now()
    verification.verified_at = None
    verification.save(
        update_fields=["email", "token", "sent_at", "verified_at"],
    )

    timeout = getattr(settings, "EMAIL_VERIFICATION_TIMEOUT", 86400)
    verification_url = request.build_absolute_uri(
        reverse("verify-email", args=[verification.token]),
    )
    subject = render_to_string(
        "cards/auth/email_verification_subject.txt",
        {"user": user},
    ).strip()
    body = render_to_string(
        "cards/auth/email_verification_email.txt",
        {
            "user": user,
            "email": email,
            "verification_url": verification_url,
            "expires_hours": max(1, timeout // 3600),
        },
    )

    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [email],
        )
    except Exception:
        logger.exception("Could not send a verification email to %s", email)
        return False
    return True
