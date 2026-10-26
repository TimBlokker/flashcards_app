import json
import re
import time
from html.parser import HTMLParser
from io import BytesIO
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test import RequestFactory
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import timezone
from django.urls import NoReverseMatch, reverse

from cards.forms import CardForm
from cards.levels import LEVELS, level_for_points, level_progress
from cards.models import (
	Card,
	DictionaryEntry,
	EmailVerification,
	StudyProgress,
	StudyReminder,
)
from cards.progress import progress_summary
from cards.reminders import build_reminder_message
from cards.whatsapp import WhatsAppNotConfigured, whatsapp_deep_link


class ArabicDisplayParser(HTMLParser):
	def __init__(self):
		super().__init__()
		self.displays = []
		self._active_display = None
		self._depth = 0

	def handle_starttag(self, tag, attrs):
		attributes = dict(attrs)
		if self._active_display is not None:
			self._depth += 1
		elif "arabic-display" in attributes.get("class", "").split():
			self._active_display = {
				"id": attributes.get("id"),
				"text": [],
			}
			self._depth = 1

	def handle_data(self, data):
		if self._active_display is not None:
			self._active_display["text"].append(data)

	def handle_endtag(self, tag):
		if self._active_display is None:
			return
		self._depth -= 1
		if self._depth == 0:
			self.displays.append({
				"id": self._active_display["id"],
				"text": " ".join("".join(self._active_display["text"]).split()),
			})
			self._active_display = None


def arabic_display_texts(response, element_id):
	parser = ArabicDisplayParser()
	parser.feed(response.content.decode())
	return [
		display["text"]
		for display in parser.displays
		if display["id"] == element_id
	]


class DictionarySuggestionTests(TestCase):
	def setUp(self):
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Apple",
			translation="التفاحة",
			vocalized_translation="التُّفَّاحَةُ",
		)
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Application",
			translation="تطبيق",
			vocalized_translation="تَطْبِيقٌ",
		)
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Water",
			translation="الماء",
			vocalized_translation="الْمَاءُ",
		)

	def test_suggests_prefix_matches_with_translations(self):
		response = self.client.get(reverse("dictionary-suggest"), {"q": "app"})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			response.json()["results"],
			[
				{"id": 1, "headword": "Apple", "translation": "التفاحة", "vocalized_translation": "التُّفَّاحَةُ"},
				{"id": 2, "headword": "Application", "translation": "تطبيق", "vocalized_translation": "تَطْبِيقٌ"},
			],
		)

	def test_does_not_search_for_one_character(self):
		response = self.client.get(reverse("dictionary-suggest"), {"q": "a"})

		self.assertEqual(response.json(), {"results": []})

	def test_dictionary_suggestion_fills_question_and_answer(self):
		self.client.force_login(get_user_model().objects.create_user("card-owner"))
		response = self.client.post(
			reverse("card-create"),
			{
				"question": "Apple",
				"answer": "التفاحة",
				"dictionary_entry_id": DictionaryEntry.objects.get(headword="Apple").id,
				"box": 1,
			},
		)

		self.assertRedirects(response, reverse("card-create"))
		card = Card.objects.get(question="Apple")
		self.assertEqual(card.answer, "التفاحة")
		self.assertEqual(card.answer_vocalized, "التُّفَّاحَةُ")
		self.assertEqual(card.user.username, "card-owner")

	def test_dictionary_suggestion_allows_custom_vocalized_answer(self):
		self.client.force_login(get_user_model().objects.create_user("custom-vocalization-owner"))
		entry = DictionaryEntry.objects.get(headword="Apple")
		response = self.client.post(
			reverse("card-create"),
			{
				"question": "Apple",
				"answer": "التفاحة",
				"answer_vocalized": "تَفَّاحَةٌ",
				"dictionary_entry_id": entry.id,
				"box": 1,
			},
		)

		self.assertRedirects(response, reverse("card-create"))
		card = Card.objects.get(question="Apple")
		self.assertEqual(card.answer_vocalized, "تَفَّاحَةٌ")


class AccountDeckTests(TestCase):
	def create_card(self, **kwargs):
		return Card.objects.create(
			answer=kwargs.pop("answer", "legacy-secret-answer"),
			question=kwargs.pop("question", "question"),
			**kwargs,
		)

	def test_card_pages_require_login(self):
		response = self.client.get(reverse("card-list"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?next={reverse('card-list')}",
		)
		self.assertRedirects(
			self.client.get(reverse("home")),
			f"{reverse('login')}?next={reverse('home')}",
		)

	def test_login_redirects_to_home(self):
		user = get_user_model().objects.create_user(
			"login-redirect-user",
			email="login-redirect@example.com",
			password="Correct-Horse-92!",
		)

		response = self.client.post(
			reverse("login"),
			{"username": user.username, "password": "Correct-Horse-92!"},
		)

		self.assertRedirects(response, reverse("home"))

	def test_login_accepts_current_csrf_cookie_token(self):
		client = self.client_class(enforce_csrf_checks=True)
		user = get_user_model().objects.create_user(
			"csrf-cookie-login-user",
			email="csrf-cookie@example.com",
			password="Correct-Horse-92!",
		)
		login_page = client.get(reverse("login"))
		csrf_token = client.cookies["csrftoken"].value

		self.assertContains(login_page, "cards/js/login_csrf.js")
		response = client.post(
			reverse("login"),
			{
				"username": user.username,
				"password": "Correct-Horse-92!",
				"csrfmiddlewaretoken": csrf_token,
			},
		)

		self.assertRedirects(response, reverse("home"))

	def test_invalid_csrf_on_login_refreshes_form_for_logged_out_user(self):
		client = self.client_class(enforce_csrf_checks=True)

		response = client.post(reverse("login"))

		self.assertRedirects(
			response,
			reverse("login"),
			fetch_redirect_response=False,
		)
		login_page = client.get(reverse("login"))
		self.assertContains(
			login_page,
			"Your login form could not be verified. Please try again.",
		)

	def test_invalid_csrf_on_login_returns_existing_session_to_app(self):
		client = self.client_class(enforce_csrf_checks=True)
		user = get_user_model().objects.create_user("already-logged-in")
		client.force_login(user)

		response = client.post(reverse("login"))

		self.assertRedirects(response, reverse("home"))
		self.assertTrue(response.wsgi_request.user.is_authenticated)

	def test_new_account_creates_language_neutral_cards_without_category(self):
		self.client.post(
			reverse("signup"),
			{
				"username": "new-user",
				"email": "new-user@example.com",
				"password1": "Correct-Horse-92!",
				"password2": "Correct-Horse-92!",
			},
		)
		user = get_user_model().objects.get(username="new-user")

		create_response = self.client.get(reverse("card-create"))
		self.assertContains(create_response, 'name="question"')
		self.assertContains(create_response, 'name="answer"')
		self.assertContains(create_response, 'name="answer_vocalized"')
		self.assertNotContains(create_response, 'name="category"')
		self.assertNotContains(create_response, 'name="language"')
		self.assertNotContains(create_response, 'name="question_en"')

		response = self.client.post(
			reverse("card-create"),
			{
				"question": "Apple",
				"answer": "التفاحة",
				"box": 1,
			},
		)
		self.assertRedirects(response, reverse("card-create"))
		self.assertEqual(Card.objects.get(user=user).question, "Apple")

	def test_signup_shows_password_validation_errors(self):
		response = self.client.get(reverse("signup"))

		self.assertContains(response, "Password requirements")
		self.assertContains(response, "at least 8 characters")
		self.assertContains(response, "commonly used password")
		self.assertContains(response, "entirely numeric")
		self.assertContains(response, "too similar to your other personal information")

		response = self.client.post(
			reverse("signup"),
			{
				"username": "new-user",
				"email": "short-password@example.com",
				"password1": "short",
				"password2": "short",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "This password is too short")
		self.assertContains(response, 'role="alert"')
		self.assertFalse(
			get_user_model().objects.filter(username="new-user").exists()
		)

	def test_card_form_prevents_duplicate_question_per_user(self):
		user = get_user_model().objects.create_user("owner")
		other_user = get_user_model().objects.create_user("other")
		Card.objects.create(user=user, question="question", answer="ترجمة")
		duplicate_form = CardForm(
			data={
				"question": "question",
				"answer": "ترجمة مختلفة",
				"box": 1,
			},
			user=user,
		)
		self.assertFalse(duplicate_form.is_valid())
		self.assertIn("already exists", str(duplicate_form.errors))

		other_user_form = CardForm(
			data={
				"question": "question",
				"answer": "ترجمة",
				"box": 1,
			},
			user=other_user,
		)
		self.assertTrue(other_user_form.is_valid())

	def test_card_question_form_accepts_up_to_500_characters(self):
		valid_form = CardForm(data={
			"question": "q" * 500,
			"answer": "ترجمة",
			"box": 1,
		})
		invalid_form = CardForm(data={
			"question": "q" * 501,
			"answer": "ترجمة",
			"box": 1,
		})

		self.assertTrue(valid_form.is_valid(), valid_form.errors)
		self.assertFalse(invalid_form.is_valid())

	def test_card_form_rejects_question_without_english_text(self):
		form = CardForm(data={
			"question": "مرحبا",
			"answer": "مرحبا",
			"box": 1,
		})

		self.assertFalse(form.is_valid())
		self.assertIn("Question must contain English text.", str(form.errors))

	def test_card_form_rejects_answer_without_arabic_text(self):
		form = CardForm(data={
			"question": "Haus",
			"answer": "maison",
			"box": 1,
		})

		self.assertFalse(form.is_valid())
		self.assertIn("Answer must contain Arabic text.", str(form.errors))

	def test_card_update_rejects_an_answer_without_arabic_text(self):
		user = get_user_model().objects.create_user("update-language-owner")
		card = Card.objects.create(
			user=user,
			question="Good morning",
			answer="صباح الخير",
		)
		self.client.force_login(user)

		response = self.client.post(
			reverse("card-update", args=[card.pk]),
			{
				"question": card.question,
				"answer": "good morning",
				"answer_vocalized": "",
				"box": card.box,
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Answer must contain Arabic text.")
		card.refresh_from_db()
		self.assertEqual(card.answer, "صباح الخير")

	def test_database_prevents_duplicate_question_per_user(self):
		user = get_user_model().objects.create_user("unique-question-owner")
		Card.objects.create(user=user, question="question", answer="translation")
		with self.assertRaises(IntegrityError), transaction.atomic():
			Card.objects.create(
				user=user,
				question="question",
				answer="different translation",
			)

	def test_legacy_username_claims_existing_cards_at_signup(self):
		legacy_card = self.create_card()

		response = self.client.post(
			reverse("signup"),
			{
				"username": "legacyUser",
				"email": "legacy-user@example.com",
				"password1": "Correct-Horse-92!",
				"password2": "Correct-Horse-92!",
			},
		)

		self.assertRedirects(response, reverse("home"))
		user = get_user_model().objects.get(username="legacyUser")
		self.assertTrue(user.check_password("Correct-Horse-92!"))
		legacy_card.refresh_from_db()
		self.assertEqual(legacy_card.user, user)
		self.assertIn("_auth_user_id", self.client.session)

	def test_other_accounts_start_with_empty_private_decks(self):
		legacy_card = self.create_card()
		self.client.post(
			reverse("signup"),
			{
				"username": "legacyUser",
				"email": "legacy-user@example.com",
				"password1": "Correct-Horse-92!",
				"password2": "Correct-Horse-92!",
			},
		)
		legacy_user = get_user_model().objects.get(username="legacyUser")

		other_client = self.client_class()
		response = other_client.post(
			reverse("signup"),
			{
				"username": "anotherUser",
				"email": "another-user@example.com",
				"password1": "A-Different-Password-75!",
				"password2": "A-Different-Password-75!",
			},
		)

		self.assertEqual(response.status_code, 302)
		other_user = get_user_model().objects.get(username="anotherUser")
		self.assertEqual(
			Card.objects.filter(user=other_user).count(),
			0,
		)

		response = other_client.get(reverse("card-list"))
		self.assertNotContains(response, "legacy-secret-answer")
		self.assertNotContains(response, "<mark>1</mark>")

		self.client.force_login(legacy_user)
		response = self.client.get(reverse("card-list"))
		self.assertContains(response, "legacy-secret-answer")
		legacy_card.refresh_from_db()
		self.assertEqual(legacy_card.user, legacy_user)

	def test_account_cannot_edit_or_delete_another_users_card(self):
		owner = get_user_model().objects.create_user("owner")
		other_user = get_user_model().objects.create_user("other")
		card = self.create_card(user=owner)
		self.client.force_login(other_user)

		self.assertEqual(
			self.client.get(reverse("card-update", args=[card.pk])).status_code,
			404,
		)
		self.assertEqual(
			self.client.post(reverse("card-delete", args=[card.pk])).status_code,
			404,
		)
		self.assertEqual(
			self.client.post(
				reverse("todo-list"),
				{"card_id": card.pk, "solved": "on"},
			).status_code,
			404,
		)
		self.assertTrue(Card.objects.filter(pk=card.pk).exists())

	def test_box_and_repeat_pages_only_show_the_current_users_cards(self):
		owner = get_user_model().objects.create_user("owner")
		other_user = get_user_model().objects.create_user("other")
		card = self.create_card(user=owner, box=1)
		card.date_moved = timezone.now() - timedelta(days=2)
		card.save(update_fields=["date_moved"])

		self.client.force_login(other_user)
		box_response = self.client.get(reverse("box", args=[1]))
		repeat_response = self.client.get(reverse("todo-list"))

		self.assertNotContains(box_response, "legacy-secret-answer")
		self.assertContains(box_response, "<mark>0</mark>")
		self.assertNotContains(repeat_response, "legacy-secret-answer")
		self.assertEqual(repeat_response.context["card_count"], 0)

		self.client.force_login(owner)
		self.assertContains(self.client.get(reverse("box", args=[1])), "legacy-secret-answer")
		self.assertContains(self.client.get(reverse("todo-list")), "legacy-secret-answer")

	def test_practice_all_cards_does_not_change_box_or_review_date(self):
		user = get_user_model().objects.create_user("practice-all-owner")
		first_card = self.create_card(user=user, box=1)
		second_card = Card.objects.create(
			user=user,
			answer="legacy-secret-answer",
			question="second question",
			box=4,
		)
		first_date_moved = first_card.date_moved
		second_date_moved = second_card.date_moved
		self.client.force_login(user)

		response = self.client.get(
			reverse("practice-all-cards"),
			{"start": "1"},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["remaining_count"], 2)
		first_practice_card = response.context["check_card"]
		self.assertIn(first_practice_card.pk, {first_card.pk, second_card.pk})

		self.client.post(
			reverse("practice-all-cards"),
			{"card_id": first_practice_card.pk, "solved": "on"},
		)
		first_practice_card.refresh_from_db()
		self.assertEqual(first_practice_card.box, (
			1 if first_practice_card.pk == first_card.pk else 4
		))
		self.assertEqual(first_practice_card.date_moved, (
			first_date_moved if first_practice_card.pk == first_card.pk
			else second_date_moved
		))

		response = self.client.get(reverse("practice-all-cards"))
		self.assertEqual(response.context["remaining_count"], 1)
		second_practice_card = response.context["check_card"]
		self.assertNotEqual(second_practice_card.pk, first_practice_card.pk)
		self.client.post(
			reverse("practice-all-cards"),
			{"card_id": second_practice_card.pk, "solved": ""},
		)

		response = self.client.get(reverse("practice-all-cards"))
		self.assertEqual(response.context["remaining_count"], 1)
		self.assertEqual(response.context["check_card"].pk, second_practice_card.pk)
		self.client.post(
			reverse("practice-all-cards"),
			{"card_id": second_practice_card.pk, "solved": "on"},
		)

		response = self.client.get(reverse("practice-all-cards"))
		self.assertIsNone(response.context["check_card"])
		self.assertContains(response, "Practice complete")
		first_card.refresh_from_db()
		second_card.refresh_from_db()
		self.assertEqual((first_card.box, first_card.date_moved), (1, first_date_moved))
		self.assertEqual((second_card.box, second_card.date_moved), (4, second_date_moved))

	def test_box_practice_only_includes_the_users_cards_from_that_box(self):
		user = get_user_model().objects.create_user("practice-box-owner")
		other_user = get_user_model().objects.create_user("practice-box-other")
		box_card = self.create_card(user=user, box=3)
		Card.objects.create(
			user=user,
			answer="legacy-secret-answer",
			question="other box",
			box=2,
		)
		other_users_card = self.create_card(user=other_user, box=3)
		self.client.force_login(user)

		response = self.client.get(
			reverse("practice-box", args=[3]),
			{"start": "1"},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["remaining_count"], 1)
		self.assertEqual(response.context["check_card"].pk, box_card.pk)
		self.assertContains(response, "Practice the 3rd box")
		self.assertContains(
			self.client.get(reverse("box", args=[3])),
			f'{reverse("practice-box", args=[3])}?start=1',
		)
		self.assertEqual(
			self.client.post(
				reverse("practice-box", args=[3]),
				{"card_id": other_users_card.pk, "solved": "on"},
			).status_code,
			404,
		)

	def test_practice_flip_control_flips_all_and_box_cards(self):
		user = get_user_model().objects.create_user("practice-flip-owner")
		all_card = Card.objects.create(
			user=user,
			answer="all card answer",
			question="all card question",
			box=1,
		)
		box_card = Card.objects.create(
			user=user,
			question="box card question",
			answer="box card answer",
			box=2,
		)
		self.client.force_login(user)

		for url, card in (
			(reverse("practice-all-cards"), all_card),
			(reverse("practice-box", args=[2]), box_card),
		):
			with self.subTest(url=url):
				response = self.client.get(url, {"flip": "1", "start": "1"})

				self.assertEqual(response.status_code, 302)
				response = self.client.get(url, {"flip": "1"})

				self.assertTrue(response.context["flipped"])
				check_card = response.context["check_card"]
				self.assertIsNotNone(check_card)
				if "box" in url:
					self.assertEqual(check_card.pk, card.pk)
				self.assertContains(
					response,
					f'aria-labelledby="answer-{check_card.pk}"',
				)

	def test_practice_flip_orientation_is_preserved_for_the_session(self):
		user = get_user_model().objects.create_user("practice-flip-session-owner")
		first_card = Card.objects.create(
			user=user,
			question="First question",
			answer="First answer",
		)
		Card.objects.create(
			user=user,
			question="Second question",
			answer="Second answer",
		)
		self.client.force_login(user)
		url = reverse("practice-all-cards")

		self.client.get(url, {"start": "1"}, follow=True)
		response = self.client.get(url, {"flip": "1"})
		self.assertTrue(response.context["flipped"])
		card = response.context["check_card"]

		self.client.post(url, {"card_id": card.pk, "solved": "on"})
		response = self.client.get(url)
		self.assertTrue(response.context["flipped"])
		self.assertNotEqual(response.context["check_card"].pk, card.pk)

		self.client.get(url, {"flip": "0"})
		response = self.client.get(url)
		self.assertFalse(response.context["flipped"])

	def test_long_practice_question_is_rendered_without_truncation(self):
		user = get_user_model().objects.create_user("long-question-practice-owner")
		question = "A long question " * 25
		Card.objects.create(
			user=user,
			question=question,
			answer="Answer",
		)
		self.client.force_login(user)

		response = self.client.get(
			reverse("practice-all-cards"),
			{"start": "1"},
			follow=True,
		)

		self.assertContains(response, question)
		self.assertContains(response, 'class="card-text-very-long"')

	def test_learning_views_use_focused_layout_with_exit_control(self):
		user = get_user_model().objects.create_user("focused-learning-owner")
		study_card = self.create_card(user=user, box=1)
		study_card.date_moved = timezone.now() - timedelta(days=2)
		study_card.save(update_fields=["date_moved"])
		Card.objects.create(
			user=user,
			answer="practice answer",
			question="practice question",
			box=1,
		)
		self.client.force_login(user)

		responses = [
			self.client.get(reverse("todo-list")),
			self.client.get(
				reverse("practice-all-cards"),
				{"start": "1"},
				follow=True,
			),
		]

		for response in responses:
			with self.subTest(path=response.request["PATH_INFO"]):
				self.assertTrue(response.context["learning_mode"])
				self.assertContains(response, 'class="learning-mode"')
				self.assertContains(response, 'aria-label="Exit learning mode"')
				self.assertContains(
					response,
					f'href="{reverse("card-list")}" aria-label="Exit learning mode"',
				)
				self.assertNotContains(response, 'class="site-header"')
				self.assertNotContains(response, 'aria-label="Main navigation"')
				self.assertNotContains(response, 'class="site-footer"')

	def test_mobile_navigation_uses_compact_tabs_and_menu_popovers(self):
		user = get_user_model().objects.create_user("mobile-navigation-owner")
		self.create_card(user=user, box=1)
		self.client.force_login(user)

		response = self.client.get(reverse("card-list"))

		self.assertContains(response, 'class="main-nav desktop-nav"')
		self.assertContains(response, 'class="mobile-nav"')
		self.assertContains(response, 'aria-label="Card boxes"')
		self.assertContains(response, 'class="mobile-nav-menu mobile-more-menu"')
		self.assertContains(response, "Your boxes")
		self.assertContains(response, "Account")
		self.assertContains(response, reverse("card-list"))
		self.assertContains(response, reverse("todo-list"))
		self.assertContains(response, reverse("box", args=[1]))
		self.assertContains(response, reverse("card-import"))
		self.assertContains(response, reverse("card-export"))
		self.assertContains(response, "Log out")
		self.assertContains(response, 'class="mobile-nav-icon account-initials"')
		self.assertContains(response, "MO")
		self.assertNotContains(response, 'aria-hidden="true">•••</span>')

	def test_desktop_account_menu_uses_initials_and_contains_account_actions(self):
		user = get_user_model().objects.create_user(
			"DesktopOwner",
			password="Correct-Horse-92!",
		)
		StudyProgress.objects.create(user=user, points=300)
		self.client.force_login(user)

		response = self.client.get(reverse("card-list"))

		self.assertContains(response, 'class="desktop-account"')
		self.assertContains(response, 'class="desktop-account-initials"')
		self.assertContains(response, "DE")
		self.assertContains(
			response,
			'aria-label="Account menu for DesktopOwner"',
		)
		self.assertContains(response, "Level 2 · 300 pts")
		self.assertContains(response, "Letter Apprentice")
		self.assertContains(response, reverse("progress"))
		self.assertContains(response, reverse("card-import"))
		self.assertContains(response, reverse("card-export"))
		self.assertContains(
			response,
			f'action="{reverse("logout")}"',
		)
		self.assertContains(response, "Log out")

	def test_desktop_account_menu_is_hidden_from_anonymous_users(self):
		response = self.client.get(reverse("login"))

		self.assertNotContains(response, 'class="desktop-account"')

	def test_navigation_counts_are_loaded_once_for_desktop_and_mobile(self):
		user = get_user_model().objects.create_user("navigation-count-owner")
		due_card = self.create_card(user=user, box=1)
		due_card.date_moved = timezone.now() - timedelta(days=2)
		due_card.save(update_fields=["date_moved"])
		Card.objects.create(
			user=user,
			answer="recent answer",
			question="recent card",
			box=2,
		)
		request = RequestFactory().get("/")
		request.user = user
		template = Template(
			"{% load cards_tags %}"
			"{% boxes_as_links %}{% repeat_box_as_link %}"
			"{% boxes_as_links %}{% repeat_box_as_link %}"
		)

		with self.assertNumQueries(1):
			rendered = template.render(Context({"request": request}))

		self.assertEqual(rendered.count(f'href="{reverse("box", args=[1])}"'), 2)
		self.assertEqual(rendered.count(f'href="{reverse("todo-list")}"'), 2)
		self.assertEqual(rendered.count("<mark>1</mark>"), 6)

	def test_mobile_tab_bar_clearance_covers_tablet_breakpoint(self):
		stylesheet = (
			Path(__file__).parent / "static" / "cards" / "css" / "app.css"
		).read_text(encoding="utf-8")

		self.assertIn(
			"@media (max-width: 960px) {\n"
			"    .page-shell:not(.learning-shell) {\n"
			"        padding-bottom: calc(5.25rem + env(safe-area-inset-bottom));",
			stylesheet,
		)
		self.assertIn(
			".mobile-nav-popover {\n"
			"        position: fixed;\n"
			"        z-index: 30;\n"
			"        top: calc(64px + env(safe-area-inset-top));",
			stylesheet,
		)
		self.assertIn(
			"        bottom: calc(4.5rem + env(safe-area-inset-bottom));",
			stylesheet,
		)
		self.assertIn(
			"        top: calc(56px + env(safe-area-inset-top));",
			stylesheet,
		)
		self.assertIn("        min-height: 4.25rem;", stylesheet)

	def test_card_list_shows_all_user_cards_without_filter_view(self):
		user = get_user_model().objects.create_user("home-page-owner")
		self.create_card(user=user)
		self.client.force_login(user)

		response = self.client.get(reverse("card-list"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "legacy-secret-answer")
		self.assertContains(
			response,
			f'{reverse("practice-all-cards")}?start=1',
		)
		self.assertNotContains(response, "Filter Cards")
		self.assertNotContains(response, 'name="category"')
		with self.assertRaises(NoReverseMatch):
			reverse("fiter-categories")

	def test_home_page_shows_progress_dashboard_and_due_card_count(self):
		user = get_user_model().objects.create_user("dashboard-owner")
		other_user = get_user_model().objects.create_user("other-dashboard-owner")
		due_card = self.create_card(user=user, question="due question")
		Card.objects.filter(pk=due_card.pk).update(
			date_moved=timezone.now() - timedelta(days=2),
		)
		self.create_card(user=user, question="new question")
		other_due_card = self.create_card(
			user=other_user,
			question="other user's due question",
		)
		Card.objects.filter(pk=other_due_card.pk).update(
			date_moved=timezone.now() - timedelta(days=2),
		)
		StudyProgress.objects.create(
			user=user,
			points=46,
			cards_today=10,
			cards_today_date=timezone.localdate(),
			current_streak=1,
			longest_streak=1,
			last_streak_date=timezone.localdate(),
		)
		self.client.force_login(user)

		response = self.client.get(reverse("home"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Welcome back, dashboard-owner")
		self.assertContains(response, "There is 1 card for you to review.")
		self.assertContains(response, "Little Student")
		self.assertContains(response, "46</strong> points")
		self.assertContains(response, "10/10")
		self.assertContains(response, f'class="brand" href="{reverse("home")}"')
		self.assertContains(response, reverse("todo-list"))
		self.assertNotContains(response, "legacy-secret-answer")


class CardAudioTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			"audio-owner",
			password="Correct-Horse-92!",
		)
		self.card = Card.objects.create(
			user=self.user,
			question="Luck",
			answer="حظ",
			answer_vocalized="حَظّ",
		)
		self.client.force_login(self.user)

	def test_card_list_shows_audio_only_for_arabic_answers(self):
		non_arabic_card = Card.objects.create(
			user=self.user,
			question="Legacy question",
			answer="legacy answer",
		)

		response = self.client.get(reverse("card-list"))

		self.assertContains(
			response,
			reverse("card-audio", args=[self.card.pk]),
		)
		self.assertNotContains(
			response,
			reverse("card-audio", args=[non_arabic_card.pk]),
		)
		self.assertContains(response, "arabic-audio-button")
		self.assertContains(response, "Listen")

	def test_audio_view_generates_and_caches_the_vocalized_answer(self):
		class FakeTTS:
			calls = []

			def __init__(self, text, lang, slow, timeout):
				self.__class__.calls.append((text, lang, slow, timeout))

			def save(self, path):
				Path(path).write_bytes(b"fake-mp3")

		with TemporaryDirectory() as temp_dir:
			with override_settings(ARABIC_AUDIO_CACHE_DIR=temp_dir):
				with patch("cards.audio.gTTS", FakeTTS):
					response = self.client.get(
						reverse("card-audio", args=[self.card.pk])
					)
					content = b"".join(response.streaming_content)

					self.assertEqual(response.status_code, 200)
					self.assertEqual(response["Content-Type"], "audio/mpeg")
					self.assertEqual(
						response["Cache-Control"],
						"private, max-age=86400",
					)
					self.assertEqual(content, b"fake-mp3")

					second_response = self.client.get(
						reverse("card-audio", args=[self.card.pk])
					)
					b"".join(second_response.streaming_content)

					self.assertEqual(len(FakeTTS.calls), 1)
					self.assertEqual(FakeTTS.calls[0][0], "حَظّ")
					self.assertEqual(FakeTTS.calls[0][1], "ar")
					self.assertEqual(
						len(list(Path(temp_dir).glob("*.mp3"))),
						1,
					)

	def test_audio_view_rejects_another_users_card(self):
		other_user = get_user_model().objects.create_user("audio-other")
		other_card = Card.objects.create(
			user=other_user,
			question="Other",
			answer="آخر",
		)

		response = self.client.get(
			reverse("card-audio", args=[other_card.pk])
		)

		self.assertEqual(response.status_code, 404)

	def test_audio_view_requires_login(self):
		self.client.logout()

		response = self.client.get(
			reverse("card-audio", args=[self.card.pk])
		)

		self.assertRedirects(
			response,
			f"{reverse('login')}?next={reverse('card-audio', args=[self.card.pk])}",
		)

	def test_audio_view_returns_503_when_tts_fails(self):
		class BrokenTTS:
			def __init__(self, *args, **kwargs):
				pass

			def save(self, path):
				raise OSError("tts is down")

		with TemporaryDirectory() as temp_dir:
			with override_settings(ARABIC_AUDIO_CACHE_DIR=temp_dir):
				with patch("cards.audio.gTTS", BrokenTTS):
					with self.assertLogs("cards.audio", level="ERROR"):
						response = self.client.get(
							reverse("card-audio", args=[self.card.pk])
						)

		self.assertEqual(response.status_code, 503)
		self.assertEqual(
			response.content,
			b"Audio is unavailable right now. Please try again.",
		)


class CardImportTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user("importer")
		self.client.force_login(self.user)

	def upload(self, filename, content):
		return self.client.post(
			reverse("card-import"),
			{
				"file": SimpleUploadedFile(filename, content),
			},
		)

	def test_csv_upload_creates_owned_cards_with_english_question_and_arabic_answer(self):
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="hello",
			translation="مرحبا",
			vocalized_translation="مَرْحَبًا",
		)
		response = self.upload(
			"cards.csv",
			(
				"question,answer\n"
				"hello,مرحبا\n"
				"bye,وداعا\n"
			).encode("utf-8"),
		)

		self.assertRedirects(response, reverse("card-list"))
		self.assertEqual(list(response.wsgi_request._messages)[0].message, "Imported 2 cards.")
		cards = list(Card.objects.filter(user=self.user).order_by("id"))
		self.assertEqual(len(cards), 2)
		self.assertEqual(cards[0].question, "hello")
		self.assertEqual(cards[0].answer, "مرحبا")
		self.assertEqual(cards[0].answer_vocalized, "مَرْحَبًا")
		self.assertEqual(cards[0].box, 1)
		self.assertEqual(cards[1].answer_vocalized, "")
		other_user = get_user_model().objects.create_user("other-deck")
		self.assertEqual(Card.objects.filter(user=other_user).count(), 0)

	def test_csv_upload_leaves_arabic_vocalization_blank_without_dictionary_entry(self):
		answer = "هي تحب ان تذهب"
		response = self.upload(
			"arabic-cards.csv",
			f"question,answer\nphrase,{answer}\n".encode("utf-8"),
		)

		self.assertRedirects(response, reverse("card-list"))
		card = Card.objects.get(user=self.user, question="phrase")
		self.assertEqual(card.answer_vocalized, "")

	def test_xlsx_upload_creates_cards_with_english_question_and_arabic_answer(self):
		from openpyxl import Workbook

		workbook = Workbook()
		worksheet = workbook.active
		worksheet.append(["question", "answer"])
		worksheet.append(["maison", "منزل"])
		content = BytesIO()
		workbook.save(content)

		response = self.upload("cards.xlsx", content.getvalue())

		self.assertRedirects(response, reverse("card-list"))
		card = Card.objects.get(user=self.user)
		self.assertEqual(card.question, "maison")
		self.assertEqual(card.answer, "منزل")

	def test_exported_xlsx_round_trips_question_and_answer(self):
		Card.objects.create(
			user=self.user,
			answer="مرحبا",
			question="hello",
		)
		Card.objects.create(
			user=self.user,
			answer="منزل",
			question="maison",
		)
		other_user = get_user_model().objects.create_user("export-recipient")
		Card.objects.create(
			user=other_user,
			answer="private",
			question="private question",
		)

		export_response = self.client.get(reverse("card-export"))

		self.assertEqual(export_response.status_code, 200)
		self.assertEqual(
			export_response["Content-Disposition"],
			'attachment; filename="flashcards.xlsx"',
		)
		self.assertEqual(
			export_response["Content-Type"],
			"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		)
		from openpyxl import load_workbook
		exported_workbook = load_workbook(
			BytesIO(export_response.content),
			read_only=True,
			data_only=True,
		)
		exported_rows = list(
			exported_workbook.active.iter_rows(values_only=True)
		)
		exported_workbook.close()
		self.assertEqual(
			exported_rows,
			[
				("question", "answer"),
				("hello", "مرحبا"),
				("maison", "منزل"),
			],
		)
		self.assertNotIn(
			("private question", "private"),
			exported_rows,
		)

		recipient_client = self.client_class()
		recipient_client.force_login(other_user)
		import_response = recipient_client.post(
			reverse("card-import"),
			{
				"file": SimpleUploadedFile(
					"flashcards.xlsx",
					export_response.content,
					content_type=(
						"application/vnd.openxmlformats-officedocument."
						"spreadsheetml.sheet"
					),
				),
			},
		)

		self.assertRedirects(import_response, reverse("card-list"))
		imported_cards = Card.objects.filter(user=other_user).order_by("id")
		self.assertEqual(imported_cards.count(), 3)
		self.assertEqual(
			list(imported_cards.values_list("question", "answer")),
			[
				("private question", "private"),
				("hello", "مرحبا"),
				("maison", "منزل"),
			],
		)

	def test_create_and_import_pages_show_all_box_counts(self):
		Card.objects.create(
			user=self.user,
			answer="مرحبا",
			question="hello",
			box=1,
		)

		for url in (reverse("card-create"), reverse("card-import")):
			with self.subTest(url=url):
				response = self.client.get(url)
				self.assertContains(response, "My cards")
				self.assertContains(response, "<mark>1</mark>")
				self.assertContains(response, "<mark>0</mark>")

	def test_duplicate_rows_are_skipped_and_reported_after_import(self):
		response = self.upload(
			"cards.csv",
			(
				"question,answer\n"
				"hello,مرحبا\n"
				"hello,مرحبا ثانية\n"
				"bye,وداعا\n"
			).encode("utf-8"),
		)

		self.assertRedirects(response, reverse("card-list"))
		self.assertEqual(Card.objects.filter(user=self.user).count(), 2)
		message = list(response.wsgi_request._messages)[0]
		self.assertEqual(
			message.message,
			"Imported 2 cards. Skipped 1 duplicate card: hello.",
		)
		self.assertTrue(message.tags.startswith("warning"))

	def test_invalid_csv_imports_nothing(self):
		response = self.upload("bad.csv", b"front,back\nhello,world\n")
		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			"question and answer",
		)
		self.assertEqual(Card.objects.filter(user=self.user).count(), 0)

		response = self.upload(
			"legacy-language.csv",
			b"question,answer,language\nhello,world,en\n",
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "question and answer")
		self.assertEqual(Card.objects.filter(user=self.user).count(), 0)

		response = self.upload(
			"invalid-row.csv",
			(
				"question,answer\n"
				"valid question,جواب صحيح\n"
				",missing question\n"
			).encode("utf-8"),
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Row 3")
		self.assertEqual(Card.objects.filter(user=self.user).count(), 0)

	def test_upload_skips_existing_duplicate_but_imports_other_rows(self):
		Card.objects.create(
			user=self.user,
			answer="مرحبا",
			question="hello",
		)
		other_user = get_user_model().objects.create_user("other-importer")
		Card.objects.create(
			user=other_user,
			answer="جواب آخر",
			question="other question",
		)

		response = self.upload(
			"cards.csv",
			(
				"question,answer\n"
				"hello,ترجمة مختلفة\n"
				"new question,جواب جديد\n"
			).encode("utf-8"),
		)

		self.assertRedirects(response, reverse("card-list"))
		self.assertEqual(Card.objects.filter(user=self.user).count(), 2)
		self.assertEqual(Card.objects.filter(user=other_user).count(), 1)
		message = list(response.wsgi_request._messages)[0]
		self.assertEqual(
			message.message,
			"Imported 1 card. Skipped 1 duplicate card: hello.",
		)

	def test_upload_requires_login(self):
		self.client.logout()
		response = self.upload("cards.csv", b"question,answer\nhello,world\n")

		self.assertEqual(response.status_code, 302)
		self.assertIn(reverse("login"), response.url)

	def test_import_rejects_question_without_english_text(self):
		response = self.upload(
			"cards.csv",
			"question,answer\nمرحبا,مرحبا\n".encode("utf-8"),
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			"Row 2: question must contain English text.",
		)
		self.assertEqual(Card.objects.filter(user=self.user).count(), 0)

	def test_import_rejects_answer_without_arabic_text(self):
		response = self.upload(
			"cards.csv",
			b"question,answer\nhello,goodbye\n",
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			"Row 2: answer must contain Arabic text.",
		)
		self.assertEqual(Card.objects.filter(user=self.user).count(), 0)

	def test_import_reports_the_first_invalid_row(self):
		response = self.upload(
			"cards.csv",
			(
				"question,answer\n"
				"valid,جواب\n"
				"hello,goodbye\n"
				"مرحبا,مرحبا\n"
			).encode("utf-8"),
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			"Row 3: answer must contain Arabic text.",
		)
		self.assertNotContains(
			response,
			"Row 4: question must contain English text.",
		)
		self.assertEqual(Card.objects.filter(user=self.user).count(), 0)

	def test_import_accepts_mixed_script_question_with_english_letters(self):
		question = "What is مرحبا in English?"
		response = self.upload(
			"cards.csv",
			f"question,answer\n{question},مرحبا\n".encode("utf-8"),
		)

		self.assertRedirects(response, reverse("card-list"))
		self.assertTrue(
			Card.objects.filter(
				user=self.user,
				question=question,
			).exists()
		)

	def test_export_writes_english_question_and_arabic_answer(self):
		Card.objects.create(
			user=self.user,
			answer="hello",
			question="مرحبا",
		)
		Card.objects.create(
			user=self.user,
			answer="وداعا",
			question="bye",
		)

		export_response = self.client.get(reverse("card-export"))

		from openpyxl import load_workbook
		exported_workbook = load_workbook(
			BytesIO(export_response.content),
			read_only=True,
			data_only=True,
		)
		exported_rows = list(
			exported_workbook.active.iter_rows(values_only=True)
		)
		exported_workbook.close()
		self.assertEqual(
			exported_rows,
			[
				("question", "answer"),
				("hello", "مرحبا"),
				("bye", "وداعا"),
			],
		)

	def test_exported_file_can_be_imported_again(self):
		Card.objects.create(
			user=self.user,
			answer="hello",
			question="مرحبا",
		)
		export_response = self.client.get(reverse("card-export"))

		recipient = get_user_model().objects.create_user("export-round-trip")
		recipient_client = self.client_class()
		recipient_client.force_login(recipient)
		import_response = recipient_client.post(
			reverse("card-import"),
			{
				"file": SimpleUploadedFile(
					"flashcards.xlsx",
					export_response.content,
					content_type=(
						"application/vnd.openxmlformats-officedocument."
						"spreadsheetml.sheet"
					),
				),
			},
		)

		self.assertRedirects(import_response, reverse("card-list"))
		self.assertEqual(
			list(
				Card.objects.filter(user=recipient).values_list(
					"question",
					"answer",
				)
			),
			[("hello", "مرحبا")],
		)


class ArabicVocalizationTests(TestCase):
	def test_bulk_vocalization_only_fills_blank_answers_for_current_user(self):
		user = get_user_model().objects.create_user("bulk-vocalization-owner")
		other_user = get_user_model().objects.create_user("bulk-vocalization-other")
		missing_card = Card.objects.create(
			user=user,
			question="Arabic answer",
			answer="هي تحب ان تذهب",
		)
		existing_card = Card.objects.create(
			user=user,
			question="Already vocalized",
			answer="صباح الخير",
			answer_vocalized="صَبَاحُ الْخَيْرِ",
		)
		non_arabic_card = Card.objects.create(
			user=user,
			question="English answer",
			answer="Good morning",
		)
		other_users_card = Card.objects.create(
			user=other_user,
			question="Other user's answer",
			answer="صباح الخير",
		)
		self.client.force_login(user)

		with patch(
			"cards.views.vocalize_arabic",
			return_value="هِيَ تُحِبُّ أَنْ تَذْهَبَ",
		) as vocalize:
			response = self.client.post(
				reverse("vocalize-missing-cards"),
				follow=True,
			)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Vocalize missing answers")
		self.assertContains(
			response,
			"Vocalized 1 card. Skipped 1 answer without Arabic text.",
		)
		vocalize.assert_called_once_with("هي تحب ان تذهب")
		missing_card.refresh_from_db()
		existing_card.refresh_from_db()
		non_arabic_card.refresh_from_db()
		other_users_card.refresh_from_db()
		self.assertEqual(
			missing_card.answer_vocalized,
			"هِيَ تُحِبُّ أَنْ تَذْهَبَ",
		)
		self.assertEqual(existing_card.answer_vocalized, "صَبَاحُ الْخَيْرِ")
		self.assertEqual(non_arabic_card.answer_vocalized, "")
		self.assertEqual(other_users_card.answer_vocalized, "")

	def test_bulk_vocalization_reports_mishkal_failures(self):
		user = get_user_model().objects.create_user("bulk-vocalization-failure")
		card = Card.objects.create(
			user=user,
			question="Arabic answer",
			answer="هي تحب ان تذهب",
		)
		self.client.force_login(user)

		with patch("cards.views.vocalize_arabic", return_value=""):
			response = self.client.post(
				reverse("vocalize-missing-cards"),
				follow=True,
			)

		self.assertContains(
			response,
			"Vocalized 0 cards. Mishkal could not vocalize 1 Arabic answer.",
		)
		card.refresh_from_db()
		self.assertEqual(card.answer_vocalized, "")

	def test_bulk_vocalization_requires_login(self):
		response = self.client.post(reverse("vocalize-missing-cards"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?next={reverse('vocalize-missing-cards')}",
		)

	def test_dictionary_question_vocalization_is_shown_but_blank_answer_is_not(self):
		user = get_user_model().objects.create_user("arbitrary-arabic-owner")
		self.client.force_login(user)
		question = "لماذا لا تأتي إلى البيت"
		answer = "هي تحب ان تذهب"
		question_vocalized = "لِمَاذَا لَا تَأْتِي إِلَى الْبَيْتِ"
		answer_vocalized = "هِيَ تُحِبُّ أَنْ تَذْهَبَ"
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Why do you not come home",
			translation=question,
			vocalized_translation=question_vocalized,
		)
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="She likes to go",
			translation=answer,
			vocalized_translation=answer_vocalized,
		)
		card = Card.objects.create(user=user, question=question, answer=answer)
		card.date_moved = timezone.now() - timedelta(days=2)
		card.save(update_fields=["date_moved"])

		for flip in (None, "1"):
			response = self.client.get(
				reverse("todo-list"),
				{"flip": flip} if flip else {},
			)

			self.assertEqual(
				arabic_display_texts(response, f"question-{card.id}"),
				[f"{question} {question_vocalized}"],
			)
			self.assertEqual(arabic_display_texts(response, f"answer-{card.id}"), [])
			self.assertContains(response, answer)
			self.assertNotContains(response, answer_vocalized)

	def test_all_cards_practice_shows_dictionary_question_vocalization_only(self):
		user = get_user_model().objects.create_user("all-cards-vocalization-owner")
		self.client.force_login(user)
		question = "لفظة تقرأ طردا وعكسا"
		vocalized_question = "لَفْظَةٌ تَقْرَأُ طَرْدَا وَعَكْسَا"
		answer = "ألفاظ تقرأ طردا وعكسا"
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Palindrome",
			translation=question,
			vocalized_translation=vocalized_question,
		)
		card = Card.objects.create(
			user=user,
			question=question,
			answer=answer,
		)

		response = self.client.get(
			reverse("practice-all-cards"),
			{"start": "1"},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["check_card"].pk, card.pk)
		self.assertEqual(
			arabic_display_texts(response, f"question-{card.pk}"),
			[f"{question} {vocalized_question}"],
		)
		self.assertEqual(arabic_display_texts(response, f"answer-{card.pk}"), [])
		self.assertContains(response, answer)

	def test_manually_entered_vocalization_is_saved_with_arabic_answer(self):
		answer = "هي تحب ان تذهب"
		vocalized_answer = "هِيَ تُحِبُّ أَنْ تَذْهَبَ"
		form = CardForm(data={
			"question": "Arabic phrase",
			"answer": answer,
			"answer_vocalized": vocalized_answer,
			"box": 1,
		})

		self.assertTrue(form.is_valid())
		card = form.save()

		self.assertEqual(card.answer_vocalized, vocalized_answer)

	def test_clearing_vocalization_removes_dictionary_fallback(self):
		user = get_user_model().objects.create_user("clear-vocalization-owner")
		self.client.force_login(user)
		answer = "صباح الخير"
		vocalized_answer = "صَبَاحُ الْخَيْرِ"
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Good morning",
			translation=answer,
			vocalized_translation=vocalized_answer,
		)
		card = Card.objects.create(
			user=user,
			question="Good morning",
			answer=answer,
			answer_vocalized=vocalized_answer,
		)

		response = self.client.post(
			reverse("card-update", args=[card.pk]),
			{
				"question": card.question,
				"answer": card.answer,
				"answer_vocalized": "",
				"box": card.box,
			},
		)

		self.assertRedirects(response, reverse("card-list"))
		card.refresh_from_db()
		self.assertEqual(card.answer_vocalized, "")
		list_response = self.client.get(reverse("card-list"))
		self.assertContains(list_response, answer)
		self.assertNotContains(list_response, vocalized_answer)

	def test_repeat_card_shows_vocalized_question_before_revealing_answer(self):
		user = get_user_model().objects.create_user("arabic-question-owner")
		self.client.force_login(user)
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Sick",
			translation="مريض",
			vocalized_translation="مَرِيضٌ",
		)
		card = Card.objects.create(
			user=user,
			question="مريض",
			answer="Sick",
		)
		card.date_moved = timezone.now() - timedelta(days=2)
		card.save(update_fields=["date_moved"])

		response = self.client.get(reverse("todo-list"))

		self.assertContains(response, "مريض")
		self.assertContains(response, "مَرِيضٌ")
		self.assertContains(response, "Reveal Answer")
		self.assertContains(response, 'name="flip"')
		self.assertNotContains(response, 'name="flip" value="1" checked')
		self.assertContains(response, 'class="flip-face flip-front"')
		self.assertContains(response, 'class="flip-face flip-back"')
		self.assertContains(response, 'class="flip-toggle flip-forward"')
		self.assertContains(response, "Reveal Answer")
		self.assertContains(response, 'class="flip-toggle flip-return"')
		self.assertContains(response, "Show Question")
		self.assertContains(response, 'aria-label="Reveal answer"')
		front = response.content.decode().split(
			'class="flip-face flip-front"',
			1,
		)[1].split('class="flip-face flip-back"', 1)[0]
		back = response.content.decode().split(
			'class="flip-face flip-back"',
			1,
		)[1].split("</section>", 1)[0]
		self.assertIn('class="card-side-label">QUESTION', front)
		self.assertIn('class="card-side-label">ANSWER', back)
		self.assertIn("مريض", front)
		self.assertIn("مَرِيضٌ", front)
		self.assertIn("Sick", back)
		self.assertEqual(card.answer_vocalized, "")

	def test_flipped_repeat_card_shows_answer_first_and_question_after_reveal(self):
		user = get_user_model().objects.create_user("flipped-arabic-question-owner")
		self.client.force_login(user)
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Sick",
			translation="مريض",
			vocalized_translation="مَرِيضٌ",
		)
		card = Card.objects.create(
			user=user,
			question="مريض",
			answer="Sick",
		)
		card.date_moved = timezone.now() - timedelta(days=2)
		card.save(update_fields=["date_moved"])

		response = self.client.get(reverse("todo-list"), {"flip": "1"})
		rendered = response.content.decode()

		self.assertContains(response, 'name="flip"')
		self.assertContains(response, "checked")
		self.assertContains(response, "Reveal Answer")
		self.assertNotContains(response, "Reveal Question")
		flipped_front = rendered.split(
			'class="flip-face flip-front"',
			1,
		)[1].split('class="flip-face flip-back"', 1)[0]
		flipped_back = rendered.split(
			'class="flip-face flip-back"',
			1,
		)[1].split("</section>", 1)[0]
		self.assertIn('class="card-side-label">QUESTION', flipped_front)
		self.assertIn('class="card-side-label">ANSWER', flipped_back)
		self.assertIn("Sick", flipped_front)
		self.assertIn("مريض", flipped_back)
		self.assertIn("مَرِيضٌ", flipped_back)

	def test_blank_arabic_answer_is_not_given_dictionary_vocalization_on_repeat(self):
		user = get_user_model().objects.create_user("arabic-answer-owner")
		self.client.force_login(user)
		DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Sick",
			translation="مريض",
			vocalized_translation="مَرِيضٌ",
		)
		card = Card.objects.create(
			user=user,
			question="Sick",
			answer="مريض",
		)
		card.date_moved = timezone.now() - timedelta(days=2)
		card.save(update_fields=["date_moved"])

		default_response = self.client.get(reverse("todo-list"))
		default_rendered = default_response.content.decode()
		self.assertNotContains(default_response, "مَرِيضٌ")
		default_back = default_rendered.split(
			'class="flip-face flip-back"',
			1,
		)[1].split("</section>", 1)[0]
		self.assertIn('class="card-side-label">ANSWER', default_back)
		self.assertIn("مريض", default_back)
		self.assertNotIn("مَرِيضٌ", default_back)

		flipped_response = self.client.get(reverse("todo-list"), {"flip": "1"})
		flipped_rendered = flipped_response.content.decode()
		self.assertNotContains(flipped_response, "مَرِيضٌ")
		flipped_front = flipped_rendered.split(
			'class="flip-face flip-front"',
			1,
		)[1].split('class="flip-face flip-back"', 1)[0]
		self.assertIn('class="card-side-label">QUESTION', flipped_front)
		self.assertIn("مريض", flipped_front)
		self.assertNotIn("مَرِيضٌ", flipped_front)

	def test_card_result_displays_vocalized_answer_right_to_left(self):
		entry = DictionaryEntry.objects.create(
			language_pair="en-ar",
			headword="Apple",
			translation="التفاحة",
			vocalized_translation="التُّفَّاحَةُ",
		)
		form = CardForm(data={
			"question": "apple",
			"answer": "التفاحة",
			"dictionary_entry_id": entry.id,
			"box": 1,
		})
		self.assertTrue(form.is_valid())
		card = form.save()
		rendered = render_to_string("cards/card.html", {
			"card": card,
			"check_card": False,
		})

		self.assertIn("التفاحة", rendered)
		self.assertIn("التُّفَّاحَةُ", rendered)
		self.assertIn('lang="ar" dir="rtl"', rendered)

	def test_import_vocalized_dictionary_stores_both_translations(self):
		payload = {
			"metadata": {
				"format": "freedict-vocalized-json-v1",
				"language_pair": "en-ar",
				"entry_count": 1,
			},
			"entries": [{
				"headword": "Apple",
				"translation": "التفاحة",
				"vocalized_translation": "التُّفَّاحَةُ",
			}],
		}
		with TemporaryDirectory() as directory:
			json_path = f"{directory}/dictionary.json"
			with open(json_path, "w", encoding="utf-8") as dictionary_file:
				json.dump(payload, dictionary_file, ensure_ascii=False)
			call_command("import_vocalized_dictionary", json_path, stdout=StringIO())

		entry = DictionaryEntry.objects.get(language_pair="en-ar", headword="Apple")
		self.assertEqual(entry.translation, "التفاحة")
		self.assertEqual(entry.vocalized_translation, "التُّفَّاحَةُ")


class ConsolidateQuestionMigrationTests(TransactionTestCase):
	latest_migration = [(
		"cards",
		"0017_emailverification",
	)]

	def test_migration_handles_collisions_and_restores_questions_on_rollback(self):
		before_migration = [("cards", "0012_remove_card_uniq_card_user_answer_de_and_more")]
		after_migration = [("cards", "0013_consolidate_question_field")]

		try:
			MigrationExecutor(connection).migrate(before_migration)
			old_apps = MigrationExecutor(connection).loader.project_state(
				before_migration
			).apps
			User = old_apps.get_model("auth", "User")
			LegacyCard = old_apps.get_model("cards", "Card")
			user = User.objects.create(username="question-migration-owner")
			LegacyCard.objects.create(
				user_id=user.pk,
				answer="translation",
				question_de="same question",
				question_en="",
				question_pl="",
				question_it="",
				question_pt="",
				question_es="",
				language="de",
			)
			LegacyCard.objects.create(
				user_id=user.pk,
				answer="translation",
				question_de="same question (2)",
				question_en="",
				question_pl="",
				question_it="",
				question_pt="",
				question_es="",
				language="de",
			)
			LegacyCard.objects.create(
				user_id=user.pk,
				answer="other translation",
				question_de="",
				question_en="same question",
				question_pl="",
				question_it="",
				question_pt="",
				question_es="",
				language="en",
			)

			MigrationExecutor(connection).migrate(after_migration)
			current_apps = MigrationExecutor(connection).loader.project_state(
				after_migration
			).apps
			CurrentCard = current_apps.get_model("cards", "Card")
			self.assertEqual(
				list(
					CurrentCard.objects.filter(user_id=user.pk)
					.order_by("pk")
					.values_list(
						"question",
						flat=True,
					)
				),
				["same question", "same question (2)", "same question (3)"],
			)
			with self.assertRaises(IntegrityError), transaction.atomic():
				CurrentCard.objects.create(
					user_id=user.pk,
					question="same question",
					answer="a different answer",
				)

			MigrationExecutor(connection).migrate(before_migration)
			restored_apps = MigrationExecutor(connection).loader.project_state(
				before_migration
			).apps
			RestoredCard = restored_apps.get_model("cards", "Card")
			restored_rows = list(
				RestoredCard.objects.filter(user_id=user.pk).values_list(
					"question_de",
					"question_en",
					"question_pl",
					"question_it",
					"question_pt",
					"question_es",
					"language",
				)
			)
			self.assertEqual(len(restored_rows), 3)
			self.assertTrue(
				all(any(row[:6]) for row in restored_rows)
			)
			self.assertEqual(
				{row[0] for row in restored_rows},
				{
					"same question",
					"same question (2)",
					"same question (3)",
				},
			)
			self.assertEqual(
				{row[6] for row in restored_rows},
				{"de"},
			)
		finally:
			MigrationExecutor(connection).migrate(self.latest_migration)

	def test_migration_removes_category_field_and_account_settings(self):
		before_migration = [("cards", "0013_consolidate_question_field")]

		try:
			MigrationExecutor(connection).migrate(before_migration)
			old_apps = MigrationExecutor(connection).loader.project_state(
				before_migration
			).apps
			User = old_apps.get_model("auth", "User")
			LegacyCard = old_apps.get_model("cards", "Card")
			CategorySettings = old_apps.get_model("cards", "AccountCategorySettings")
			user = User.objects.create(username="category-migration-owner")
			CategorySettings.objects.create(user_id=user.pk, arabic_only=True)
			LegacyCard.objects.create(
				user_id=user.pk,
				answer="translation",
				question="question",
				category="Arabic",
			)

			MigrationExecutor(connection).migrate(self.latest_migration)
			current_apps = MigrationExecutor(connection).loader.project_state(
				self.latest_migration
			).apps
			CurrentCard = current_apps.get_model("cards", "Card")
			self.assertNotIn("category", [field.name for field in CurrentCard._meta.fields])
			self.assertNotIn(
				"accountcategorysettings",
				current_apps.all_models["cards"],
			)
			with connection.cursor() as cursor:
				card_columns = {
					column.name
					for column in connection.introspection.get_table_description(
						cursor,
						CurrentCard._meta.db_table,
					)
				}
				table_names = connection.introspection.table_names(cursor)
			self.assertNotIn("category", card_columns)
			self.assertNotIn("cards_accountcategorysettings", table_names)
		finally:
			MigrationExecutor(connection).migrate(self.latest_migration)

	def test_arabic_question_migration_reorients_pairs_without_changing_ids(self):
		before_migration = [(
			"cards",
			"0014_remove_card_category_and_account_category_settings",
		)]
		after_migration = [(
			"cards",
			"0015_reorient_arabic_cards_and_expand_questions",
		)]

		try:
			MigrationExecutor(connection).migrate(before_migration)
			old_apps = MigrationExecutor(connection).loader.project_state(
				before_migration
			).apps
			User = old_apps.get_model("auth", "User")
			LegacyCard = old_apps.get_model("cards", "Card")
			user = User.objects.create(username="arabic-reorientation-owner")
			reversed_card = LegacyCard.objects.create(
				user_id=user.pk,
				question="مَرْحَبًا",
				answer="Hello",
				answer_vocalized="stale vocalization",
			)
			already_oriented_card = LegacyCard.objects.create(
				user_id=user.pk,
				question="Good morning",
				answer="صَبَاحُ الْخَيْرِ",
				answer_vocalized="صَبَاحُ الْخَيْرِ",
			)
			both_arabic_card = LegacyCard.objects.create(
				user_id=user.pk,
				question="أَهْلًا",
				answer="أَهْلًا",
				answer_vocalized="",
			)
			card_ids = {
				reversed_card.pk,
				already_oriented_card.pk,
				both_arabic_card.pk,
			}

			MigrationExecutor(connection).migrate(after_migration)
			current_apps = MigrationExecutor(connection).loader.project_state(
				after_migration
			).apps
			CurrentCard = current_apps.get_model("cards", "Card")
			reversed_card = CurrentCard.objects.get(pk=reversed_card.pk)
			already_oriented_card = CurrentCard.objects.get(
				pk=already_oriented_card.pk
			)
			both_arabic_card = CurrentCard.objects.get(pk=both_arabic_card.pk)

			self.assertEqual(
				(reversed_card.question, reversed_card.answer),
				("Hello", "مَرْحَبًا"),
			)
			self.assertEqual(reversed_card.answer_vocalized, "")
			self.assertEqual(
				(already_oriented_card.question, already_oriented_card.answer),
				("Good morning", "صَبَاحُ الْخَيْرِ"),
			)
			self.assertEqual(
				(both_arabic_card.question, both_arabic_card.answer),
				("أَهْلًا", "أَهْلًا"),
			)
			self.assertEqual(
				set(CurrentCard.objects.filter(user_id=user.pk).values_list("pk", flat=True)),
				card_ids,
			)
			self.assertEqual(
				CurrentCard._meta.get_field("question").max_length,
				500,
			)
			self.assertEqual(CurrentCard._meta.pk.get_internal_type(), "BigAutoField")
		finally:
			MigrationExecutor(connection).migrate(self.latest_migration)

	def test_arabic_question_migration_reports_conflicts_before_changing_data(self):
		before_migration = [(
			"cards",
			"0014_remove_card_category_and_account_category_settings",
		)]
		after_migration = [(
			"cards",
			"0015_reorient_arabic_cards_and_expand_questions",
		)]

		try:
			MigrationExecutor(connection).migrate(before_migration)
			old_apps = MigrationExecutor(connection).loader.project_state(
				before_migration
			).apps
			User = old_apps.get_model("auth", "User")
			LegacyCard = old_apps.get_model("cards", "Card")
			user = User.objects.create(username="arabic-reorientation-conflict")
			reversed_card = LegacyCard.objects.create(
				user_id=user.pk,
				question="مَرْحَبًا",
				answer="Hello",
				answer_vocalized="stale vocalization",
			)
			existing_card = LegacyCard.objects.create(
				user_id=user.pk,
				question="Hello",
				answer="مرحبا",
				answer_vocalized="مَرْحَبًا",
			)

			with self.assertRaisesRegex(
				RuntimeError,
				"question collision",
			) as error:
				MigrationExecutor(connection).migrate(after_migration)

			self.assertIn(str(reversed_card.pk), str(error.exception))
			self.assertIn(str(existing_card.pk), str(error.exception))
			self.assertEqual(
				list(
					LegacyCard.objects.filter(user_id=user.pk)
					.order_by("pk")
					.values_list("pk", "question", "answer", "answer_vocalized")
				),
				[
					(
						reversed_card.pk,
						"مَرْحَبًا",
						"Hello",
						"stale vocalization",
					),
					(
						existing_card.pk,
						"Hello",
						"مرحبا",
						"مَرْحَبًا",
					),
				],
			)
			self.assertNotIn(
				("cards", "0015_reorient_arabic_cards_and_expand_questions"),
				MigrationExecutor(connection).loader.applied_migrations,
			)
			with connection.cursor() as cursor:
				column_names = {
					column.name
					for column in connection.introspection.get_table_description(
						cursor,
						LegacyCard._meta.db_table,
					)
				}
			self.assertEqual(
				LegacyCard._meta.get_field("question").max_length,
				100,
			)
			self.assertTrue(
				{"question", "answer", "answer_vocalized"}.issubset(column_names)
			)
		finally:
			if "user" in locals():
				LegacyCard.objects.filter(user_id=user.pk).delete()
				User.objects.filter(pk=user.pk).delete()
			MigrationExecutor(connection).migrate(self.latest_migration)


class StudyProgressTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user("studious")
		self.client.force_login(self.user)
		self.card = Card.objects.create(
			user=self.user,
			question="Luck",
			answer="حظ",
			answer_vocalized="حَظّ",
		)

	def review(self, solved="on", follow=False):
		return self.client.post(
			reverse("todo-list"),
			{"card_id": self.card.pk, "solved": solved},
			HTTP_REFERER=reverse("todo-list"),
			follow=follow,
		)

	def progress(self):
		return StudyProgress.objects.get(user=self.user)

	def test_review_awards_five_points_for_a_correct_answer_and_counts_today(self):
		self.review()

		progress = self.progress()
		self.assertEqual(progress.points, 5)
		self.assertEqual(progress.cards_today, 1)
		self.assertEqual(progress.current_streak, 0)
		self.assertEqual(progress.cards_today_date, timezone.localdate())

	def test_review_awards_one_point_for_a_wrong_answer(self):
		self.review(solved="")

		progress = self.progress()
		self.assertEqual(progress.points, 1)
		self.assertEqual(progress.cards_today, 1)

	def test_practice_awards_five_points_for_a_correct_answer(self):
		self.client.get(reverse("practice-all-cards"), {"start": "1"})
		session_key = f"card_practice_{self.user.pk}_all"
		card_id = self.client.session[session_key][0]

		self.client.post(
			reverse("practice-all-cards"),
			{"card_id": card_id, "solved": "on"},
		)

		self.assertEqual(self.progress().points, 5)
		self.assertEqual(self.progress().cards_today, 1)

	def test_ten_reviews_award_the_daily_streak_bonus(self):
		response = None
		for _ in range(10):
			response = self.review(follow=True)

		progress = self.progress()
		self.assertEqual(progress.points, 70)
		self.assertEqual(progress.cards_today, 10)
		self.assertEqual(progress.current_streak, 1)
		self.assertEqual(progress.longest_streak, 1)
		self.assertEqual(progress.last_streak_date, timezone.localdate())
		self.assertContains(response, "Daily goal reached")
		self.assertContains(response, "+20 points")

	def test_streak_bonus_is_awarded_only_once_per_day(self):
		for _ in range(12):
			self.review()

		progress = self.progress()
		self.assertEqual(progress.points, 80)
		self.assertEqual(progress.current_streak, 1)

	def test_consecutive_day_extends_the_streak(self):
		progress = StudyProgress.objects.create(
			user=self.user,
			points=40,
			cards_today=0,
			cards_today_date=timezone.localdate(),
			current_streak=1,
			longest_streak=1,
			last_streak_date=timezone.localdate() - timedelta(days=1),
		)

		for _ in range(10):
			self.review()

		progress.refresh_from_db()
		self.assertEqual(progress.current_streak, 2)
		self.assertEqual(progress.longest_streak, 2)
		self.assertEqual(progress.points, 110)

	def test_missed_day_resets_the_streak_but_keeps_the_record(self):
		progress = StudyProgress.objects.create(
			user=self.user,
			points=40,
			cards_today=0,
			cards_today_date=timezone.localdate(),
			current_streak=4,
			longest_streak=4,
			last_streak_date=timezone.localdate() - timedelta(days=3),
		)

		for _ in range(10):
			self.review()

		progress.refresh_from_db()
		self.assertEqual(progress.current_streak, 1)
		self.assertEqual(progress.longest_streak, 4)
		self.assertEqual(progress.points, 110)

	def test_stale_streak_is_displayed_as_zero(self):
		StudyProgress.objects.create(
			user=self.user,
			current_streak=5,
			longest_streak=5,
			last_streak_date=timezone.localdate() - timedelta(days=2),
		)

		self.assertEqual(progress_summary(self.user)["streak"], 0)

	def test_yesterday_streak_still_counts_until_today_is_missed(self):
		StudyProgress.objects.create(
			user=self.user,
			current_streak=5,
			longest_streak=5,
			last_streak_date=timezone.localdate() - timedelta(days=1),
		)

		self.assertEqual(progress_summary(self.user)["streak"], 5)

	def test_level_boundaries(self):
		cases = [
			(0, 1),
			(239, 1),
			(240, 2),
			(639, 2),
			(640, 3),
			(1279, 3),
			(1280, 4),
			(13000, 10),
			(999999, 10),
		]
		for points, expected in cases:
			with self.subTest(points=points):
				self.assertEqual(level_for_points(points).number, expected)

	def test_level_progress_percentage(self):
		current, upcoming, percent = level_progress(120)

		self.assertEqual(current.number, 1)
		self.assertEqual(upcoming.number, 2)
		self.assertEqual(percent, 50)

		_top, upcoming, percent = level_progress(13000)
		self.assertIsNone(upcoming)
		self.assertEqual(percent, 100)

	def test_header_shows_streak_points_and_level(self):
		StudyProgress.objects.create(
			user=self.user,
			points=300,
			cards_today=4,
			cards_today_date=timezone.localdate(),
			current_streak=3,
			longest_streak=3,
			last_streak_date=timezone.localdate(),
		)

		response = self.client.get(reverse("card-list"))

		self.assertContains(response, "study-stats--desktop")
		self.assertContains(response, "study-stats--mobile")
		self.assertContains(response, "تلميذ الحروف")
		self.assertContains(response, "Letter Apprentice")
		self.assertContains(response, "<strong>300</strong> pts")
		self.assertContains(response, "4/10")

	def test_learning_toolbar_shows_todays_goal(self):
		StudyProgress.objects.create(
			user=self.user,
			points=12,
			cards_today=6,
			cards_today_date=timezone.localdate(),
			last_streak_date=timezone.localdate(),
			current_streak=1,
		)

		response = self.client.get(reverse("todo-list"))

		self.assertContains(response, "learning-goal")
		self.assertContains(response, "6/10")

	def test_progress_page_lists_every_level(self):
		response = self.client.get(reverse("progress"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "The road to professor")
		for level in LEVELS:
			self.assertContains(response, level.title)
			self.assertContains(response, level.arabic_title)

	def test_progress_page_explains_the_scoring(self):
		response = self.client.get(reverse("progress"))

		self.assertContains(response, "5 points for a correct answer")
		self.assertContains(response, "20 bonus points")
		self.assertContains(response, "10 cards")

	def test_reminder_form_normalizes_and_saves_the_number(self):
		response = self.client.post(
			reverse("progress"),
			{
				"phone_number": "+31 6 1234 5678",
				"reminder_time": "19:00",
				"enabled": "on",
			},
		)

		self.assertRedirects(response, reverse("progress"))
		reminder = StudyReminder.objects.get(user=self.user)
		self.assertEqual(reminder.phone_number, "+31612345678")
		self.assertTrue(reminder.enabled)
		self.assertEqual(reminder.reminder_time.strftime("%H:%M"), "19:00")

	def test_reminder_form_rejects_a_number_without_country_code(self):
		response = self.client.post(
			reverse("progress"),
			{
				"phone_number": "0612345678",
				"reminder_time": "19:00",
				"enabled": "on",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "international format")
		self.assertFalse(StudyReminder.objects.get(user=self.user).enabled)

	def test_enabling_reminders_requires_a_number(self):
		response = self.client.post(
			reverse("progress"),
			{
				"phone_number": "",
				"reminder_time": "19:00",
				"enabled": "on",
			},
		)

		self.assertContains(response, "Add a number to receive WhatsApp reminders.")

	def test_progress_page_does_not_show_server_setup(self):
		response = self.client.get(reverse("progress"))

		self.assertNotContains(response, "manage.py")
		self.assertNotContains(response, "WHATSAPP_ACCESS_TOKEN")
		self.assertNotContains(response, "cron")

	def test_progress_page_offers_a_whatsapp_deep_link(self):
		StudyReminder.objects.create(
			user=self.user,
			phone_number="+31612345678",
			enabled=True,
		)

		response = self.client.get(reverse("progress"))

		self.assertContains(response, "https://wa.me/31612345678?text=")
		self.assertContains(response, "Open WhatsApp reminder")

	def test_deep_link_encodes_the_message(self):
		link = whatsapp_deep_link("+31 6 1234 5678", "hello world")

		self.assertEqual(link, "https://wa.me/31612345678?text=hello%20world")

	def test_reminder_message_includes_progress_and_due_cards(self):
		StudyProgress.objects.create(
			user=self.user,
			points=42,
			cards_today=2,
			cards_today_date=timezone.localdate(),
			current_streak=3,
			longest_streak=3,
			last_streak_date=timezone.localdate(),
		)

		message = build_reminder_message(self.user, due_count=5)

		self.assertIn("3 days", message)
		self.assertIn("42 points", message)
		self.assertIn("5 cards ready", message)
		self.assertIn("طالب صغير", message)
		self.assertIn("2/10", message)


class SendStudyRemindersTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user("reminded")
		self.reminder = StudyReminder.objects.create(
			user=self.user,
			phone_number="+31612345678",
			enabled=True,
			reminder_time=timezone.localtime().replace(
				second=0,
				microsecond=0,
			).time(),
		)

	@patch("cards.management.commands.send_study_reminders.send_whatsapp_message")
	def test_sends_once_per_day(self, send_message):
		call_command("send_study_reminders", stdout=StringIO())
		call_command("send_study_reminders", stdout=StringIO())

		self.assertEqual(send_message.call_count, 1)
		self.reminder.refresh_from_db()
		self.assertEqual(self.reminder.last_sent_on, timezone.localdate())

	@patch("cards.management.commands.send_study_reminders.send_whatsapp_message")
	def test_dry_run_prints_without_sending(self, send_message):
		output = StringIO()

		call_command("send_study_reminders", "--dry-run", stdout=output)

		send_message.assert_not_called()
		self.assertIn("مرحبا reminded", output.getvalue())
		self.reminder.refresh_from_db()
		self.assertIsNone(self.reminder.last_sent_on)

	@patch("cards.management.commands.send_study_reminders.send_whatsapp_message")
	def test_skips_disabled_reminders(self, send_message):
		StudyReminder.objects.filter(pk=self.reminder.pk).update(enabled=False)

		call_command("send_study_reminders", stdout=StringIO())

		send_message.assert_not_called()

	@patch("cards.management.commands.send_study_reminders.send_whatsapp_message")
	def test_skips_reminders_outside_their_hour(self, send_message):
		outside = (
			timezone.localtime() - timedelta(hours=3)
		).replace(second=0, microsecond=0).time()
		StudyReminder.objects.filter(pk=self.reminder.pk).update(
			reminder_time=outside,
		)

		call_command("send_study_reminders", stdout=StringIO())

		send_message.assert_not_called()

	@patch(
		"cards.management.commands.send_study_reminders.send_whatsapp_message",
		side_effect=WhatsAppNotConfigured("missing credentials"),
	)
	def test_reports_missing_configuration(self, send_message):
		errors = StringIO()

		call_command("send_study_reminders", stderr=errors)

		self.assertIn("not configured", errors.getvalue())

	@patch("cards.management.commands.send_study_reminders.send_whatsapp_message")
	def test_force_resends_on_the_same_day(self, send_message):
		self.reminder.last_sent_on = timezone.localdate()
		self.reminder.save(update_fields=["last_sent_on"])

		call_command("send_study_reminders", "--force", stdout=StringIO())

		send_message.assert_called_once()

	@patch("cards.management.commands.send_study_reminders.send_whatsapp_message")
	def test_user_filter(self, send_message):
		other = get_user_model().objects.create_user("other-reminded")
		StudyReminder.objects.create(
			user=other,
			phone_number="+31698765432",
			enabled=True,
			reminder_time=self.reminder.reminder_time,
		)

		call_command(
			"send_study_reminders",
			"--user",
			"reminded",
			stdout=StringIO(),
		)

		self.assertEqual(send_message.call_count, 1)
		self.reminder.refresh_from_db()
		self.assertEqual(self.reminder.last_sent_on, timezone.localdate())
		self.assertIsNone(StudyReminder.objects.get(user=other).last_sent_on)


class AuthenticationFeatureTests(TestCase):
	password = "Correct-Horse-92!"

	def setUp(self):
		mail.outbox.clear()

	def create_user(self, username, **kwargs):
		kwargs.setdefault("password", self.password)
		return get_user_model().objects.create_user(username, **kwargs)

	def verify_email(self, user, *, verified=True, sent_at=None):
		return EmailVerification.objects.create(
			user=user,
			email=user.email,
			token=f"token-{user.pk}",
			sent_at=sent_at or timezone.now(),
			verified_at=timezone.now() if verified else None,
		)

	def test_signup_requires_an_email_address(self):
		response = self.client.post(
			reverse("signup"),
			{
				"username": "new-user",
				"password1": self.password,
				"password2": self.password,
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "This field is required.")
		self.assertFalse(
			get_user_model().objects.filter(username="new-user").exists()
		)

	def test_signup_stores_a_normalized_email_address(self):
		response = self.client.post(
			reverse("signup"),
			{
				"username": "new-user",
				"email": "New.User@Example.COM",
				"password1": self.password,
				"password2": self.password,
			},
		)

		self.assertRedirects(response, reverse("home"))
		user = get_user_model().objects.get(username="new-user")
		self.assertEqual(user.email, "new.user@example.com")
		self.assertIn("_auth_user_id", self.client.session)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, ["new.user@example.com"])
		self.assertEqual(
			mail.outbox[0].subject,
			"Verify your Flashcards email",
		)
		verification = EmailVerification.objects.get(user=user)
		self.assertEqual(verification.email, "new.user@example.com")
		self.assertFalse(verification.is_verified)

	def test_signup_rejects_a_duplicate_email_case_insensitively(self):
		self.create_user("existing-user", email="taken@example.com")

		response = self.client.post(
			reverse("signup"),
			{
				"username": "new-user",
				"email": "TAKEN@example.com",
				"password1": self.password,
				"password2": self.password,
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			"An account with this email already exists.",
		)
		self.assertFalse(
			get_user_model().objects.filter(username="new-user").exists()
		)

	def test_account_settings_updates_the_email_address(self):
		user = self.create_user(
			"settings-user",
			email="old@example.com",
		)
		self.verify_email(user)
		self.client.force_login(user)

		response = self.client.post(
			reverse("account-settings"),
			{"email": "updated@example.com"},
		)

		self.assertRedirects(response, reverse("account-settings"))
		user.refresh_from_db()
		self.assertEqual(user.email, "updated@example.com")
		verification = EmailVerification.objects.get(user=user)
		self.assertEqual(verification.email, "updated@example.com")
		self.assertFalse(verification.is_verified)
		self.assertEqual(len(mail.outbox), 1)

	def test_account_settings_rejects_another_users_email(self):
		self.create_user("other-user", email="taken@example.com")
		user = self.create_user("settings-user", email="mine@example.com")
		self.client.force_login(user)

		response = self.client.post(
			reverse("account-settings"),
			{"email": "TAKEN@example.com"},
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			"An account with this email already exists.",
		)
		user.refresh_from_db()
		self.assertEqual(user.email, "mine@example.com")

	def test_regular_account_can_share_a_staff_email(self):
		self.create_user(
			"admin-user",
			email="shared@example.com",
			is_staff=True,
		)
		user = self.create_user(
			"regular-user",
			email="mine@example.com",
		)
		self.client.force_login(user)

		response = self.client.post(
			reverse("account-settings"),
			{"email": "Shared@Example.com"},
		)

		self.assertRedirects(response, reverse("account-settings"))
		user.refresh_from_db()
		self.assertEqual(user.email, "shared@example.com")
		self.assertEqual(len(mail.outbox), 1)

	def test_staff_account_can_share_a_regular_users_email(self):
		self.create_user(
			"regular-user",
			email="shared@example.com",
		)
		admin = self.create_user(
			"admin-user",
			email="admin@example.com",
			is_staff=True,
		)
		self.client.force_login(admin)

		response = self.client.post(
			reverse("account-settings"),
			{"email": "shared@example.com"},
		)

		self.assertRedirects(response, reverse("account-settings"))
		admin.refresh_from_db()
		self.assertEqual(admin.email, "shared@example.com")

	def test_existing_account_without_email_is_forced_to_add_one(self):
		user = self.create_user("no-email-user")

		response = self.client.post(
			reverse("login"),
			{"username": user.username, "password": self.password},
		)

		self.assertRedirects(response, reverse("complete-email"))
		self.assertRedirects(
			self.client.get(reverse("home")),
			reverse("complete-email"),
		)

		response = self.client.get(reverse("complete-email"))
		self.assertContains(response, "Add your email address")
		self.assertNotContains(response, "Add an email address so you can reset")

		response = self.client.post(
			reverse("complete-email"),
			{"email": "new-email@example.com"},
		)

		self.assertRedirects(response, reverse("home"))
		user.refresh_from_db()
		self.assertEqual(user.email, "new-email@example.com")
		verification = EmailVerification.objects.get(user=user)
		self.assertFalse(verification.is_verified)
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(
			self.client.get(reverse("home")).status_code,
			200,
		)

	def test_email_verification_link_marks_the_address_verified(self):
		user = self.create_user(
			"verify-user",
			email="verify@example.com",
		)
		self.verify_email(user, verified=False)

		response = self.client.get(
			reverse("verify-email", args=[f"token-{user.pk}"]),
		)

		self.assertContains(response, "Email confirmed")
		verification = EmailVerification.objects.get(user=user)
		self.assertTrue(verification.is_verified)

	def test_expired_verification_link_is_rejected(self):
		user = self.create_user(
			"expired-verify-user",
			email="expired@example.com",
		)
		self.verify_email(
			user,
			verified=False,
			sent_at=timezone.now() - timedelta(days=2),
		)

		response = self.client.get(
			reverse("verify-email", args=[f"token-{user.pk}"]),
		)

		self.assertContains(response, "has expired")
		self.assertFalse(
			EmailVerification.objects.get(user=user).is_verified,
		)

	def test_invalid_verification_token_is_rejected(self):
		response = self.client.get(
			reverse("verify-email", args=["not-a-real-token"]),
		)

		self.assertContains(response, "invalid")

	def test_account_settings_shows_unverified_email_status(self):
		user = self.create_user(
			"status-user",
			email="status@example.com",
		)
		self.verify_email(user, verified=False)
		self.client.force_login(user)

		response = self.client.get(reverse("account-settings"))

		self.assertContains(response, "Not verified")
		self.assertContains(response, "Resend verification email")

	def test_resend_verification_email_sends_a_new_link(self):
		user = self.create_user(
			"resend-user",
			email="resend@example.com",
		)
		verification = self.verify_email(
			user,
			verified=False,
			sent_at=timezone.now() - timedelta(days=1),
		)
		old_token = verification.token
		self.client.force_login(user)

		response = self.client.post(
			reverse("resend-email-verification"),
		)

		self.assertRedirects(response, reverse("account-settings"))
		self.assertEqual(len(mail.outbox), 1)
		verification.refresh_from_db()
		self.assertNotEqual(verification.token, old_token)
		self.assertFalse(verification.is_verified)

	def test_login_page_offers_reset_and_password_visibility(self):
		response = self.client.get(reverse("login"))

		self.assertContains(response, reverse("password_reset"))
		self.assertContains(response, "Forgot your password?")
		self.assertContains(response, "cards/js/password_toggle.js")

	def test_password_pages_use_the_app_layout(self):
		user = self.create_user("layout-user", email="layout@example.com")
		self.client.force_login(user)

		for url, heading in (
			(reverse("password_reset"), "ACCOUNT RECOVERY"),
			(reverse("password_change"), "ACCOUNT SECURITY"),
		):
			with self.subTest(url=url):
				response = self.client.get(url)
				self.assertEqual(response.status_code, 200)
				self.assertContains(response, heading)
				self.assertContains(response, "auth-panel")
				self.assertTemplateUsed(response, "cards/base_cards.html")

	@override_settings(
		EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	)
	def test_password_reset_sends_a_link_and_changes_the_password(self):
		user = self.create_user(
			"reset-user",
			email="reset@example.com",
		)
		self.verify_email(user)

		response = self.client.post(
			reverse("password_reset"),
			{"email": "reset@example.com"},
		)

		self.assertRedirects(response, reverse("password_reset_done"))
		self.assertEqual(len(mail.outbox), 1)
		self.assertEqual(mail.outbox[0].to, ["reset@example.com"])
		self.assertEqual(
			mail.outbox[0].subject,
			"Reset your Flashcards password (reset-user)",
		)

		match = re.search(
			r"/accounts/password-reset/confirm/[^\s]+",
			mail.outbox[0].body,
		)
		self.assertIsNotNone(match)
		response = self.client.get(match.group(0))
		self.assertEqual(response.status_code, 302)

		new_password = "Another-Correct-Horse-93!"
		response = self.client.post(
			response.url,
			{
				"new_password1": new_password,
				"new_password2": new_password,
			},
		)

		self.assertRedirects(response, reverse("password_reset_complete"))
		user.refresh_from_db()
		self.assertTrue(user.check_password(new_password))

	@override_settings(
		EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	)
	def test_password_reset_emails_each_account_sharing_an_address(self):
		shared_email = "shared@example.com"
		regular = self.create_user(
			"regular-reset-user",
			email=shared_email,
		)
		admin = self.create_user(
			"admin-reset-user",
			email=shared_email,
			is_staff=True,
		)
		self.verify_email(regular)
		self.verify_email(admin)

		response = self.client.post(
			reverse("password_reset"),
			{"email": shared_email},
		)

		self.assertRedirects(response, reverse("password_reset_done"))
		self.assertEqual(len(mail.outbox), 2)
		self.assertEqual(
			{message.subject for message in mail.outbox},
			{
				"Reset your Flashcards password (regular-reset-user)",
				"Reset your Flashcards password (admin-reset-user)",
			},
		)
		self.assertEqual(
			{message.to[0] for message in mail.outbox},
			{shared_email},
		)

	@override_settings(
		EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
	)
	def test_password_reset_does_not_email_unknown_addresses(self):
		response = self.client.post(
			reverse("password_reset"),
			{"email": "unknown@example.com"},
		)

		self.assertRedirects(response, reverse("password_reset_done"))
		self.assertEqual(len(mail.outbox), 0)

	def test_password_reset_does_not_email_unverified_addresses(self):
		user = self.create_user(
			"unverified-reset-user",
			email="unverified-reset@example.com",
		)
		self.verify_email(user, verified=False)

		response = self.client.post(
			reverse("password_reset"),
			{"email": "unverified-reset@example.com"},
		)

		self.assertRedirects(response, reverse("password_reset_done"))
		self.assertEqual(len(mail.outbox), 0)

	def test_idle_session_is_logged_out(self):
		user = self.create_user("idle-user")
		self.client.force_login(user)
		session = self.client.session
		session["last_activity"] = time.time() - 301
		session.save()

		response = self.client.get(reverse("home"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?reason=idle",
		)
		self.assertNotIn("_auth_user_id", self.client.session)

	def test_activity_keeps_the_session_alive(self):
		user = self.create_user(
			"active-user",
			email="active@example.com",
		)
		self.client.force_login(user)

		response = self.client.get(reverse("home"))

		self.assertEqual(response.status_code, 200)
		self.assertIn("last_activity", self.client.session)

	def test_idle_logout_endpoint_clears_the_session(self):
		user = self.create_user("idle-endpoint-user")
		self.client.force_login(user)

		response = self.client.post(reverse("idle-logout"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?reason=idle",
		)
		self.assertNotIn("_auth_user_id", self.client.session)

	def test_tab_session_expired_endpoint_clears_the_session(self):
		user = self.create_user("tab-endpoint-user")
		self.client.force_login(user)

		response = self.client.post(reverse("tab-session-expired"))

		self.assertRedirects(
			response,
			f"{reverse('login')}?reason=expired",
		)
		self.assertNotIn("_auth_user_id", self.client.session)

	def test_session_heartbeat_requires_authentication(self):
		response = self.client.post(reverse("session-heartbeat"))

		self.assertEqual(response.status_code, 401)

	def test_session_heartbeat_updates_activity(self):
		user = self.create_user("heartbeat-user")
		self.client.force_login(user)

		response = self.client.post(reverse("session-heartbeat"))

		self.assertEqual(response.status_code, 204)
		self.assertIn("last_activity", self.client.session)

	def test_tab_session_start_clears_the_login_handshake(self):
		user = self.create_user("tab-start-user")
		self.client.force_login(user)
		self.assertIn("tab_session_handshake", self.client.session)

		response = self.client.post(reverse("tab-session-start"))

		self.assertEqual(response.status_code, 204)
		self.assertNotIn("tab_session_handshake", self.client.session)
