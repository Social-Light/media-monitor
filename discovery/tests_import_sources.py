"""
Tests for the `import_sources` management command.
Run with: python manage.py test discovery.tests_import_sources

Covers both supported CSV shapes:
  - the documented schema: name,url,source_type,organisation,keywords,crawl_interval
  - the media-source export schema: name,type,domain,... (online-only filter)
"""
import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from discovery.management.commands.import_sources import (
    extract_domain,
    split_keywords,
)
from discovery.models import SeedSource, SourceType
from matching.models import Organisation, OrganisationKeyword, OrganisationSource

SPEC_HEADER   = "name,url,source_type,organisation,keywords,crawl_interval"
EXPORT_HEADER = "name,type,domain,reach,country"


class ImportSourcesTestCase(TestCase):
    """Base class with a helper to run the command against an inline CSV."""

    def setUp(self):
        self._tmp_files = []

    def tearDown(self):
        for path in self._tmp_files:
            try:
                os.remove(path)
            except OSError:
                pass

    def _csv(self, *lines, header=SPEC_HEADER):
        """Write a CSV (header + rows) to a temp file and return its path."""
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            fh.write(header + "\n")
            for line in lines:
                fh.write(line + "\n")
        self._tmp_files.append(path)
        return path

    def _run(self, path, **opts):
        out = StringIO()
        call_command("import_sources", path, stdout=out, **opts)
        return out.getvalue()


# ── Pure helpers ────────────────────────────────────────────────────────────────

class HelperTests(TestCase):
    def test_extract_domain_with_scheme(self):
        self.assertEqual(extract_domain("https://www.example.co.za/news/"), "www.example.co.za")

    def test_extract_domain_without_scheme(self):
        self.assertEqual(extract_domain("www.example.com/news"), "www.example.com")

    def test_extract_domain_lowercases(self):
        self.assertEqual(extract_domain("https://Example.COM"), "example.com")

    def test_split_keywords_strips_and_dedupes(self):
        self.assertEqual(split_keywords(" gold , Mining, gold "), ["gold", "Mining"])

    def test_split_keywords_empty(self):
        self.assertEqual(split_keywords(""), [])


# ── Documented schema ───────────────────────────────────────────────────────────

class BasicImportTests(ImportSourcesTestCase):

    def test_creates_org_seed_keywords_and_source(self):
        # keywords field is itself comma-separated, so the whole field is quoted.
        path = self._csv(
            '"Mining Weekly RSS","https://miningweekly.com/feed","rss","Debswana","gold, mining","30"'
        )
        self._run(path)

        org = Organisation.objects.get(name="Debswana")
        self.assertTrue(org.slug)  # auto-slugified

        seed = SeedSource.objects.get(url="https://miningweekly.com/feed")
        self.assertEqual(seed.name, "Mining Weekly RSS")
        self.assertEqual(seed.source_type, SourceType.RSS)
        self.assertEqual(seed.crawl_interval, 30)
        self.assertEqual(seed.keyword_filter, "gold, mining")

        self.assertEqual(set(org.keywords.values_list("keyword", flat=True)), {"gold", "mining"})
        self.assertEqual(list(org.sources.values_list("domain", flat=True)), ["miningweekly.com"])

    def test_keyword_filter_drives_model_property(self):
        path = self._csv('"S","https://a.com/feed","rss","OrgA","alpha, beta, gamma",60')
        self._run(path)
        seed = SeedSource.objects.get(url="https://a.com/feed")
        self.assertEqual(seed.keywords, ["alpha", "beta", "gamma"])  # model property

    def test_summary_counts_in_output(self):
        path = self._csv('"S","https://a.com/feed","rss","OrgA","k",60')
        out = self._run(path)
        self.assertIn("Organisations created: 1", out)
        self.assertIn("Seed sources created : 1", out)


class DefaultsAndValidationTests(ImportSourcesTestCase):

    def test_crawl_interval_defaults_to_60_when_blank(self):
        self._run(self._csv('"S","https://a.com/feed","rss","OrgA","k",'))
        self.assertEqual(SeedSource.objects.get(url="https://a.com/feed").crawl_interval, 60)

    def test_invalid_crawl_interval_falls_back_to_60(self):
        out = self._run(self._csv('"S","https://a.com/feed","rss","OrgA","k","abc"'))
        self.assertEqual(SeedSource.objects.get(url="https://a.com/feed").crawl_interval, 60)
        self.assertIn("invalid crawl_interval", out)

    def test_source_type_defaults_to_seed_url_when_blank(self):
        self._run(self._csv('"S","https://a.com/feed","","OrgA","k",60'))
        self.assertEqual(
            SeedSource.objects.get(url="https://a.com/feed").source_type, SourceType.SEED_URL
        )

    def test_unknown_source_type_warns_and_defaults(self):
        out = self._run(self._csv('"S","https://a.com/feed","podcast","OrgA","k",60'))
        seed = SeedSource.objects.get(url="https://a.com/feed")
        self.assertEqual(seed.source_type, SourceType.SEED_URL)
        self.assertIn("unknown source_type", out)

    def test_organisation_falls_back_to_name_when_blank(self):
        self._run(self._csv('"Acme Corp","https://a.com/feed","rss","","k",60'))
        self.assertTrue(Organisation.objects.filter(name="Acme Corp").exists())

    def test_row_missing_url_is_skipped(self):
        out = self._run(self._csv('"S","","rss","OrgA","k",60'))
        self.assertFalse(SeedSource.objects.exists())
        self.assertIn("Skipped (invalid)    : 1", out)

    def test_missing_url_and_domain_columns_raises(self):
        path = self._csv('"S","OrgA"', header="name,organisation")
        with self.assertRaises(CommandError):
            self._run(path)

    def test_missing_name_column_raises(self):
        path = self._csv('"https://a.com"', header="url")
        with self.assertRaises(CommandError):
            self._run(path)

    def test_missing_file_raises(self):
        with self.assertRaises(CommandError):
            self._run("/no/such/file.csv")


# ── Media-source export schema ───────────────────────────────────────────────────

class ExportSchemaTests(ImportSourcesTestCase):

    def test_online_row_imported_with_domain_as_url(self):
        path = self._csv(
            '"MyBroadband","online","https://mybroadband.co.za/news/","10000000","South Africa"',
            header=EXPORT_HEADER,
        )
        self._run(path)

        seed = SeedSource.objects.get(url="https://mybroadband.co.za/news/")
        self.assertEqual(seed.name, "MyBroadband")
        # No source_type column → default seed_url (landing pages → link extraction)
        self.assertEqual(seed.source_type, SourceType.SEED_URL)

        # url maps to domain → OrganisationSource carries the bare host
        org = Organisation.objects.get(name="MyBroadband")
        self.assertEqual(list(org.sources.values_list("domain", flat=True)), ["mybroadband.co.za"])

    def test_social_rows_are_skipped(self):
        path = self._csv(
            '"MyBroadband","online","https://mybroadband.co.za/news/","1","ZA"',
            '"MMG Social","social","https://www.facebook.com/mmgsocial","1","AU"',
            header=EXPORT_HEADER,
        )
        out = self._run(path)
        self.assertEqual(SeedSource.objects.count(), 1)
        self.assertFalse(SeedSource.objects.filter(url__icontains="facebook").exists())
        self.assertIn("Skipped (not online) : 1", out)

    def test_export_row_creates_org_from_name(self):
        path = self._csv(
            '"Choppies Enterprises ","online","https://choppiesgroup.com/investor.php","1","BW"',
            header=EXPORT_HEADER,
        )
        self._run(path)
        # name is stripped of trailing whitespace
        self.assertTrue(Organisation.objects.filter(name="Choppies Enterprises").exists())


# ── Idempotency ──────────────────────────────────────────────────────────────────

class IdempotencyTests(ImportSourcesTestCase):

    def test_running_twice_does_not_duplicate(self):
        path = self._csv('"S","https://a.com/feed","rss","OrgA","alpha, beta",60')
        self._run(path)
        out2 = self._run(path)

        self.assertEqual(SeedSource.objects.count(), 1)
        self.assertEqual(Organisation.objects.count(), 1)
        self.assertEqual(OrganisationKeyword.objects.count(), 2)
        self.assertEqual(OrganisationSource.objects.count(), 1)
        # Nothing changed on the second pass → no creates, no updates.
        self.assertIn("Seed sources created : 0", out2)
        self.assertIn("Seed sources updated : 0", out2)

    def test_second_run_updates_changed_keyword_filter(self):
        self._run(self._csv('"S","https://a.com/feed","rss","OrgA","alpha",60'))
        out2 = self._run(self._csv('"S","https://a.com/feed","rss","OrgA","alpha, beta",60'))

        seed = SeedSource.objects.get(url="https://a.com/feed")
        self.assertEqual(seed.keyword_filter, "alpha, beta")
        self.assertIn("Seed sources updated : 1", out2)
        # New keyword added to the org; old one kept.
        self.assertEqual(
            set(Organisation.objects.get(name="OrgA").keywords.values_list("keyword", flat=True)),
            {"alpha", "beta"},
        )

    def test_shared_organisation_across_rows(self):
        path = self._csv(
            '"Feed1","https://a.com/feed","rss","SharedOrg","alpha",60',
            '"Feed2","https://b.com/feed","rss","SharedOrg","beta",60',
        )
        self._run(path)
        self.assertEqual(Organisation.objects.filter(name="SharedOrg").count(), 1)
        self.assertEqual(SeedSource.objects.count(), 2)
        org = Organisation.objects.get(name="SharedOrg")
        self.assertEqual(set(org.keywords.values_list("keyword", flat=True)), {"alpha", "beta"})
        self.assertEqual(set(org.sources.values_list("domain", flat=True)), {"a.com", "b.com"})


# ── Dry run ──────────────────────────────────────────────────────────────────────

class DryRunTests(ImportSourcesTestCase):

    def test_dry_run_writes_nothing_but_reports_real_counts(self):
        path = self._csv('"S","https://a.com/feed","rss","OrgA","alpha, beta",60')
        out = self._run(path, dry_run=True)
        self.assertEqual(SeedSource.objects.count(), 0)
        self.assertEqual(Organisation.objects.count(), 0)
        self.assertEqual(OrganisationKeyword.objects.count(), 0)
        self.assertIn("DRY RUN", out)
        # Rollback-based dry-run still reports what *would* be created.
        self.assertIn("Organisations created: 1", out)
        self.assertIn("Seed sources created : 1", out)

    def test_dry_run_does_not_clear(self):
        SeedSource.objects.create(name="Keep", url="https://keep.com/feed", source_type=SourceType.RSS)
        path = self._csv('"S","https://a.com/feed","rss","OrgA","k",60')
        out = self._run(path, dry_run=True, clear_existing=True)
        self.assertTrue(SeedSource.objects.filter(url="https://keep.com/feed").exists())
        self.assertIn("Would clear", out)


# ── Clear existing ───────────────────────────────────────────────────────────────

class ClearExistingTests(ImportSourcesTestCase):

    def test_clear_existing_removes_then_imports(self):
        old_org = Organisation.objects.create(name="OldOrg")
        OrganisationKeyword.objects.create(organisation=old_org, keyword="stale")
        SeedSource.objects.create(name="Old", url="https://old.com/feed", source_type=SourceType.RSS)

        path = self._csv('"New","https://new.com/feed","rss","NewOrg","fresh",60')
        out = self._run(path, clear_existing=True)

        self.assertFalse(SeedSource.objects.filter(url="https://old.com/feed").exists())
        self.assertFalse(Organisation.objects.filter(name="OldOrg").exists())
        self.assertFalse(OrganisationKeyword.objects.filter(keyword="stale").exists())  # cascaded
        self.assertTrue(SeedSource.objects.filter(url="https://new.com/feed").exists())
        self.assertTrue(Organisation.objects.filter(name="NewOrg").exists())
        self.assertIn("Cleared", out)
