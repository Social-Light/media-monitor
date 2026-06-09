from django.contrib import admin

from .models import (
    ArticleMatch,
    Organisation,
    OrganisationKeyword,
    OrganisationSource,
)


class OrganisationKeywordInline(admin.TabularInline):
    model = OrganisationKeyword
    extra = 1


class OrganisationSourceInline(admin.TabularInline):
    model = OrganisationSource
    extra = 1


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display    = ["name", "slug", "is_active", "keyword_count", "created_at"]
    list_filter     = ["is_active"]
    search_fields   = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at", "updated_at"]
    ordering        = ["name"]
    inlines         = [OrganisationKeywordInline, OrganisationSourceInline]

    @admin.display(description="Keywords")
    def keyword_count(self, obj):
        return obj.keywords.count()


@admin.register(OrganisationKeyword)
class OrganisationKeywordAdmin(admin.ModelAdmin):
    list_display        = ["keyword", "organisation", "is_active", "created_at"]
    list_filter         = ["is_active", "organisation"]
    search_fields       = ["keyword", "organisation__name"]
    list_select_related = ["organisation"]
    readonly_fields     = ["created_at", "updated_at"]
    ordering            = ["keyword"]


@admin.register(OrganisationSource)
class OrganisationSourceAdmin(admin.ModelAdmin):
    list_display        = ["domain", "organisation", "created_at"]
    list_filter         = ["organisation"]
    search_fields       = ["domain", "organisation__name"]
    list_select_related = ["organisation"]
    readonly_fields     = ["created_at", "updated_at"]
    ordering            = ["domain"]


@admin.register(ArticleMatch)
class ArticleMatchAdmin(admin.ModelAdmin):
    list_display        = ["organisation", "matched_keyword", "matched_in", "confidence", "article_title", "created_at"]
    list_filter         = ["matched_in", "organisation"]
    search_fields       = ["matched_keyword", "organisation__name", "parsed_article__title"]
    list_select_related = ["organisation", "parsed_article"]
    readonly_fields     = ["created_at", "updated_at"]
    ordering            = ["-created_at"]

    @admin.display(description="Article")
    def article_title(self, obj):
        return obj.parsed_article.title[:60]
