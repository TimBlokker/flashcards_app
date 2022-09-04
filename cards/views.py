from unicodedata import category
from django.shortcuts import render
from django.urls import reverse_lazy
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
from .forms import CardCategoriesFilterForm
from django.shortcuts import render
from .models import CAT_CHOICES

class FilterView(ListView):
    model = Card
    form_class = CardCategoriesFilterForm
    template = "cards/filter_category.html"
    initial = {'category': 'F6'}
    
    def get_queryset(self):
        return Card.objects.all().order_by("box", "-date_created")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


    def get(self, request, *args, **kwargs):

            form = self.form_class(request.GET)
            if form.is_valid():
                categoryForm = form.cleaned_data["category"]
            else:
                form = self.form_class(self.initial)
                categoryForm = form.data["category"]
            request.session['category']=categoryForm
            self.object_list = self.get_queryset()
            self.queryset = self.get_queryset()
            queryset = self.queryset.filter(category = categoryForm )
            context = self.get_context_data(**kwargs)
            context["object_list"] = queryset
            context["card_list"]  = queryset
            context["form"] = form
            context["category"] = categoryForm
            return render(request, self.template, context)

class CardListView(ListView):
    model = Card
    template = "cards/card_list.html"
    def get_queryset(self):
            return Card.objects.all().order_by("box", "-date_created").filter(category = self.request.session["category"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["category"]=self.request.session["category"]
        return context

class CardCreateView(CreateView):
    model = Card
    fields = ["question", "answer", "box", "category"]
    success_url = reverse_lazy("card-create")

class CardUpdateView(CardCreateView, UpdateView):
    success_url = reverse_lazy("card-list")

class BoxView(CardListView):
    template_name = "cards/box.html"
    def get_queryset(self):
        return Card.objects.all().filter(box=self.kwargs["box_num"]).filter(category = self.request.session["category"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["box_number"] = self.kwargs["box_num"]
        context["object_list"] = self.get_queryset()
        context["category"] = self.request.session["category"]
        return context

class CardRepeatView(CardListView):
    template_name = "cards/repeat.html"
    form_class = CardCheckForm

    def get_queryset(self):
        object_list = [card for card in Card.objects.all().filter(category =self.request.session["category"]) if card.to_repeat]
        return object_list

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object_list"] = self.get_queryset()
        context["card_count"] = len(self.get_queryset())
        context["category"] = self.request.session["category"]

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
