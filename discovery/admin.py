from django.contrib import admin
from django.utils.html import format_html

from .models import SeedSource, DiscoveredURL, CrawlJob


# ─────────────────────────────────────────────────────────────────────────────
# Inline for CrawlJob (shown inside DiscoveredURL detail view)
# ─────────────────────────────────────────────────────────────────────────────

class CrawlJobInline(admin.TabularInline):
    model       = CrawlJob
    extra       = 0
    max_num     = 10
    can_delete  = False
    ordering    = ["-created_at"]
    fields      = ["trigger", "status", "http_status", "started_at", "finished_at", "error_message"]
    readonly_fields = ["trigger", "status", "http_status", "started_at", "finished_at", "error_message"]


# ─────────────────────────────────────────────────────────────────────────────
# SeedSource Admin
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(SeedSource)
class SeedSourceAdmin(admin.ModelAdmin):
    list_display  = ["name", "source_type", "is_active", "crawl_interval",
                     "last_crawled_at", "url_link", "discovered_count"]
    list_filter   = ["source_type", "is_active"]
    search_fields = ["name", "url"]
    readonly_fields = ["last_crawled_at", "created_at", "updated_at"]
    ordering      = ["name"]

    fieldsets = (
        ("Identity", {
            "fields": ("name", "url", "source_type"),
        }),
        ("Schedule", {
            "fields": ("is_active", "crawl_interval", "last_crawled_at"),
        }),
        ("Filtering & Config", {
            "fields": ("keyword_filter", "meta"),
            "description": (
                "keyword_filter: comma-separated words — only matching URLs are stored.<br>"
                "meta: JSON config specific to the source type."
            ),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    # ── Custom columns ────────────────────────────────────────────────────────

    @admin.display(description="URL")
    def url_link(self, obj):
        return format_html('<a href="{}" target="_blank">🔗 Open</a>', obj.url)

    @admin.display(description="Discovered URLs")
    def discovered_count(self, obj):
        return obj.discovered_urls.count()


# ─────────────────────────────────────────────────────────────────────────────
# DiscoveredURL Admin
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(DiscoveredURL)
class DiscoveredURLAdmin(admin.ModelAdmin):
    list_display  = ["short_url", "seed", "status", "published_at", "created_at"]
    list_filter   = ["status", "seed"]
    search_fields = ["url", "title", "snippet"]
    readonly_fields = ["url_hash", "created_at", "updated_at"]
    list_select_related = ["seed"]
    ordering      = ["-created_at"]
    inlines       = [CrawlJobInline]

    fieldsets = (
        ("URL", {
            "fields": ("seed", "url", "url_hash", "status", "error_message"),
        }),
        ("Metadata", {
            "fields": ("title", "snippet", "published_at"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="URL")
    def short_url(self, obj):
        display = obj.url if len(obj.url) <= 80 else obj.url[:77] + "..."
        return format_html('<a href="{}" target="_blank">{}</a>', obj.url, display)


# ─────────────────────────────────────────────────────────────────────────────
# CrawlJob Admin
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(CrawlJob)
class CrawlJobAdmin(admin.ModelAdmin):
    list_display  = ["id", "short_url", "trigger", "status",
                     "http_status", "duration", "started_at"]
    list_filter   = ["status", "trigger"]
    search_fields = ["discovered_url__url", "celery_task_id"]
    readonly_fields = ["celery_task_id", "created_at", "updated_at"]
    list_select_related = ["discovered_url"]
    ordering      = ["-created_at"]

    @admin.display(description="URL")
    def short_url(self, obj):
        u = obj.discovered_url.url
        return u if len(u) <= 70 else u[:67] + "..."

    @admin.display(description="Duration")
    def duration(self, obj):
        secs = obj.duration_seconds
        if secs is None:
            return "—"
        return f"{secs:.1f}s"