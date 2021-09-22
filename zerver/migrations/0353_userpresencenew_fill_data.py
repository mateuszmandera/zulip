from django.db import migrations
from django.db.backends.postgresql.schema import DatabaseSchemaEditor
from django.db.migrations.state import StateApps

PRESENCE_ACTIVE_STATUS = 1
PRESENCE_IDLE_STATUS = 2


def fill_new_columns(apps: StateApps, schema_editor: DatabaseSchemaEditor) -> None:
    UserPresence = apps.get_model("zerver", "UserPresence")
    UserPresenceNew = apps.get_model("zerver", "UserPresenceNew")

    # In theory, we'd like to preserve the distinction between the
    # IDLE and ACTIVE statuses in legacy data.  However, there is no
    # correct way to do so; the previous data structure only stored
    # the current IDLE/ACTIVE status of the last update for each
    # (user, client) pair. There's no way to know whether the last
    # time the user had the other status with that client was minutes
    # or months beforehand.
    #
    # So the only sane thing we can do with this migration is to treat
    # the last presence update as having been a PRESENCE_ACTIVE_STATUS
    # event. This will result in some currently-idle users being
    # incorrectly recorded as having been active at the last moment
    # that they were idle before this migration.  This error is
    # unlikely to be significant in practice, and in any case is an
    # unavoidable flaw caused by the legacy previous data model.
    latest_presence_per_user = (
        UserPresence.objects.filter(
            status__in=[
                PRESENCE_IDLE_STATUS,
                PRESENCE_ACTIVE_STATUS,
            ]
        )
        .order_by("user_profile", "-timestamp")
        .distinct("user_profile")
        .values("user_profile_id", "timestamp", "user_profile__realm_id")
    )

    UserPresenceNew.objects.bulk_create(
        [
            UserPresenceNew(
                user_profile_id=presence_row["user_profile_id"],
                realm_id=presence_row["user_profile__realm_id"],
                last_connected_time=presence_row["timestamp"],
                last_active_time=presence_row["timestamp"],
            )
            for presence_row in latest_presence_per_user
        ],
        # Limit the size of individual network requests for very large
        # servers.
        batch_size=10000,
    )


def clear_new_columns(apps: StateApps, schema_editor: DatabaseSchemaEditor) -> None:
    UserPresenceNew = apps.get_model("zerver", "UserPresenceNew")
    UserPresenceNew.objects.all().delete()


class Migration(migrations.Migration):
    """
    Ports data from the UserPresence model into the new one.
    """

    atomic = False

    dependencies = [
        ("zerver", "0352_userpresencenew"),
    ]

    operations = [migrations.RunPython(fill_new_columns, reverse_code=clear_new_columns)]
