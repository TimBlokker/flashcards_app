# cards/forms.py
from unicodedata import category
from django import forms
from django.forms import ModelForm
from .models import CAT_CHOICES
from .models import Card
from .models import LANG_CHOICES

class CardCheckForm(forms.Form):
    card_id = forms.IntegerField(required=True)
    solved = forms.BooleanField(required=False)

class CardUpdateForm(forms.Form):
    card_id = forms.IntegerField(required=True)
    solved = forms.BooleanField(required=False)

class CardCategoriesFilterForm(forms.Form):
    category= forms.ChoiceField(choices= CAT_CHOICES)
    language= forms.ChoiceField(choices= LANG_CHOICES)

