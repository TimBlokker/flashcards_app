from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.generic import (
    ListView,
    CreateView,
    UpdateView,
    DeleteView,
    View,
)
from django.views.generic.edit import FormView
from .email_verification import send_verification_email
from .models import (
    BOXES,
    Card,
    DictionaryEntry,
    EmailVerification,
    StudyReminder,
)
from .levels import level_ladder
from .progress import (
    due_card_count,
    progress_summary,
    record_card_review,
    streak_message,
)
from .audio import ArabicAudioError, get_or_create_arabic_audio
from .reminders import build_reminder_message
from .whatsapp import whatsapp_deep_link
import random
from io import BytesIO
from .forms import (
    AccountEmailForm,
    CardForm,
    CardImportForm,
    SignUpForm,
    StudyReminderForm,
)
from .forms import CardCheckForm
from .arabic import contains_arabic, vocalize_arabic


def csrf_failure(request, reason=""):
    if request.path_info != reverse("login"):
        return default_csrf_failure(request, reason=reason)

    if request.user.is_authenticated:
        return redirect("home")

    messages.error(
        request,
        "Your login form could not be verified. Please try again.",
    )
    return redirect("login")


def with_card_vocalizations(cards):
    question_vocalization = DictionaryEntry.objects.filter(
        language_pair="en-ar",
        translation=OuterRef("question"),
    ).exclude(
        vocalized_translation="",
    ).order_by("pk").values("vocalized_translation")[:1]
    cards_with_dictionary_vocalizations = cards.annotate(
        question_vocalized=Subquery(question_vocalization),
    )
    return cards_with_dictionary_vocalizations


def exported_card_columns(card):
    """Return the card as (question, answer) with English before Arabic.

    Card 0015 already reorients stored rows where the question is Arabic and
    the answer is not. Export applies the same rule so old, unreoriented rows
    still come out with an English question and an Arabic answer.
    """
    if (
        card.question
        and card.answer
        and contains_arabic(card.question)
        and not contains_arabic(card.answer)
    ):
        return card.answer, card.question
    return card.question, card.answer


def dictionary_suggestions(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    results = DictionaryEntry.objects.filter(
        language_pair="en-ar",
        search_key__startswith=query.casefold(),
    ).order_by("search_key", "headword").values(
        "id",
        "headword",
        "translation",
        "vocalized_translation",
    )[:10]
    return JsonResponse({"results": list(results)})


class EmailSetupLoginView(DjangoLoginView):
    """Prompt accounts without an email address to add one after login."""

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.get_user()
        if (user.email or "").strip():
            self.request.session.pop("email_setup_required", None)
            return response

        self.request.session["email_setup_required"] = True
        return redirect("complete-email")


class SignUpView(FormView):
    template_name = "registration/signup.html"
    form_class = SignUpForm
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
            if user.get_username() == settings.LEGACY_CARD_USERNAME:
                Card.objects.filter(user__isnull=True).update(user=user)
        login(self.request, user)
        if send_verification_email(self.request, user):
            messages.success(
                self.request,
                f"We sent a verification link to {user.email}.",
            )
        else:
            messages.warning(
                self.request,
                "Your account was created, but we couldn't send the "
                "verification email. Resend it from Account settings.",
            )
        return super().form_valid(form)


class AccountSettingsView(LoginRequiredMixin, FormView):
    template_name = "registration/account_settings.html"
    form_class = AccountEmailForm
    success_url = reverse_lazy("account-settings")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["email_verification"] = getattr(
            self.request.user,
            "email_verification",
            None,
        )
        context["email_settings_page"] = True
        return context

    def form_valid(self, form):
        previous_email = (self.request.user.email or "").casefold()
        user = form.save()
        self.request.session.pop("email_setup_required", None)
        email_changed = (user.email or "").casefold() != previous_email
        verification = getattr(user, "email_verification", None)
        was_verified = bool(verification and verification.is_verified)
        sent = send_verification_email(self.request, user)

        if email_changed:
            if sent:
                messages.success(
                    self.request,
                    "Email updated. Check your inbox to verify the new address.",
                )
            else:
                messages.warning(
                    self.request,
                    "Email updated, but we couldn't send the verification "
                    "message. Try resending it below.",
                )
        elif not was_verified and sent:
            messages.success(
                self.request,
                f"Verification email sent to {user.email}.",
            )
        else:
            messages.success(self.request, "Your email address is up to date.")
        return super().form_valid(form)


class CompleteEmailView(LoginRequiredMixin, FormView):
    template_name = "cards/auth/complete_email.html"
    form_class = AccountEmailForm
    success_url = reverse_lazy("home")

    def dispatch(self, request, *args, **kwargs):
        if (
            request.user.is_authenticated
            and (request.user.email or "").strip()
        ):
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["email_setup"] = True
        return context

    def form_valid(self, form):
        user = form.save()
        self.request.session.pop("email_setup_required", None)
        if send_verification_email(self.request, user):
            messages.success(
                self.request,
                f"Email saved. We sent a verification link to {user.email}.",
            )
        else:
            messages.warning(
                self.request,
                "Email saved, but we couldn't send the verification email. "
                "Resend it from Account settings.",
            )
        return super().form_valid(form)


class ResendEmailVerificationView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if not (request.user.email or "").strip():
            return redirect("complete-email")

        verification = getattr(request.user, "email_verification", None)
        if verification and verification.is_verified:
            messages.info(request, "Your email address is already verified.")
        elif send_verification_email(request, request.user):
            messages.success(
                request,
                f"Verification email sent to {request.user.email}.",
            )
        else:
            messages.error(
                request,
                "We couldn't send the verification email. Please try again.",
            )
        return redirect("account-settings")


class VerifyEmailView(View):
    template_name = "cards/auth/email_verification_result.html"

    def get(self, request, token, *args, **kwargs):
        verification = (
            EmailVerification.objects.filter(token=token)
            .select_related("user")
            .first()
        )
        context = {"verified": False, "reason": "invalid"}

        if verification is None:
            return render(request, self.template_name, context)

        user = verification.user
        email = verification.email
        if email.casefold() != (user.email or "").casefold():
            context = {
                "verified": False,
                "reason": "changed",
                "email": email,
            }
        elif verification.verified_at:
            context = {
                "verified": True,
                "reason": "already",
                "email": email,
            }
        elif verification.is_expired:
            context = {
                "verified": False,
                "reason": "expired",
                "email": email,
            }
        else:
            verification.verified_at = timezone.now()
            verification.save(update_fields=["verified_at"])
            context = {
                "verified": True,
                "reason": "verified",
                "email": email,
            }

        return render(request, self.template_name, context)


class IdleLogoutView(View):
    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect(f"{reverse('login')}?reason=idle")


class TabSessionExpiredView(View):
    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect(f"{reverse('login')}?reason=expired")


class SessionHeartbeatView(View):
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return HttpResponse(status=401)
        return HttpResponse(status=204)


class TabSessionStartView(View):
    def post(self, request, *args, **kwargs):
        request.session.pop("tab_session_handshake", None)
        return HttpResponse(status=204)


class CardAudioView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        card = get_object_or_404(Card, pk=pk, user=request.user)
        text = (card.answer_vocalized or "").strip()
        if not contains_arabic(text):
            text = (card.answer or "").strip()
        if not contains_arabic(text):
            raise Http404("This card has no Arabic answer.")

        try:
            audio_path = get_or_create_arabic_audio(text)
        except ArabicAudioError:
            return HttpResponse(
                "Audio is unavailable right now. Please try again.",
                status=503,
                content_type="text/plain; charset=utf-8",
            )

        response = FileResponse(
            audio_path.open("rb"),
            content_type="audio/mpeg",
        )
        response["Cache-Control"] = "private, max-age=86400"
        response["Content-Length"] = str(audio_path.stat().st_size)
        return response


class CardListView(LoginRequiredMixin, ListView):
    model = Card
    template_name = "cards/card_list.html"
    context_object_name = "card_list"

    def get_queryset(self):
        return with_card_vocalizations(
            Card.objects.filter(
                user=self.request.user,
            ).order_by("box", "-date_created")
        )


class HomeView(LoginRequiredMixin, View):
    template_name = "cards/home.html"

    def get(self, request, *args, **kwargs):
        summary = progress_summary(request.user)
        request._cards_progress_summary = summary
        return render(
            request,
            self.template_name,
            {
                **summary,
                "due_count": due_card_count(request.user),
            },
        )


class CardVocalizeMissingView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        cards = Card.objects.filter(
            user=request.user,
            answer_vocalized="",
        ).only("pk", "answer")
        vocalized_count = 0
        non_arabic_count = 0
        failure_count = 0

        for card in cards.iterator():
            if not contains_arabic(card.answer):
                non_arabic_count += 1
                continue

            vocalized_answer = vocalize_arabic(card.answer)
            if not vocalized_answer:
                failure_count += 1
                continue

            vocalized_count += Card.objects.filter(
                pk=card.pk,
                user=request.user,
                answer_vocalized="",
            ).update(answer_vocalized=vocalized_answer)

        if vocalized_count or non_arabic_count or failure_count:
            card_label = "card" if vocalized_count == 1 else "cards"
            message = f"Vocalized {vocalized_count} {card_label}."
            if non_arabic_count:
                answer_label = "answer" if non_arabic_count == 1 else "answers"
                message += (
                    f" Skipped {non_arabic_count} {answer_label} "
                    "without Arabic text."
                )
            if failure_count:
                answer_label = "answer" if failure_count == 1 else "answers"
                message += (
                    f" Mishkal could not vocalize {failure_count} Arabic "
                    f"{answer_label}."
                )
                messages.warning(request, message)
            else:
                messages.success(request, message)
        else:
            messages.success(request, "No cards are missing a vocalized answer.")

        return redirect("card-list")


class CardCreateView(LoginRequiredMixin, CreateView):
    model = Card
    form_class = CardForm
    success_url = reverse_lazy("card-create")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)


class CardImportView(LoginRequiredMixin, FormView):
    template_name = "cards/card_import.html"
    form_class = CardImportForm
    success_url = reverse_lazy("card-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        answers = list({answer for _, _, answer in form.cleaned_data["rows"]})
        vocalized_answers = {}
        for offset in range(0, len(answers), 500):
            answer_batch = answers[offset:offset + 500]
            entries = DictionaryEntry.objects.filter(
                language_pair="en-ar",
                translation__in=answer_batch,
            ).exclude(
                vocalized_translation="",
            ).order_by("pk").values_list(
                "translation",
                "vocalized_translation",
            )
            for translation, vocalized_translation in entries:
                vocalized_answers.setdefault(translation, vocalized_translation)

        cards = []
        for row_number, question, answer in form.cleaned_data["rows"]:
            cards.append(Card(
                user=self.request.user,
                question=question,
                answer=answer,
                box=BOXES[0],
                answer_vocalized=vocalized_answers.get(answer, ""),
            ))

        if cards:
            try:
                with transaction.atomic():
                    Card.objects.bulk_create(cards, batch_size=500)
            except IntegrityError:
                form.add_error(
                    "file",
                    "A card conflicts with an existing card. No cards were imported; "
                    "check for duplicate questions and try again.",
                )
                return self.form_invalid(form)

        duplicate_questions = form.cleaned_data["duplicate_questions"]
        if duplicate_questions:
            duplicate_list = ", ".join(duplicate_questions)
            imported_label = "card" if len(cards) == 1 else "cards"
            duplicate_label = (
                "duplicate card" if form.cleaned_data["duplicate_count"] == 1
                else "duplicate cards"
            )
            messages.warning(
                self.request,
                f"Imported {len(cards)} {imported_label}. Skipped "
                f"{form.cleaned_data['duplicate_count']} "
                f"{duplicate_label}: {duplicate_list}.",
            )
        else:
            messages.success(self.request, f"Imported {len(cards)} cards.")
        return super().form_valid(form)


class CardExportView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        from openpyxl import Workbook

        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet(title="Flashcards")
        worksheet.append(["question", "answer"])

        cards = Card.objects.filter(user=request.user).order_by("pk").iterator()
        for card in cards:
            question, answer = exported_card_columns(card)
            if question:
                worksheet.append([question, answer])

        output = BytesIO()
        workbook.save(output)
        response = HttpResponse(
            output.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = 'attachment; filename="flashcards.xlsx"'
        return response


class CardUpdateView(LoginRequiredMixin, UpdateView):
    model = Card
    form_class = CardForm
    template_name = "cards/card_form.html"
    success_url = reverse_lazy("card-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_queryset(self):
        return Card.objects.filter(user=self.request.user)


class CardDeleteView(LoginRequiredMixin, DeleteView):
    model = Card
    template = "cards/card_confirm_delete.html"
    success_url = reverse_lazy("card-list")

    def get_queryset(self):
        return Card.objects.filter(user=self.request.user)


class BoxView(CardListView):
    template_name = "cards/box.html"
    def get_queryset(self):
        return with_card_vocalizations(
            Card.objects.filter(
                user=self.request.user,
                box=self.kwargs["box_num"],
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["box_number"] = self.kwargs["box_num"]
        context["object_list"] = self.get_queryset()
        return context


class CardRepeatView(CardListView):
    template_name = "cards/repeat.html"
    form_class = CardCheckForm

    def get_queryset(self):
        object_list = [
            card
            for card in with_card_vocalizations(
                Card.objects.filter(
                    user=self.request.user,
                )
            )
            if card.to_repeat
        ]
        return object_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        object_list = context["object_list"]
        context["card_count"] = len(object_list)
        context["flipped"] = self.request.GET.get("flip") == "1"
        context["learning_mode"] = True

        if object_list:
            context["check_card"] = random.choice(object_list)

        return context

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST)
        if form.is_valid():
            card = get_object_or_404(
                Card,
                id=form.cleaned_data["card_id"],
                user=request.user,
            )
            card.move(form.cleaned_data["solved"])
            progress, streak_awarded = record_card_review(
                request.user,
                form.cleaned_data["solved"],
            )
            if streak_awarded:
                messages.success(request, streak_message(progress))
            card.to_repeat
        return redirect(request.META.get("HTTP_REFERER") or "todo-list")


class CardPracticeView(LoginRequiredMixin, ListView):
    template_name = "cards/repeat.html"
    form_class = CardCheckForm

    def get_session_key(self):
        box_num = self.kwargs.get("box_num", "all")
        return f"card_practice_{self.request.user.pk}_{box_num}"

    def get_flip_session_key(self):
        return f"{self.get_session_key()}_flipped"

    def get_cards(self):
        cards = Card.objects.filter(user=self.request.user)
        if "box_num" in self.kwargs:
            cards = cards.filter(box=self.kwargs["box_num"])
        return cards

    def get_card_ids(self):
        key = self.get_session_key()
        card_ids = self.request.session.get(key)
        if card_ids is None:
            card_ids = list(self.get_cards().values_list("pk", flat=True))
            random.shuffle(card_ids)
            self.request.session[key] = card_ids
        else:
            available_ids = set(
                self.get_cards().values_list("pk", flat=True)
            )
            card_ids = [card_id for card_id in card_ids if card_id in available_ids]
            self.request.session[key] = card_ids
        return card_ids

    def get(self, request, *args, **kwargs):
        if request.GET.get("start") == "1":
            key = self.get_session_key()
            card_ids = list(self.get_cards().values_list("pk", flat=True))
            random.shuffle(card_ids)
            request.session[key] = card_ids
            request.session[self.get_flip_session_key()] = False
            return redirect(request.path)
        if "flip" in request.GET:
            request.session[self.get_flip_session_key()] = (
                request.GET.get("flip") == "1"
            )
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        card_ids = self.get_card_ids()
        if not card_ids:
            return []
        cards = with_card_vocalizations(
            self.get_cards().filter(pk=card_ids[0])
        )
        return list(cards)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        object_list = context["object_list"]
        context["check_card"] = object_list[0] if object_list else None
        context["practice_only"] = True
        context["remaining_count"] = len(self.get_card_ids())
        context["total_cards"] = self.get_cards().count()
        context["flipped"] = self.request.session.get(
            self.get_flip_session_key(),
            False,
        )
        context["learning_mode"] = True
        if "box_num" in self.kwargs:
            context["box_number"] = self.kwargs["box_num"]
        else:
            context["practice_title"] = "Practice all cards"
        return context

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST)
        if form.is_valid():
            card = get_object_or_404(
                self.get_cards(),
                pk=form.cleaned_data["card_id"],
            )
            key = self.get_session_key()
            card_ids = self.get_card_ids()
            if not card_ids or card.pk != card_ids[0]:
                return redirect(request.path)
            card_ids.remove(card.pk)
            if not form.cleaned_data["solved"]:
                card_ids.append(card.pk)
            request.session[key] = card_ids
            progress, streak_awarded = record_card_review(
                request.user,
                form.cleaned_data["solved"],
            )
            if streak_awarded:
                messages.success(request, streak_message(progress))
        return redirect(request.path)


class StudyProgressView(LoginRequiredMixin, View):
    template_name = "cards/progress.html"

    def _context(self, request, form=None):
        summary = progress_summary(request.user)
        request._cards_progress_summary = summary
        reminder, _ = StudyReminder.objects.get_or_create(user=request.user)
        due_count = due_card_count(request.user)
        preview = build_reminder_message(
            request.user,
            summary=summary,
            due_count=due_count,
        )
        return {
            **summary,
            "levels": level_ladder(summary["points"]),
            "reminder": reminder,
            "reminder_form": form or StudyReminderForm(instance=reminder),
            "due_count": due_count,
            "reminder_preview": preview,
            "whatsapp_link": (
                whatsapp_deep_link(reminder.phone_number, preview)
                if reminder.phone_number
                else ""
            ),
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self._context(request))

    def post(self, request, *args, **kwargs):
        reminder, _ = StudyReminder.objects.get_or_create(user=request.user)
        form = StudyReminderForm(request.POST, instance=reminder)
        if form.is_valid():
            form.save()
            messages.success(request, "WhatsApp reminder saved.")
            return redirect("progress")
        return render(request, self.template_name, self._context(request, form=form))
