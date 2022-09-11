from os import times
from time import time
from django.db import models
from itertools import chain
import datetime
from django.core.exceptions import ValidationError
# Create your models here.
NUM_BOXES = 6
BOXES = range(1, NUM_BOXES + 1)
CAT_CHOICES = [
    ('Test_Card','test cards'),
    ('livres', 'Livres/Journaux'),
    ('F5','Francais 5'),
    ('F6','Francais 6'),
    ('F7','Francais 7'),
]

LANG_CHOICES = [
    ('de', 'Allemand'),
    ('en', 'Anglais'),
    ('pl', 'polognais'),
    ('it', 'italien'),
    ('pt', 'portogaise'),
    ('es', 'espagnol'),
]

class Card(models.Model):
    answer =  models.CharField(max_length = 100)
    question_en = models.CharField(max_length = 100)
    question_es = models.CharField(max_length = 100)
    question_de = models.CharField(max_length = 100)
    question_it = models.CharField(max_length = 100)
    question_pt = models.CharField(max_length = 100)
    question_pl = models.CharField(max_length = 100)

    box = models.IntegerField(
        choices=zip(BOXES, BOXES),
        default=BOXES[0],
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_moved = models.DateTimeField(auto_now_add=True)
    category = models.CharField(max_length=9, choices= CAT_CHOICES,    default='F6')
    language = models.CharField(max_length=15, choices = LANG_CHOICES, default ="de" )

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
        1:-1,
        2:2,
        3:4,
        4:7,
        5:14,
        6:30
        }
        to_repeat = (now - self.date_moved- datetime.timedelta(days=min_age[self.box])).total_seconds() > 0 
        return to_repeat

    class Meta:
        unique_together = ('answer', 'question_de',) # should it be unique across categories?  
        unique_together = ('answer', 'question_en',)        
        unique_together = ('answer', 'question_pl',)        
        unique_together = ('answer', 'question_it',)        
        unique_together = ('answer', 'question_pt',)        
        unique_together = ('answer', 'question_es',)