from os import times
from time import time
from django.db import models
from itertools import chain
import datetime
# Create your models here.
NUM_BOXES = 6
BOXES = range(1, NUM_BOXES + 1)
CAT_CHOICES = (
    ('Test_Card','test cards'),
    ('livres', 'Livres/Journaux'),
    ('F5','Francais 5'),
    ('F6','Francais 6'),
    ('F7','Francais 7'),
)

class Card(models.Model):
    question = models.CharField(max_length=100)
    answer = models.CharField(max_length=100)
    box = models.IntegerField(
        choices=zip(BOXES, BOXES),
        default=BOXES[0],
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_moved = models.DateTimeField(auto_now_add=True)
    category = models.CharField(max_length=9, choices= CAT_CHOICES,
    default='F6')

    def __str__(self):
        return self.question


    def move(self, solved):
        new_box = self.box + 1 if solved else BOXES[0]
        if new_box in BOXES:
            self.box = new_box
            self.save()
            date_moved = datetime.datetime.now(datetime.timezone.utc)
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

