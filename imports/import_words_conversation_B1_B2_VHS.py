from ODSReader import ODSReader
import sys
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
os.environ['DJANGO_SETTINGS_MODULE'] = 'flashcards.settings'
import django
django.setup()
import pandas as pd
import numpy as np
import datetime
from cards.models import Card
cards = ODSReader("verlan.ods")
cards["answer"] = cards["french"]
cards["de"] =  cards["german"]
cards["date_moved"] = datetime.datetime.now(datetime.timezone.utc)
cards["date_created"] = datetime.datetime.now(datetime.timezone.utc)
cards["category"] = "VHS_B1/B2"
cards["box"] = 1



for i in range(0, len(cards["answer"])):
    Card(answer= cards["answer"][i], date_moved= cards["date_moved"][i], category= cards["category"][i], box= cards["box"][i], date_created=
 cards["date_created"][i], question_de = cards["de"][i]).save()