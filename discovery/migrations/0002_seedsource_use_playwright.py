from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='seedsource',
            name='use_playwright',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Use headless Chromium (Playwright) to render articles from this '
                    'seed. Enable for JS-heavy sites where plain HTTP returns incomplete HTML.'
                ),
            ),
        ),
    ]
