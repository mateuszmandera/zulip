# Generated by Django 3.2.7 on 2021-10-03 18:04

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("zerver", "0358_split_create_stream_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="SCIMClient",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=32)),
                (
                    "realm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="zerver.realm"
                    ),
                ),
            ],
            options={
                "unique_together": {("realm", "name")},
            },
        ),
    ]
