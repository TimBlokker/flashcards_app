import pandas as pd
import numpy as np
import datetime
from cards.models import Card
tree = pd.read_xml('lexique-1.1.xml', parser = "etree")
tree2 = pd.read_xml('lexique-2.0.xml', parser = "etree")
tree = tree.iloc[:,0:9]
tree.columns == tree2.columns
tree=tree.reset_index(drop=True)
tree2=tree2.reset_index(drop=True)
merged_tree = tree.merge(tree2, "outer")
merged_tree.duplicated()
tree = tree2
tree["date_moved"] = datetime.datetime.now(datetime.timezone.utc)
tree["date_created"] = datetime.datetime.now(datetime.timezone.utc)
tree["category"] = "F5"
tree["box"] = 1
tree["question"] = tree["en"]
tree["answer"] = tree["fr"]


splitted1 = [word[1] for word in tree["definition"].str.split("<i>")]
splitted2 = [word.split("</i")[0] for word in splitted1]
splitted3 = [word.split(" ")[0] for word in splitted2]
splitted4 = [word.split(".")[1].strip() for word in splitted2]

dict = { 
    'm' : "le",
    'f' : "la",
    ''  : '',
    'pron': 'pron',
    'fam': 'fam',
    'inv': 'inv',
    'indéf':'indéf'}

genders = [dict[word] for word in splitted4]
word_type = splitted3


tree["answer"] = ["".join((genders[i], " ", tree["fr"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]


tree["de"] = ["".join((tree["de"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]
tree["pt"] = ["".join((tree["pt"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]
tree["it"] = ["".join((tree["it"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]
tree["pl"] = ["".join((tree["pl"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]
tree["en"] = ["".join((tree["en"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]
tree["es"] = ["".join((tree["es"][i], " (", word_type[i], ")")) for i in range(0,len(splitted3))]

tree[ ["date_moved", "box", "category", "answer", "question", "de", "pt", "it", "pl", "en", "es"]]

for i in range(0, len(splitted3)):
    Card(answer= tree["answer"][i], date_moved= tree["date_moved"][i], category= tree["category"][i], box= tree["box"][i], date_created= tree["date_created"][i], question_en= tree["en"][i] ,question_de = tree["de"][i],  question_pt= tree["pt"][i], question_it= tree["it"][i], question_pl= tree["pl"][i], question_es= tree["es"][i]).save()



