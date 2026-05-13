"""
fetcher/management/commands/index_articles.py

Bulk-index all non-duplicate ParsedArticles into Elasticsearch.

Usage:
    python manage.py index_articles
    python manage.py index_articles --reset          # drop + recreate index first
    python manage.py index_articles --batch-size 200
"""
from django.core.management.base import BaseCommand

from fetcher.models import ParsedArticle
from fetcher.search import bulk_index_articles, drop_index, ensure_index


class Command(BaseCommand):
    help = "Bulk-index all non-duplicate ParsedArticles into Elasticsearch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete and recreate the index before indexing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Articles per bulk request (default: 100).",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            drop_index()
            self.stdout.write("Index dropped.")

        ensure_index()

        articles = ParsedArticle.objects.filter(is_duplicate=False).select_related(
            "fetched_page__discovered_url"
        )
        total = articles.count()

        if not total:
            self.stdout.write("No articles to index.")
            return

        self.stdout.write(f"Indexing {total} articles...")
        successes, errors = bulk_index_articles(articles, chunk_size=options["batch_size"])
        self.stdout.write(
            f"Done. indexed={successes}  errors={errors}  total={total}"
        )
