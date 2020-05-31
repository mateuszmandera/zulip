from django.db import migrations
from django.db.backends.postgresql.schema import DatabaseSchemaEditor
from django.db.migrations.state import StateApps

PRESENCE_ACTIVE_STATUS = 1
PRESENCE_IDLE_STATUS = 2

def fill_new_columns(apps: StateApps, schema_editor: DatabaseSchemaEditor) -> None:
    UserPresence = apps.get_model('zerver', 'UserPresence')
    UserPresenceNew = apps.get_model('zerver', 'UserPresenceNew')
    latest_active_presence_per_user = list(UserPresence.objects.filter(status=PRESENCE_ACTIVE_STATUS)
                                           .order_by('user_profile', '-timestamp').distinct('user_profile'))
    latest_idle_presence_per_user = list(UserPresence.objects.filter(status=PRESENCE_IDLE_STATUS)
                                         .order_by('user_profile', '-timestamp').distinct('user_profile'))

    user_profile_list = [
        presence.user_profile for presence in latest_active_presence_per_user + latest_idle_presence_per_user
    ]
    user_id_to_user_profile = {user_profile.id: user_profile for user_profile in user_profile_list}

    user_id_to_presence_info = {}
    for active_presence in latest_active_presence_per_user:
        user_id_to_presence_info[active_presence.user_profile_id] = dict(
            last_active_time=active_presence.timestamp,
            last_connected_time=active_presence.timestamp
        )

    for idle_presence in latest_idle_presence_per_user:
        user_id = idle_presence.user_profile_id
        if user_id not in user_id_to_presence_info:
            user_id_to_presence_info[user_id] = dict(last_active_time=idle_presence.timestamp,
                                                     last_connected_time=idle_presence.timestamp)
        else:
            last_connected_time = max(user_id_to_presence_info[user_id]['last_connected_time'],
                                      idle_presence.timestamp)
            user_id_to_presence_info[user_id]['last_connected_time'] = last_connected_time

    UserPresenceNew.objects.bulk_create(
        [UserPresenceNew(user_profile_id=user_id, realm_id=user_id_to_user_profile[user_id].realm_id,
                         last_connected_time=presence_info['last_connected_time'],
                         last_active_time=presence_info['last_active_time'])
         for user_id, presence_info in user_id_to_presence_info.items()]
    )

class Migration(migrations.Migration):
    """
    Ports data from the UserPresence model into the new one.
    """

    dependencies = [
        ('zerver', '0281_userpresencenew'),
    ]

    operations = [
        migrations.RunPython(fill_new_columns)
    ]
