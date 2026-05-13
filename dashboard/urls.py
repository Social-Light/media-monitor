from django.urls import path

from dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("",                          views.overview,       name="overview"),
    path("seeds/",                    views.seed_list,      name="seed-list"),
    path("seeds/create/",             views.seed_create,    name="seed-create"),
    path("seeds/<int:pk>/edit/",      views.seed_edit,      name="seed-edit"),
    path("seeds/<int:pk>/delete/",    views.seed_delete,    name="seed-delete"),
    path("articles/",                 views.article_list,   name="article-list"),
    path("articles/<int:pk>/",        views.article_detail, name="article-detail"),
]
