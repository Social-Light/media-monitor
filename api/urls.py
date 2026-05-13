from rest_framework.routers import DefaultRouter

from api.views import (
    DiscoveredURLViewSet,
    ExtractionRuleViewSet,
    ParsedArticleViewSet,
    SeedSourceViewSet,
)

router = DefaultRouter()
router.register("seeds", SeedSourceViewSet, basename="seed")
router.register("discovered-urls", DiscoveredURLViewSet, basename="discovered-url")
router.register("articles", ParsedArticleViewSet, basename="article")
router.register("extraction-rules", ExtractionRuleViewSet, basename="extraction-rule")

urlpatterns = router.urls
