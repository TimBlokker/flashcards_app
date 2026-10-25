"""Send the daily WhatsApp study reminder.

Run this from cron every hour, e.g.::

    0 * * * * cd /path/to/app && bin/python manage.py send_study_reminders

Each reminder goes out once per day, within the hour after the time the user
picked. Add ``--dry-run`` to see the messages without contacting WhatsApp.
"""
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError

from django.core.management.base import BaseCommand
from django.utils import timezone

from cards.models import StudyReminder
from cards.progress import due_card_count
from cards.reminders import build_reminder_message
from cards.whatsapp import WhatsAppNotConfigured, send_whatsapp_message


class Command(BaseCommand):
    help = "Send due WhatsApp study reminders to users who opted in."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the messages without sending them.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignore the reminder time and the once-per-day guard.",
        )
        parser.add_argument(
            "--user",
            help="Only send to this username.",
        )

    def handle(self, *args, **options):
        now = timezone.localtime()
        reminders = (
            StudyReminder.objects.filter(enabled=True)
            .exclude(phone_number="")
            .select_related("user")
            .order_by("pk")
        )
        if options["user"]:
            reminders = reminders.filter(user__username=options["user"])

        sent = 0
        for reminder in reminders.iterator():
            if not options["force"]:
                if reminder.last_sent_on == now.date():
                    continue
                if not self._in_window(reminder.reminder_time, now):
                    continue

            due_count = due_card_count(reminder.user)
            message = build_reminder_message(reminder.user, due_count=due_count)

            if options["dry_run"]:
                self.stdout.write(
                    f"[dry-run] {reminder.user.get_username()} "
                    f"-> {reminder.phone_number}"
                )
                self.stdout.write(message)
                self.stdout.write("")
                sent += 1
                continue

            try:
                send_whatsapp_message(reminder.phone_number, message)
            except WhatsAppNotConfigured as error:
                self.stderr.write(f"WhatsApp is not configured: {error}")
                return
            except (HTTPError, URLError, ValueError) as error:
                self.stderr.write(
                    f"Could not send to {reminder.user.get_username()}: {error}"
                )
                continue

            reminder.last_sent_on = now.date()
            reminder.save(update_fields=["last_sent_on"])
            sent += 1

        label = "reminder" if sent == 1 else "reminders"
        self.stdout.write(self.style.SUCCESS(f"Processed {sent} {label}."))

    @staticmethod
    def _in_window(reminder_time, now):
        start = datetime.combine(now.date(), reminder_time, tzinfo=now.tzinfo)
        for candidate in (start - timedelta(days=1), start):
            if candidate <= now < candidate + timedelta(hours=1):
                return True
        return False
