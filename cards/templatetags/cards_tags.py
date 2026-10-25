from django import template
from cards.arabic import contains_arabic
from cards.models import BOXES, Card
from cards.progress import progress_summary

register = template.Library()


@register.filter
def has_arabic(value):
    return contains_arabic(value)


def _get_navigation_data(request):
    cached_data = getattr(request, "_cards_navigation_data", None)
    if cached_data is not None:
        return cached_data

    box_counts = {box_num: 0 for box_num in BOXES}
    repeat_count = 0
    cards = Card.objects.filter(user=request.user).only("box", "date_moved")
    for card in cards:
        box_counts[card.box] = box_counts.get(card.box, 0) + 1
        if card.to_repeat:
            repeat_count += 1

    cached_data = {
        "boxes": [
            {"number": box_num, "card_count": box_counts[box_num]}
            for box_num in BOXES
        ],
        "repeat_count": repeat_count,
    }
    request._cards_navigation_data = cached_data
    return cached_data


@register.inclusion_tag("cards/box_links.html", takes_context=True)
def boxes_as_links(context):
    return {"boxes": _get_navigation_data(context["request"])["boxes"]}


@register.inclusion_tag("cards/repeat_link.html", takes_context=True)
def repeat_box_as_link(context):
    return {
        "repeat_count": _get_navigation_data(context["request"])["repeat_count"]
    }


def _get_progress_summary(request):
    summary = getattr(request, "_cards_progress_summary", None)
    if summary is None:
        summary = progress_summary(request.user)
        request._cards_progress_summary = summary
    return summary


@register.inclusion_tag("cards/study_stats.html", takes_context=True)
def study_stats(context, variant="desktop"):
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return {"show": False}

    return {
        **_get_progress_summary(request),
        "show": True,
        "variant": variant,
    }


@register.inclusion_tag("cards/account_menu.html", takes_context=True)
def account_menu(context):
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return {"show": False}

    return {
        **_get_progress_summary(request),
        "show": True,
        "user": request.user,
    }
