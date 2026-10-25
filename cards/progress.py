"""Points and streak bookkeeping.

A correct answer is worth 5 points and a wrong one 1 point. A streak day is
earned when 10 cards are reviewed in one day: the first time the daily goal is
reached the user gets a 20 point bonus plus one more day on their streak.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .levels import level_progress
from .models import Card, StudyProgress

DAILY_GOAL = 10
POINTS_CORRECT = 5
POINTS_WRONG = 1
STREAK_BONUS = 20


def get_progress(user):
    progress, _ = StudyProgress.objects.get_or_create(user=user)
    return progress


def record_card_review(user, solved=True, today=None):
    """Award points for one reviewed card.

    A correct answer earns 5 points and a wrong answer 1. Returns
    ``(progress, streak_awarded)``. The row is locked so two cards answered at
    the same time cannot lose points or double-award the bonus.
    """
    today = today or timezone.localdate()

    with transaction.atomic():
        progress = get_progress(user)
        progress = StudyProgress.objects.select_for_update().get(pk=progress.pk)

        progress.points += POINTS_CORRECT if solved else POINTS_WRONG
        if progress.cards_today_date != today:
            progress.cards_today = 0
            progress.cards_today_date = today
        progress.cards_today += 1

        streak_awarded = False
        if (
            progress.cards_today >= DAILY_GOAL
            and progress.last_streak_date != today
        ):
            if progress.last_streak_date == today - timedelta(days=1):
                progress.current_streak += 1
            else:
                progress.current_streak = 1
            progress.last_streak_date = today
            progress.longest_streak = max(
                progress.longest_streak,
                progress.current_streak,
            )
            progress.points += STREAK_BONUS
            streak_awarded = True

        progress.save(
            update_fields=[
                "points",
                "cards_today",
                "cards_today_date",
                "current_streak",
                "longest_streak",
                "last_streak_date",
            ]
        )

    return progress, streak_awarded


def progress_summary(user, today=None):
    """Everything the header and progress page need, in one place."""
    today = today or timezone.localdate()
    progress = get_progress(user)

    streak_alive = progress.last_streak_date in (
        today,
        today - timedelta(days=1),
    )
    streak = progress.current_streak if streak_alive else 0
    cards_today = (
        progress.cards_today if progress.cards_today_date == today else 0
    )
    level, next_level, level_percent = level_progress(progress.points)

    return {
        "progress": progress,
        "points": progress.points,
        "streak": streak,
        "longest_streak": progress.longest_streak,
        "cards_today": cards_today,
        "goal": DAILY_GOAL,
        "goal_remaining": max(0, DAILY_GOAL - cards_today),
        "streak_ready": cards_today >= DAILY_GOAL,
        "level": level,
        "next_level": next_level,
        "level_percent": level_percent,
        "points_to_next": (
            next_level.points - progress.points if next_level else 0
        ),
    }


def due_card_count(user):
    return sum(
        1
        for card in Card.objects.filter(user=user).only("box", "date_moved")
        if card.to_repeat
    )


def streak_message(progress):
    days = progress.current_streak
    day_label = "day" if days == 1 else "days"
    return (
        f"🔥 Daily goal reached — {days} {day_label} streak! "
        f"+{STREAK_BONUS} points."
    )
