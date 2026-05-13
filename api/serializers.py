from rest_framework import serializers

from discovery.models import DiscoveredURL, SeedSource
from fetcher.models import ExtractionRule, ParsedArticle


class SeedSourceSerializer(serializers.ModelSerializer):
    source_type_display = serializers.CharField(
        source="get_source_type_display", read_only=True
    )

    class Meta:
        model = SeedSource
        fields = [
            "id", "name", "url", "source_type", "source_type_display",
            "is_active", "crawl_interval", "last_crawled_at",
            "keyword_filter", "use_playwright", "meta",
            "created_at", "updated_at",
        ]
        read_only_fields = ["last_crawled_at", "created_at", "updated_at"]


class DiscoveredURLSerializer(serializers.ModelSerializer):
    seed_name = serializers.CharField(source="seed.name", read_only=True)

    class Meta:
        model = DiscoveredURL
        fields = [
            "id", "seed", "seed_name", "url", "title", "snippet",
            "published_at", "status", "error_message",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "seed", "seed_name", "url", "title", "snippet",
            "published_at", "status", "error_message",
            "created_at", "updated_at",
        ]


class ParsedArticleSerializer(serializers.ModelSerializer):
    url = serializers.CharField(read_only=True)
    word_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ParsedArticle
        fields = [
            "id", "url", "title", "body_text", "summary", "author",
            "published_at", "source_domain", "language", "tags", "signals",
            "is_duplicate", "duplicate_of", "word_count",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "url", "title", "body_text", "summary", "author",
            "published_at", "source_domain", "language", "tags", "signals",
            "is_duplicate", "duplicate_of", "word_count",
            "created_at", "updated_at",
        ]


class ExtractionRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionRule
        fields = [
            "id", "seed", "domain",
            "title_selector", "body_selector", "author_selector",
            "date_selector", "date_format", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
