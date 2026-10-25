# Flashcards

A small Django app for practising Arabic vocabulary with spaced repetition.

## Running locally

```bash
python -m venv .
bin/pip install -r requirements.txt
bin/python manage.py migrate
bin/python manage.py runserver
```

Run the tests with `bin/python manage.py test cards`.

## Points, streaks and levels

- A **correct answer** earns **5 points**, a wrong answer earns **1 point**.
  This applies to both "Study" (repeat) and "Practice".
- **10 reviewed cards in one day** completes the daily goal: **+20 points** and
  **one streak day**. The bonus is awarded once per day.
- Consecutive goal days increase the streak; missing a day resets it to 1 on
  the next completed goal.
- Progress (streak, points, goal and level) appears in the header and on
  `/progress/`, including the full level ladder.

| Level | Title | Arabic | Points |
| ---: | --- | --- | ---: |
| 1 | Little Student | طالب صغير | 0 |
| 2 | Letter Apprentice | تلميذ الحروف | 240 |
| 3 | Word Collector | جامع الكلمات | 640 |
| 4 | Sentence Weaver | ناسج الجمل | 1,280 |
| 5 | Grammar Gardener | بستاني النحو | 2,200 |
| 6 | Storyteller | راوي الحكايات | 3,400 |
| 7 | Souq Poet | شاعر السوق | 5,000 |
| 8 | Language Sage | حكيم اللغة | 7,000 |
| 9 | Master of Letters | أستاذ الحروف | 9,600 |
| 10 | Professor of Arabic | أستاذ العربية | 13,000 |

## WhatsApp reminders

On `/progress/` you can save a WhatsApp number and a reminder time. The
"Open WhatsApp reminder" button creates a `wa.me` link with the message
pre-filled, so it works without any credentials.

For automatic delivery through the WhatsApp Cloud API, set:

```bash
export WHATSAPP_ACCESS_TOKEN="..."
export WHATSAPP_PHONE_NUMBER_ID="..."
export STUDY_APP_URL="https://your-app.example"   # optional, added to the message
```

Then run the command hourly from cron:

```cron
0 * * * * cd /path/to/flashcards_app && bin/python manage.py send_study_reminders
```

Each user receives at most one reminder per day, within the hour after their
chosen time. Options:

```bash
bin/python manage.py send_study_reminders --dry-run
bin/python manage.py send_study_reminders --force
bin/python manage.py send_study_reminders --user Tim
```

`--dry-run` prints the messages without sending. `--force` ignores the time
window and the once-per-day guard. Without the two Cloud API variables the
command reports that WhatsApp is not configured; the `wa.me` button still
works.
