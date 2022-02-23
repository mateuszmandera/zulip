# Generated by Django 3.2.9 on 2021-12-27 21:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("zilencer", "0023_remotezulipserver_deactivated"),
    ]

    operations = [
        migrations.AlterField(
            model_name="remotepushdevicetoken",
            name="user_id",
            field=models.BigIntegerField(db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="remotepushdevicetoken",
            name="user_uuid",
            field=models.UUIDField(null=True),
        ),
        migrations.AlterUniqueTogether(
            name="remotepushdevicetoken",
            unique_together={
                ("server", "user_uuid", "kind", "token"),
                ("server", "user_id", "kind", "token"),
            },
        ),
    ]
