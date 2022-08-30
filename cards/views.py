from unicodedata import category
from django.shortcuts import render
from django.urls import reverse_lazy
from datetime import datetime, timedelta, timezone
from django.views.generic import (
    ListView,
    CreateView,
    UpdateView,
)

from itertools import chain
from .models import Card
import random
from django.shortcuts import get_object_or_404, redirect
from .forms import CardCheckForm

class CardListView(ListView):
    model = Card
    queryset = Card.objects.all().order_by("box", "-date_created")

class CardCreateView(CreateView):
    model = Card
    fields = ["question", "answer", "box", "category"]
    success_url = reverse_lazy("card-create")

class CardUpdateView(CardCreateView, UpdateView):
    success_url = reverse_lazy("card-list")

class BoxView(CardListView):
    template_name = "cards/box.html"
    def get_queryset(self):
        return Card.objects.filter(box=self.kwargs["box_num"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["box_number"] = self.kwargs["box_num"]
        context["object_list"] = self.get_queryset()
        return context

class CardRepeatView(CardListView):
    template_name = "cards/repeat.html"
    form_class = CardCheckForm

    def get_queryset(self):
        object_list = [card for card in Card.objects.all() if card.to_repeat]
        return object_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_list"] = self.get_queryset()
        context["card_count"] = len(self.get_queryset())
        self.queryset = self.object_list
        if context["card_count"] >0:
            context["check_card"] = random.choice(self.get_queryset())

        return context

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST)
        if form.is_valid():
            card = get_object_or_404(Card, id=form.cleaned_data["card_id"])
            card.move(form.cleaned_data["solved"])
            card.to_repeat
        return redirect(request.META.get("HTTP_REFERER"))
