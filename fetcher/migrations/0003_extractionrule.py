from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discovery', '0001_initial'),
        ('fetcher', '0002_parsedarticle_dedup_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExtractionRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('domain', models.CharField(db_index=True, help_text="Exact hostname to match, e.g. 'www.miningweekly.com'.", max_length=255)),
                ('title_selector', models.CharField(blank=True, help_text='CSS selector for the article title element.', max_length=500)),
                ('body_selector', models.CharField(blank=True, help_text='CSS selector for the article body element.', max_length=500)),
                ('author_selector', models.CharField(blank=True, help_text='CSS selector for the author element.', max_length=500)),
                ('date_selector', models.CharField(blank=True, help_text='CSS selector for the publication date element.', max_length=500)),
                ('date_format', models.CharField(blank=True, help_text="strptime format string for date_selector text, e.g. '%%d %%B %%Y'. Leave blank to auto-parse ISO 8601.", max_length=100)),
                ('is_active', models.BooleanField(default=True, help_text='Inactive rules are ignored during extraction.')),
                ('seed', models.ForeignKey(help_text='Seed source that owns this rule.', on_delete=django.db.models.deletion.CASCADE, related_name='rules', to='discovery.seedsource')),
            ],
            options={
                'verbose_name': 'Extraction Rule',
                'verbose_name_plural': 'Extraction Rules',
                'ordering': ['domain'],
            },
        ),
    ]
