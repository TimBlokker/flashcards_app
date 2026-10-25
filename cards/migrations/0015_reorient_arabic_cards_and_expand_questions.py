from django.db import migrations, models


def _contains_arabic(value):
    return any(
        "\u0600" <= character <= "\u06ff"
        or "\u0750" <= character <= "\u077f"
        or "\u08a0" <= character <= "\u08ff"
        for character in value
    )


def reorient_arabic_cards(apps, schema_editor):
    Card = apps.get_model("cards", "Card")
    database = schema_editor.connection.alias
    cards_to_update = []

    for card in Card.objects.using(database).only(
        "pk",
        "question",
        "answer",
        "answer_vocalized",
    ).iterator():
        if _contains_arabic(card.question) and not _contains_arabic(card.answer):
            card.question, card.answer = card.answer, card.question
            card.answer_vocalized = ""
            cards_to_update.append(card)
            if len(cards_to_update) == 500:
                Card.objects.using(database).bulk_update(
                    cards_to_update,
                    ["question", "answer", "answer_vocalized"],
                )
                cards_to_update.clear()

    if cards_to_update:
        Card.objects.using(database).bulk_update(
            cards_to_update,
            ["question", "answer", "answer_vocalized"],
        )


def reverse_reorientation_if_unambiguous(apps, schema_editor):
    Card = apps.get_model("cards", "Card")
    database = schema_editor.connection.alias
    for card in Card.objects.using(database).only("question", "answer").iterator():
        if not _contains_arabic(card.question) and _contains_arabic(card.answer):
            raise RuntimeError(
                "The Arabic card reorientation cannot be safely reversed because "
                "the database does not record which English-to-Arabic cards "
                "were originally reversed."
            )


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0014_remove_card_category_and_account_category_settings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="card",
            name="question",
            field=models.CharField(max_length=500),
        ),
        migrations.RunPython(
            reorient_arabic_cards,
            reverse_reorientation_if_unambiguous,
        ),
    ]
