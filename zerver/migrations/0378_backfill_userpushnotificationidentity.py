import uuid

from django.db import migrations, models
from django.db.backends.postgresql.schema import DatabaseSchemaEditor
from django.db.migrations.state import StateApps


def backfill_userpushnotificationidentity(
    apps: StateApps, schema_editor: DatabaseSchemaEditor
) -> None:
    UserProfile = apps.get_model("zerver", "UserProfile")
    UserPushNotificationIdentity = apps.get_model("zerver", "UserPushNotificationIdentity")

    max_id = UserProfile.objects.aggregate(models.Max("id"))["id__max"]
    if max_id is None:
        # Nothing to do if there are no users yet.
        return

    BATCH_SIZE = 10000
    lower_bound = 1

    exists_expression = models.Exists(
        UserPushNotificationIdentity.objects.filter(
            user_profile_id=models.OuterRef("id"),
        )
    )

    while lower_bound <= max_id:
        objects_to_create = []
        for user_profile_id in (
            UserProfile.objects.filter(id__gte=lower_bound, id__lte=lower_bound + BATCH_SIZE)
            .annotate(has_push_notif_identity=exists_expression)
            .filter(has_push_notif_identity=False)
            .values_list("id", flat=True)
        ):
            objects_to_create.append(
                UserPushNotificationIdentity(user_profile_id=user_profile_id, uuid=uuid.uuid4())
            )
        lower_bound += BATCH_SIZE + 1

        UserPushNotificationIdentity.objects.bulk_create(objects_to_create)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("zerver", "0377_userpushnotificationidentity"),
    ]

    operations = [
        migrations.RunPython(
            backfill_userpushnotificationidentity, reverse_code=migrations.RunPython.noop
        ),
    ]
