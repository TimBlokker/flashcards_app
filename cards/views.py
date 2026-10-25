from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.urls import reverse_lazy
from django.views.csrf import csrf_failure as django_csrf_failure
from django.views.generic import (
    ListView,
    CreateView,
    UpdateView,
    DeleteView,
    View,
)
from django.views.generic.edit import FormView
from .models import BOXES, Card, DictionaryEntry
import random
from io import BytesIO
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from .forms import (
    CardForm,
    CardImportForm,
)
from .forms import CardCheckForm
from .arabic import contains_arabic, vocalize_arabic


def csrf_failure(request, reason=""):
    return django_csrf_failure(request, reason)


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


class SignUpView(FormView):
    template_name = "registration/signup.html"
    form_class = UserCreationForm
    success_url = reverse_lazy("card-list")

    def form_valid(self, form):
        with transaction.atomic():
            user = form.save()
            if user.get_username() == settings.LEGACY_CARD_USERNAME:
                Card.objects.filter(user__isnull=True).update(user=user)
        login(self.request, user)
        return super().form_valid(form)


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
            card.to_repeat
        return redirect(request.META.get("HTTP_REFERER"))


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
        return redirect(request.path)
