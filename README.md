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

## Accounts, password reset and session security

Every account has an email address, required at signup, used for password
recovery. Existing accounts are sent to a required **Add your email address**
page on their next authenticated request. Users can change their address under
**Account settings** in the account menu.

### Email verification

- Signup and email changes send a verification link. The address must be
  verified before it can be used for password reset.
- Verification links expire after 24 hours (`EMAIL_VERIFICATION_TIMEOUT`, in
  seconds). Users can resend a link from the reminder banner or **Account
  settings**.
- Verification does not block login. Until the address is verified, a reminder
  banner appears in the app.
- Existing accounts with an email address were marked verified by the data
  migration. New addresses always require verification.

### Password reset

- **Forgot your password?** on the login page sends a one-hour reset link.
  Reset emails are only sent to verified addresses.
- In development the email is printed to the terminal by default (Django's
  console backend). To send real email, set these environment variables:

  ```bash
  export DJANGO_EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
  export EMAIL_HOST="smtp.example.com"
  export EMAIL_PORT="587"
  export EMAIL_HOST_USER="..."
  export EMAIL_HOST_PASSWORD="..."
  export EMAIL_USE_TLS="1"
  export DEFAULT_FROM_EMAIL="Flashcards <noreply@example.com>"
  ```

  Use `EMAIL_USE_SSL=1` instead of `EMAIL_USE_TLS` for an implicit TLS
  connection; do not enable both. Reset links expire after one hour
  (`PASSWORD_RESET_TIMEOUT`).
- Users who have not saved and verified an email address cannot request a reset
  themselves. They can add or resend verification after logging in, or an
  administrator can set it in Django admin.

### Automatic logout

- After **5 minutes without activity**, the page logs out automatically and the
  login page explains why. Activity in any open tab keeps the session alive.
- Tabs coordinate with each other: opening a second tab or reloading keeps you
  logged in, while closing the final tab ends the session. Reopening after all
  tabs are closed requires logging in again.
- Session cookies are browser-session cookies, so closing the browser also ends
  the session.
- The server also enforces the idle limit. The timeout can be changed with
  `SESSION_IDLE_TIMEOUT` in `flashcards/settings.py`; the tab login handshake
  window is `TAB_SESSION_HANDSHAKE_TIMEOUT`.

## Arabic audio

Arabic answers have a **Listen** button in the card list and study views.

### Setup

`gTTS` is included in `requirements.txt`, so installing the dependencies is all
that is required:

```bash
bin/pip install -r requirements.txt
```

No API key, migration, or `MEDIA_URL` configuration is needed. Django serves
the audio through an authenticated view rather than exposing the cache folder
as public media.

By default, generated MP3s are stored in `media/arabic_audio/`. The folder is
created automatically and is excluded from git. To use a different location,
add this to `settings.py`:

```python
ARABIC_AUDIO_CACHE_DIR = "/var/lib/flashcards/arabic_audio"
```

The Django process needs outbound internet access for the first generation and
write access to the cache directory.

### How it works

1. The **Listen** button requests `/audio/<card-id>/`.
2. The endpoint requires login and checks that the card belongs to the current
   user, so users cannot fetch another user's audio.
3. It uses `answer_vocalized` when that contains Arabic; otherwise it uses
   `answer`. Cards without an Arabic answer return `404`.
4. The text is hashed with SHA-256. If
   `media/arabic_audio/<sha256>.mp3` already exists, that file is served
   immediately.
5. On a cache miss, gTTS requests an MP3 from Google Translate's text-to-speech
   service. The result is written atomically into the cache and then served.
6. Browsers cache the response privately for 24 hours. The server-side cache
   persists until its files are deleted; editing the Arabic text creates a new
   cache entry.
7. If Google/gTTS is unavailable, the endpoint returns `503` and the button
   shows **Try again**.

No Google Cloud API key is required. Audio generation is not local, so the
first play needs internet access. Later plays use the cached MP3. If JavaScript
is unavailable, the page falls back to the browser's native audio control.

### Troubleshooting

- The first play can take a few seconds while Google generates the audio.
- A **Try again** button or a `503` response usually means the server could not
  reach Google; check outbound network access and the Django logs.
- To regenerate all audio, stop the app and delete `media/arabic_audio/`.
- Make sure the user running Django can write to the cache directory.
- Cached MP3s are not pruned automatically.

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
