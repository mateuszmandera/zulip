# Generated by Django 2.2.12 on 2020-05-30 14:36

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    First step of migrating to a new UserPresence data model. Renames
    the old UserPresence table and creates a table with the intended fields in its place,
    into which in the next step
    data can be ported over from the current UserPresence model.
    In the last step, the old, renamed table will be dropped.
    """

    dependencies = [
        ("zerver", "0387_reupload_realmemoji_again"),
    ]

    operations = [
        # Django doesn't rename indexes and constraints when renaming a table. This means that
        # after renaming UserPresence->UserPresenceOld the UserPresenceOld indexes/constraints
        # retain their old name causing a conflict when CreateModel tries to create them for
        # the new UserPresence table. Thus we have to manually rename them all.
        # The list of indexes and constraints to rename was obtained through
        # `\d zulip.zerver_userpresence` in psql.
        migrations.RunSQL(
            """
            ALTER INDEX zerver_userpresence_pkey RENAME TO zerver_userpresenceold_pkey;
            ALTER INDEX zerver_userpresence_user_profile_id_client_id_5fdcf4f4_uniq RENAME TO zerver_userpresenceold_user_profile_id_client_id_5fdcf4f4_uniq;
            ALTER INDEX zerver_userpresence_client_id_ed703e94 RENAME TO zerver_userpresenceold_client_id_ed703e94;
            ALTER INDEX zerver_userpresence_realm_id_5c4ef5a9 RENAME TO zerver_userpresenceold_realm_id_5c4ef5a9;
            ALTER INDEX zerver_userpresence_realm_id_timestamp_25f410da_idx RENAME TO zerver_userpresenceold_realm_id_timestamp_25f410da_idx;
            ALTER INDEX zerver_userpresence_user_profile_id_b67b4092 RENAME TO zerver_userpresenceold_user_profile_id_b67b4092;

            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresence_status_check TO zerver_userpresenceold_status_check;
            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresence_client_id_ed703e94_fk_zerver_client_id TO zerver_userpresenceold_client_id_ed703e94_fk_zerver_client_id;
            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresence_realm_id_5c4ef5a9_fk_zerver_realm_id TO zerver_userpresenceold_realm_id_5c4ef5a9_fk_zerver_realm_id;
            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresence_user_profile_id_b67b4092_fk_zerver_us TO zerver_userpresenceold_user_profile_id_b67b4092_fk_zerver_us;

            ALTER TABLE zerver_userpresence RENAME TO zerver_userpresenceold;
        """,
            reverse_sql="""
            ALTER TABLE zerver_userpresenceold RENAME TO zerver_userpresence;

            ALTER INDEX zerver_userpresenceold_pkey RENAME TO zerver_userpresence_pkey;
            ALTER INDEX zerver_userpresenceold_user_profile_id_client_id_5fdcf4f4_uniq RENAME TO zerver_userpresence_user_profile_id_client_id_5fdcf4f4_uniq;
            ALTER INDEX zerver_userpresenceold_client_id_ed703e94 RENAME TO zerver_userpresence_client_id_ed703e94;
            ALTER INDEX zerver_userpresenceold_realm_id_5c4ef5a9 RENAME TO zerver_userpresence_realm_id_5c4ef5a9;
            ALTER INDEX zerver_userpresenceold_realm_id_timestamp_25f410da_idx RENAME TO zerver_userpresence_realm_id_timestamp_25f410da_idx;
            ALTER INDEX zerver_userpresenceold_user_profile_id_b67b4092 RENAME TO zerver_userpresence_user_profile_id_b67b4092;

            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresenceold_status_check TO zerver_userpresence_status_check;
            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresenceold_client_id_ed703e94_fk_zerver_client_id TO zerver_userpresence_client_id_ed703e94_fk_zerver_client_id;
            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresenceold_realm_id_5c4ef5a9_fk_zerver_realm_id TO zerver_userpresence_realm_id_5c4ef5a9_fk_zerver_realm_id;
            ALTER TABLE zerver_userpresence RENAME CONSTRAINT zerver_userpresenceold_user_profile_id_b67b4092_fk_zerver_us TO zerver_userpresence_user_profile_id_b67b4092_fk_zerver_us;
        """,
            state_operations=[
                migrations.RenameModel(
                    old_name="UserPresence",
                    new_name="UserPresenceOld",
                )
            ],
        ),
        migrations.CreateModel(
            name="UserPresence",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "last_connected_time",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "last_active_time",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.Realm"
                    ),
                ),
                (
                    "user_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                "index_together": {("realm", "last_active_time"), ("realm", "last_connected_time")},
            },
        ),
    ]
