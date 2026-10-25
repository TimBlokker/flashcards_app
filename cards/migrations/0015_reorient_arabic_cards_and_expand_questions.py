from django.db import migrations, models


def _contains_arabic(value):
    return any(
        "\u0600" <= character <= "\u06ff"
        or "\u0750" <= character <= "\u077f"
        or "\u08a0" <= character <= "\u08ff"
        for character in value
    )


def _reorientation_conflicts(Card, database):
    projected_questions = {}
    for card in Card.objects.using(database).only(
        "pk",
        "user_id",
        "question",
        "answer",
    ).iterator():
        reoriented = (
            _contains_arabic(card.question)
            and not _contains_arabic(card.answer)
        )
        question = card.answer if reoriented else card.question
        if card.user_id is None or not question:
            continue

        key = (card.user_id, question)
        projected_questions.setdefault(key, []).append((card.pk, reoriented))

    return [
        (user_id, question, cards)
        for (user_id, question), cards in projected_questions.items()
        if len(cards) > 1 and any(reoriented for _, reoriented in cards)
    ]


def check_reorientation_conflicts(apps, schema_editor):
    Card = apps.get_model("cards", "Card")
    database = schema_editor.connection.alias
    conflicts = _reorientation_conflicts(Card, database)
    if not conflicts:
        return

    report_limit = 50
    report_lines = [
        "Arabic card reorientation preflight found "
        f"{len(conflicts)} question collision(s). Resolve these conflicts "
        "before retrying the migration:"
    ]
    for user_id, question, cards in conflicts[:report_limit]:
        reoriented_ids = [
            card_id for card_id, reoriented in cards if reoriented
        ]
        existing_ids = [
            card_id for card_id, reoriented in cards if not reoriented
        ]
        report_lines.append(
            f"- user_id={user_id}, question={question!r}, "
            f"reoriented_card_ids={reoriented_ids}, "
            f"existing_card_ids={existing_ids}"
        )
    if len(conflicts) > report_limit:
        report_lines.append(
            f"- {len(conflicts) - report_limit} additional collision(s) omitted"
        )

    raise RuntimeError("\n".join(report_lines))


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
        migrations.RunPython(
            check_reorientation_conflicts,
            migrations.RunPython.noop,
        ),
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
