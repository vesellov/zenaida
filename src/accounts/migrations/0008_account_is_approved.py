# Generated by Django 3.2.25 on 2024-11-09 14:55

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_alter_notification_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='is_approved',
            field=models.BooleanField(default=True, help_text='indicate if the user was approved by the site administrator', verbose_name='approved'),
        ),
    ]
