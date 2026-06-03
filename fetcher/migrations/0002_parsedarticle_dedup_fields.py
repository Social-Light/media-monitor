from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('fetcher', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='parsedarticle',
            name='content_hash',
            field=models.CharField(blank=True, db_index=True, help_text='SHA-256 of normalised body text.', max_length=64),
        ),
        migrations.AddField(
            model_name='parsedarticle',
            name='canonical_url',
            field=models.URLField(blank=True, help_text='<link rel="canonical"> href extracted from the page.'),
        ),
        migrations.AddField(
            model_name='parsedarticle',
            name='duplicate_of',
            field=models.ForeignKey(blank=True, help_text='Original article this is a duplicate of.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='duplicates', to='fetcher.parsedarticle'),
        ),
        migrations.AddField(
            model_name='parsedarticle',
            name='is_duplicate',
            field=models.BooleanField(db_index=True, default=False, help_text='True if this article is a duplicate of another.'),
        ),
    ]
