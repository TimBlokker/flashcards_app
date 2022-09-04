from django.urls import path
from . import views

urlpatterns = [
    path(
        "",
        views.FilterView.as_view(),
        name="fiter-categories"
    ),
     path(
        "list",
        views.CardListView.as_view(),
        name="card-list"
    ),
     path(
        "new",
        views.CardCreateView.as_view(),
        name="card-create"
    ),
    path(
        "edit/<int:pk>",
        views.CardUpdateView.as_view(),
        name="card-update"
    ),
    path(
        "box/<int:box_num>",
        views.BoxView.as_view(),
        name="box"
    ),
    path(
        "repeat",
        views.CardRepeatView.as_view(),
        name="todo-list"
    ), 
]
