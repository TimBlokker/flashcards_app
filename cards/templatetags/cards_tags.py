from timeit import repeat
from unicodedata import category
from django import template
from cards.models import BOXES, Card

register = template.Library()

@register.inclusion_tag("cards/box_links.html")
def boxes_as_links():
    boxes = []
    for box_num in BOXES:
        card_count = Card.objects.filter(box=box_num).count()
        boxes.append({
            "number": box_num,
            "card_count": card_count,
        })
    return {"boxes": boxes}

@register.inclusion_tag("cards/repeat_link.html")
def repeat_box_as_link(): 
    repeat_count=0
    for card in Card.objects.all():
        if(card.to_repeat):
            repeat_count = repeat_count +1

    return {"repeat_count":repeat_count}