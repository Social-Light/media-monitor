"""
platform_sync/routers.py
──────────────────────────
Routes the mirrored platform models to the right database.

  PLATFORM_INTEGRATED = False (dev/test)
      Everything lives in the 'default' database. platform_sync models are
      `managed = True`, so their migrations build local `monitor_*` tables.

  PLATFORM_INTEGRATED = True (production)
      platform_sync models read/write the separate 'platform' database (the
      shared platform PostgreSQL). They are `managed = False`, and this router
      refuses to migrate them anywhere — the platform owns those tables. Crawler
      apps stay on 'default' and never touch the 'platform' DB.
"""
from django.conf import settings

PLATFORM_APP = "platform_sync"


class PlatformRouter:
    @staticmethod
    def _integrated() -> bool:
        return getattr(settings, "PLATFORM_INTEGRATED", False)

    def _target_db(self) -> str:
        return "platform" if self._integrated() else "default"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == PLATFORM_APP:
            return self._target_db()
        return None  # fall through → 'default'

    def db_for_write(self, model, **hints):
        if model._meta.app_label == PLATFORM_APP:
            return self._target_db()
        return None

    def allow_relation(self, obj1, obj2, **hints):
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        # Both platform models, or both crawler models → same DB, allow.
        if labels == {PLATFORM_APP} or PLATFORM_APP not in labels:
            return True
        return None  # mixed: let Django's default cross-db checks apply

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == PLATFORM_APP:
            # Production: platform owns its schema — never migrate it.
            if self._integrated():
                return False
            # Dev/test: build the local mirror in the default DB only.
            return db == "default"
        # All crawler / built-in apps live in the default DB only, never in
        # the shared platform DB.
        return db == "default"
