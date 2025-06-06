# Generated by Django 3.2.25 on 2025-03-22 11:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('back', '0036_auto_20250302_1053'),
    ]

    operations = [
        migrations.AlterField(
            model_name='domain',
            name='auto_renew_enabled',
            field=models.BooleanField(default=True, help_text='Domain will be automatically renewed 3 months before the expiration date, if you have enough funds. Account balance will be automatically deducted.Please make sure you also enabled automatic domains renewal in your profile settings.', verbose_name='Automatically renew'),
        ),
    ]
