"""Build the WhatsApp study reminder text."""
from .progress import DAILY_GOAL, progress_summary
from .whatsapp import app_url


def build_reminder_message(user, summary=None, due_count=0):
    summary = summary or progress_summary(user)
    level = summary["level"]
    streak = summary["streak"]
    day_label = "day" if streak == 1 else "days"

    lines = [
        f"مرحبا {user.get_username()}! 👋",
        f"Your Arabic streak is {streak} {day_label} · {summary['points']} points.",
        f"Level {level.number}: {level.arabic_title} ({level.title}).",
    ]
    if summary["streak_ready"]:
        lines.append(
            f"{summary['cards_today']}/{DAILY_GOAL} cards today — goal done ✓"
        )
    else:
        lines.append(
            f"{summary['cards_today']}/{DAILY_GOAL} cards today — "
            f"{summary['goal_remaining']} to keep the flame alive."
        )
    if due_count:
        card_label = "card" if due_count == 1 else "cards"
        lines.append(f"{due_count} {card_label} ready for review.")
    lines.append("10 cards = a streak day + 20 bonus points. هيا نتعلّم! 🚀")

    url = app_url()
    if url:
        lines.append(url)
    return "\n".join(lines)
