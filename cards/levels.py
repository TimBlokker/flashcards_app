"""Level titles for the points system.

Points come from studying: a correct answer is worth 5 points, a wrong one 1
point, and every completed daily goal (10 cards) adds a 20 point streak bonus.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Level:
    number: int
    title: str
    arabic_title: str
    points: int
    tagline: str


LEVELS = (
    Level(
        1,
        "Little Student",
        "طالب صغير",
        0,
        "Every professor starts with a single word.",
    ),
    Level(
        2,
        "Letter Apprentice",
        "تلميذ الحروف",
        240,
        "The alphabet is starting to feel like home.",
    ),
    Level(
        3,
        "Word Collector",
        "جامع الكلمات",
        640,
        "Your pockets are full of new words.",
    ),
    Level(
        4,
        "Sentence Weaver",
        "ناسج الجمل",
        1280,
        "Words are turning into sentences.",
    ),
    Level(
        5,
        "Grammar Gardener",
        "بستاني النحو",
        2200,
        "You prune a sentence until it blooms.",
    ),
    Level(
        6,
        "Storyteller",
        "راوي الحكايات",
        3400,
        "You can carry a story across two languages.",
    ),
    Level(
        7,
        "Souq Poet",
        "شاعر السوق",
        5000,
        "Your Arabic has rhythm now.",
    ),
    Level(
        8,
        "Language Sage",
        "حكيم اللغة",
        7000,
        "Nuance no longer trips you up.",
    ),
    Level(
        9,
        "Master of Letters",
        "أستاذ الحروف",
        9600,
        "You teach the letters new tricks.",
    ),
    Level(
        10,
        "Professor of Arabic",
        "أستاذ العربية",
        13000,
        "The classroom is yours.",
    ),
)


def level_for_points(points):
    """Return the highest level unlocked by ``points``."""
    current = LEVELS[0]
    for level in LEVELS:
        if points >= level.points:
            current = level
        else:
            break
    return current


def level_progress(points):
    """Return ``(current_level, next_level, percent_to_next)``."""
    current = level_for_points(points)
    upcoming = next((level for level in LEVELS if level.points > points), None)
    if upcoming is None:
        return current, None, 100

    span = upcoming.points - current.points
    earned = points - current.points
    percent = round(earned * 100 / span) if span else 100
    return current, upcoming, max(0, min(100, percent))


def level_ladder(points):
    """Describe every level for the progress page."""
    current = level_for_points(points)
    return [
        {
            "level": level,
            "unlocked": points >= level.points,
            "current": level.number == current.number,
        }
        for level in LEVELS
    ]
