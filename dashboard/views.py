from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from discovery.models import SeedSource
from fetcher.models import ParsedArticle

from dashboard.forms import SeedSourceForm


def overview(request):
    from core.models import URLStatusChoices
    from discovery.models import DiscoveredURL

    status_counts = (
        DiscoveredURL.objects
        .values("status")
        .annotate(count=Count("id"))
    )
    url_by_status = {row["status"]: row["count"] for row in status_counts}

    context = {
        "total_seeds":     SeedSource.objects.count(),
        "active_seeds":    SeedSource.objects.filter(is_active=True).count(),
        "total_articles":  ParsedArticle.objects.count(),
        "unique_articles": ParsedArticle.objects.filter(is_duplicate=False).count(),
        "url_by_status":   url_by_status,
        "recent_articles": (
            ParsedArticle.objects
            .filter(is_duplicate=False)
            .select_related("fetched_page__discovered_url")
            .order_by("-created_at")[:10]
        ),
    }
    return render(request, "dashboard/overview.html", context)


def seed_list(request):
    seeds = SeedSource.objects.annotate(
        article_count=Count(
            "discovered_urls__fetched_page__parsed_article",
            distinct=True,
        )
    ).order_by("name")
    return render(request, "dashboard/seeds/list.html", {"seeds": seeds})


def seed_create(request):
    if request.method == "POST":
        form = SeedSourceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("dashboard:seed-list")
    else:
        form = SeedSourceForm()
    return render(request, "dashboard/seeds/form.html", {"form": form, "action": "Create"})


def seed_edit(request, pk):
    seed = get_object_or_404(SeedSource, pk=pk)
    if request.method == "POST":
        form = SeedSourceForm(request.POST, instance=seed)
        if form.is_valid():
            form.save()
            return redirect("dashboard:seed-list")
    else:
        form = SeedSourceForm(instance=seed)
    return render(
        request,
        "dashboard/seeds/form.html",
        {"form": form, "action": "Edit", "seed": seed},
    )


def seed_delete(request, pk):
    seed = get_object_or_404(SeedSource, pk=pk)
    if request.method == "POST":
        seed.delete()
        return redirect("dashboard:seed-list")
    return render(request, "dashboard/seeds/confirm_delete.html", {"seed": seed})


def article_list(request):
    qs = ParsedArticle.objects.select_related("fetched_page__discovered_url")

    q             = request.GET.get("q", "").strip()
    source_domain = request.GET.get("source_domain", "").strip()
    language      = request.GET.get("language", "").strip()
    show_dups     = request.GET.get("show_duplicates", "") == "1"

    if not show_dups:
        qs = qs.filter(is_duplicate=False)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(summary__icontains=q))
    if source_domain:
        qs = qs.filter(source_domain=source_domain)
    if language:
        qs = qs.filter(language=language)

    qs = qs.order_by("-published_at", "-created_at")[:100]

    domains = (
        ParsedArticle.objects
        .filter(is_duplicate=False)
        .exclude(source_domain="")
        .values_list("source_domain", flat=True)
        .distinct()
        .order_by("source_domain")
    )
    languages = (
        ParsedArticle.objects
        .filter(is_duplicate=False)
        .exclude(language="")
        .values_list("language", flat=True)
        .distinct()
        .order_by("language")
    )

    context = {
        "articles":      qs,
        "domains":       domains,
        "languages":     languages,
        "q":             q,
        "source_domain": source_domain,
        "language":      language,
        "show_dups":     show_dups,
    }

    if request.META.get("HTTP_HX_REQUEST"):
        return render(request, "dashboard/articles/_table.html", context)
    return render(request, "dashboard/articles/list.html", context)


def article_detail(request, pk):
    article = get_object_or_404(
        ParsedArticle.objects.select_related("fetched_page__discovered_url"),
        pk=pk,
    )
    return render(request, "dashboard/articles/detail.html", {"article": article})
