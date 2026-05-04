from django.contrib import admin
from django.utils.html import format_html

from .models import FetchedPage, ParsedArticle


@admin.register(FetchedPage)
class FetchedPageAdmin(admin.ModelAdmin):
    list_display   = ["short_url", "status_code", "content_type", "fetch_duration_ms", "was_successful", "fetched_at"]
    list_filter    = ["status_code"]
    search_fields  = ["discovered_url__url"]
    readonly_fields = ["fetched_at", "created_at", "updated_at"]
    list_select_related = ["discovered_url"]
    ordering       = ["-fetched_at"]

    fieldsets = (
        ("Source", {
            "fields": ("discovered_url",),
        }),
        ("Response", {
            "fields": ("status_code", "content_type", "encoding", "fetch_duration_ms"),
        }),
        ("Content", {
            "fields": ("raw_html",),
            "classes": ("collapse",),
            "description": "Raw HTML — collapsed by default due to size.",
        }),
        ("Timestamps", {
            "fields": ("fetched_at", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="URL")
    def short_url(self, obj):
        u = obj.discovered_url.url
        display = u if len(u) <= 80 else u[:77] + "..."
        return format_html('<a href="{}" target="_blank">{}</a>', u, display)

    @admin.display(description="OK?", boolean=True)
    def was_successful(self, obj):
        return obj.was_successful


@admin.register(ParsedArticle)
class ParsedArticleAdmin(admin.ModelAdmin):
    list_display   = ["title", "source_domain", "author", "language", "word_count", "published_at", "created_at"]
    list_filter    = ["source_domain", "language"]
    search_fields  = ["title", "body_text", "author", "source_domain"]
    readonly_fields = ["created_at", "updated_at", "word_count_display"]
    ordering       = ["-published_at"]

    fieldsets = (
        ("Article", {
            "fields": ("fetched_page", "title", "author", "published_at", "source_domain"),
        }),
        ("Content", {
            "fields": ("body_text", "summary"),
        }),
        ("Classification", {
            "fields": ("language", "tags", "signals"),
        }),
        ("Stats", {
            "fields": ("word_count_display", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Word count")
    def word_count_display(self, obj):
        return obj.word_count

    @admin.display(description="Word count")
    def word_count(self, obj):
        return obj.word_count