from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from discovery.models import DiscoveredURL, SeedSource
from fetcher.models import ExtractionRule, ParsedArticle
from fetcher.search import search_articles

from api.serializers import (
    DiscoveredURLSerializer,
    ExtractionRuleSerializer,
    ParsedArticleSerializer,
    SeedSourceSerializer,
)


class SeedSourceViewSet(viewsets.ModelViewSet):
    queryset = SeedSource.objects.all()
    serializer_class = SeedSourceSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1", "yes"))
        return qs


class DiscoveredURLViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = DiscoveredURL.objects.select_related("seed")
    serializer_class = DiscoveredURLSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        seed_id = self.request.query_params.get("seed")
        status = self.request.query_params.get("status")
        if seed_id:
            qs = qs.filter(seed_id=seed_id)
        if status:
            qs = qs.filter(status=status)
        return qs


class ParsedArticleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ParsedArticle.objects.select_related(
        "fetched_page__discovered_url"
    )
    serializer_class = ParsedArticleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        source_domain = self.request.query_params.get("source_domain")
        language = self.request.query_params.get("language")
        is_dup = self.request.query_params.get("is_duplicate")
        if source_domain:
            qs = qs.filter(source_domain=source_domain)
        if language:
            qs = qs.filter(language=language)
        if is_dup is not None:
            qs = qs.filter(is_duplicate=is_dup.lower() in ("true", "1", "yes"))
        return qs

    @action(detail=False, methods=["get"])
    def search(self, request):
        q = request.query_params.get("q", "")
        source_domain = request.query_params.get("source_domain")
        language = request.query_params.get("language")
        sentiment = request.query_params.get("sentiment")
        try:
            from_ = int(request.query_params.get("from_", 0))
            size = min(int(request.query_params.get("size", 10)), 100)
        except ValueError:
            from_, size = 0, 10

        hits = search_articles(
            q,
            source_domain=source_domain,
            language=language,
            sentiment=sentiment,
            from_=from_,
            size=size,
        )
        results = [
            {"id": h["_id"], "score": h["_score"], **h["_source"]}
            for h in hits
        ]
        return Response({"count": len(results), "results": results})


class ExtractionRuleViewSet(viewsets.ModelViewSet):
    queryset = ExtractionRule.objects.select_related("seed")
    serializer_class = ExtractionRuleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        seed_id = self.request.query_params.get("seed")
        domain = self.request.query_params.get("domain")
        is_active = self.request.query_params.get("is_active")
        if seed_id:
            qs = qs.filter(seed_id=seed_id)
        if domain:
            qs = qs.filter(domain=domain)
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1", "yes"))
        return qs
