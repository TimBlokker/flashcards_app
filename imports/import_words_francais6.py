import pandas as pd
import numpy as np
import datetime
from cards.models import Card
#import odf 

cards = pd.read_csv("/home/tim/flashcards_app/imports/Vocab_12_09_22.ods")
cards["answer"] = cards["FR"]
cards["de"] =  cards["DE"]
cards["en"] =  cards["EN"]

cards["date_moved"] = datetime.datetime.now(datetime.timezone.utc)
cards["date_created"] = datetime.datetime.now(datetime.timezone.utc)
cards["category"] = "F6"
cards["box"] = 1


#cards.to_csv("/home/tim/flashcards_app/imports/12_Sep_22_vocabulary.csv")

for i in range(0, len(cards["answer"])):
    Card(answer= cards["answer"][i], date_moved= cards["date_moved"][i], category= cards["category"][i], box= cards["box"][i], date_created= cards["date_created"][i], question_en= cards["en"][i] ,question_de = cards["de"][i]).save()



