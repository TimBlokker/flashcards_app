# cards/forms.py
import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import BOXES, Card, DictionaryEntry, StudyReminder
from .card_import import parse_card_import
from .arabic import contains_arabic, contains_latin


class CardForm(forms.ModelForm):
    dictionary_entry_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    answer_vocalized = forms.CharField(
        required=False,
        label="Vocalized answer (optional)",
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Filled from a dictionary suggestion when available; you can edit it.",
    )

    class Meta:
        model = Card
        fields = ["question", "answer", "answer_vocalized", "box"]
        labels = {
            "question": "Question",
            "answer": "Answer / translation",
        }
        help_texts = {
            "question": "Write the question in English.",
            "answer": "Write the answer in Arabic.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user or self.instance.user
        self._dictionary_entry = None

    def clean(self):
        cleaned_data = super().clean()
        entry_id = cleaned_data.get("dictionary_entry_id")
        if entry_id:
            self._dictionary_entry = DictionaryEntry.objects.filter(
                pk=entry_id,
                language_pair="en-ar",
            ).first()
            if self._dictionary_entry is None:
                self.add_error("dictionary_entry_id", "Choose a valid dictionary suggestion.")
            else:
                cleaned_data["question"] = self._dictionary_entry.headword
                cleaned_data["answer"] = self._dictionary_entry.translation
                if not cleaned_data.get("answer_vocalized"):
                    cleaned_data["answer_vocalized"] = (
                        self._dictionary_entry.vocalized_translation
                    )
        question = cleaned_data.get("question")
        answer = cleaned_data.get("answer")
        if question and not contains_latin(question):
            self.add_error("question", "Question must contain English text.")
        if answer and not contains_arabic(answer):
            self.add_error("answer", "Answer must contain Arabic text.")
        if question and self.user is not None:
            duplicates = Card.objects.filter(
                user=self.user,
                question=question,
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error(
                    "question",
                    "A card with this question already exists.",
                )
        return cleaned_data

    def save(self, commit=True):
        card = super().save(commit=False)
        if commit:
            card.save()
            self.save_m2m()
        return card


class CardCheckForm(forms.Form):
    card_id = forms.IntegerField(required=True)
    solved = forms.BooleanField(required=False)

class CardUpdateForm(forms.Form):
    card_id = forms.IntegerField(required=True)
    solved = forms.BooleanField(required=False)

class CardImportForm(forms.Form):
    file = forms.FileField(label="CSV or Excel file")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned_data = super().clean()
        uploaded_file = cleaned_data.get("file")
        if uploaded_file is None:
            return cleaned_data

        try:
            rows = parse_card_import(uploaded_file)
        except ValidationError as error:
            self.add_error("file", error)
            return cleaned_data

        max_answer_length = Card._meta.get_field("answer").max_length
        oversized_rows = [
            row_number for row_number, question, answer in rows
            if len(question) > Card._meta.get_field("question").max_length
            or len(answer) > max_answer_length
        ]
        if oversized_rows:
            row_list = ", ".join(str(row) for row in oversized_rows[:20])
            if len(oversized_rows) > 20:
                row_list += ", ..."
            self.add_error(
                "file",
                f"Text exceeds the card field length in row(s): {row_list}. "
                "No cards were imported.",
            )
            return cleaned_data

        questions = [question for _, question, _ in rows]
        existing_questions = set()
        for offset in range(0, len(questions), 500):
            query = Q()
            for question in questions[offset:offset + 500]:
                query |= Q(question=question)
            existing_questions.update(
                Card.objects.filter(user=self.user)
                .filter(query)
                .values_list("question", flat=True)
            )

        import_rows = []
        duplicate_questions = []
        duplicate_count = 0
        seen_questions = set()
        for row_number, question, answer in rows:
            if question in seen_questions or question in existing_questions:
                duplicate_count += 1
                if question not in duplicate_questions:
                    duplicate_questions.append(question)
                continue
            seen_questions.add(question)
            import_rows.append((row_number, question, answer))

        cleaned_data["rows"] = import_rows
        cleaned_data["duplicate_count"] = duplicate_count
        cleaned_data["duplicate_questions"] = duplicate_questions
        return cleaned_data


class StudyReminderForm(forms.ModelForm):
    class Meta:
        model = StudyReminder
        fields = ["phone_number", "reminder_time", "enabled"]
        labels = {
            "phone_number": "WhatsApp number",
            "reminder_time": "Remind me at",
            "enabled": "Send automatic reminders",
        }
        help_texts = {
            "phone_number": "Include the country code, e.g. +31 6 1234 5678.",
            "reminder_time": "Local time.",
        }
        widgets = {
            "reminder_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean_phone_number(self):
        value = (self.cleaned_data.get("phone_number") or "").strip()
        if not value:
            return ""
        normalized = re.sub(r"[\s().-]", "", value)
        if not re.fullmatch(r"\+[1-9]\d{6,14}", normalized):
            raise ValidationError(
                "Enter the number in international format, e.g. +31612345678."
            )
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("enabled") and not cleaned_data.get("phone_number"):
            self.add_error(
                "phone_number",
                "Add a number to receive WhatsApp reminders.",
            )
        return cleaned_data