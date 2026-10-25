from os import times
from time import time
from django.conf import settings
from django.db import models
from itertools import chain
import datetime
from django.core.exceptions import ValidationError
# Create your models here.
NUM_BOXES = 6
BOXES = range(1, NUM_BOXES + 1)


class Card(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="cards",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    answer =  models.CharField(max_length = 300)
    answer_vocalized = models.TextField(blank=True)
    question = models.CharField(max_length=500)

    box = models.IntegerField(
        choices=zip(BOXES, BOXES),
        default=BOXES[0],
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_moved = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "question"],
                name="uniq_card_user_question",
                condition=~models.Q(question=""),
            ),
        ]

    def __str__(self):
        return self.question


    def move(self, solved):
        new_box = self.box + 1 if solved else BOXES[0]
        if new_box in BOXES:
            self.box = new_box
            self.date_moved = datetime.datetime.now(datetime.timezone.utc)
            self.save()
        return self

    @property
    def to_repeat(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        min_age = {
        1:1,
        2:2,
        3:4,
        4:7,
        5:14,
        6:30
        }
        to_repeat = (now - self.date_moved- datetime.timedelta(days=min_age[self.box])).total_seconds() > 0
        return to_repeat

class DictionaryEntry(models.Model):
    language_pair = models.CharField(max_length=16, default="en-ar")
    headword = models.CharField(max_length=255)
    search_key = models.CharField(max_length=255, blank=True, default="", db_collation="nocase")
    translation = models.TextField()
    vocalized_translation = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["language_pair", "search_key"], name="dict_pair_search_idx"),
        ]

    def __str__(self):
        return self.headword

    def save(self, *args, **kwargs):
        if not self.search_key:
            self.search_key = self.headword.casefold()
        super().save(*args, **kwargs)