from django.db import migrations
from django.db.backends.postgresql.schema import DatabaseSchemaEditor
from django.db.migrations.state import StateApps

PRESENCE_ACTIVE_STATUS = 1
PRESENCE_IDLE_STATUS = 2


def fill_new_columns(apps: StateApps, schema_editor: DatabaseSchemaEditor) -> None:
    UserProfile = apps.get_model("zerver", "UserProfile")
    UserPresence = apps.get_model("zerver", "UserPresence")
    UserPresenceNew = apps.get_model("zerver", "UserPresenceNew")
    latest_active_presence_per_user = list(
        UserPresence.objects.filter(status=PRESENCE_ACTIVE_STATUS)
        .order_by("user_profile", "-timestamp")
        .distinct("user_profile")
    )
    latest_idle_presence_per_user = list(
        UserPresence.objects.filter(status=PRESENCE_IDLE_STATUS)
        .order_by("user_profile", "-timestamp")
        .distinct("user_profile")
    )

    user_profile_id_list = [
        presence.user_profile_id
        for presence in latest_active_presence_per_user + latest_idle_presence_per_user
    ]
    user_id_to_realm_id = {
        user["id"]: user["realm_id"]
        for user in UserProfile.objects.filter(id__in=user_profile_id_list).values("id", "realm_id")
    }

    user_id_to_presence_info = {}
    for active_presence in latest_active_presence_per_user:
        # The simple case is where our last old-style presence input
        # had the user active: last_active_time and
        # last_connected_time can both have that value.
        user_id_to_presence_info[active_presence.user_profile_id] = dict(
            last_active_time=active_presence.timestamp,
            last_connected_time=active_presence.timestamp,
        )

    for idle_presence in latest_idle_presence_per_user:
        # We cannot faithfully convert the data for users whose last
        # data is "idle".

        user_id = idle_presence.user_profile_id
        if user_id not in user_id_to_presence_info:
            user_id_to_presence_info[user_id] = dict(
                last_active_time=idle_presence.timestamp,
                last_connected_time=idle_presence.timestamp,
            )
        else:
            # If one client has an IDLE
            last_connected_time = max(
                user_id_to_presence_info[user_id]["last_connected_time"], idle_presence.timestamp
            )
            user_id_to_presence_info[user_id]["last_connected_time"] = last_connected_time

    UserPresenceNew.objects.bulk_create(
        [
            UserPresenceNew(
                user_profile_id=user_id,
                realm_id=user_id_to_realm_id[user_id],
                last_connected_time=presence_info["last_connected_time"],
                last_active_time=presence_info["last_active_time"],
            )
            for user_id, presence_info in user_id_to_presence_info.items()
        ]
    )


def clear_new_columns(apps: StateApps, schema_editor: DatabaseSchemaEditor) -> None:
    UserPresenceNew = apps.get_model("zerver", "UserPresenceNew")
    UserPresenceNew.objects.all().delete()


class Migration(migrations.Migration):
    """
    Ports data from the UserPresence model into the new one.
    """

    dependencies = [
        ("zerver", "0352_userpresencenew"),
    ]

    operations = [migrations.RunPython(fill_new_columns, reverse_code=clear_new_columns)]
