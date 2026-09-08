"""
Seed the crawler so every model in the media-monitor project is populated and
the pipeline is visible end to end:

    SeedSource → DiscoveredURL → CrawlJob → FetchedPage → ParsedArticle
                                                    ├→ ArticleMatch (crawler Organisation)
                                                    ├→ AlertMatch   (crawler Alert)
                                                    └→ platform_sync.OnlineArticle /
                                                       platform_sync.CompetitorArticle
                                                       (the push into the platform)

The crawler owns discovery/*, fetcher/*, matching/*, alerts/* and its own
`auth.User` accounts. It does NOT own the `platform_sync` models — those mirror
the platform's `monitor_*` tables. In development they are managed locally in
this SQLite database, so the seed writes them here to show the shape; in
production they are `managed = False` and routed to the shared platform
database, where the platform is the sole owner.

Organisation UUIDs match the platform's `seed_system`, so the same organisation
has the same id on both sides.

    python manage.py seed_system            # idempotent top-up
    python manage.py seed_system --reset    # wipe crawler data first
"""
import hashlib
import random
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from alerts.models import Alert as CrawlerAlert, AlertMatch, PlatformAlertNotification
from core.models import URLStatusChoices
from discovery.models import CrawlJob, DiscoveredURL, SeedSource, SourceType
from fetcher.models import ExtractionRule, FetchedPage, ParsedArticle
from matching.models import (
    ArticleMatch,
    Organisation,
    OrganisationKeyword,
    OrganisationSource,
)
from platform_sync.models import (
    Alert as PlatformAlert,
    Competitor,
    CompetitorArticle,
    Keyword,
    OnlineArticle,
    Organization,
)

ORG_NAMESPACE = uuid.UUID('6f1e6a30-2a3c-5f1e-9c4b-3f2a1d0e5b77')
SEED_PASSWORD = 'Crawler#2026'


def org_uuid(name):
    """Same derivation as the platform's seed_system — one org, one id."""
    return uuid.uuid5(ORG_NAMESPACE, f'sociallight:org:{name}')


# ─────────────────────────────────────────────────────────────────────────────
# Crawl sources — what the crawler watches
# ─────────────────────────────────────────────────────────────────────────────

SEEDS = [
    {
        'name': 'Mmegi Online RSS',
        'url': 'https://www.mmegi.bw/feed',
        'source_type': SourceType.RSS,
        'domain': 'www.mmegi.bw',
        'crawl_interval': 30,
        'keyword_filter': 'FNBB, Debswana, BTC, bank, mining, telecom',
        'use_playwright': False,
        'meta': {'max_items': 60},
    },
    {
        'name': 'Sunday Standard RSS',
        'url': 'https://www.sundaystandard.info/feed',
        'source_type': SourceType.RSS,
        'domain': 'www.sundaystandard.info',
        'crawl_interval': 60,
        'keyword_filter': 'Debswana, diamond, Jwaneng, Orapa',
        'use_playwright': False,
        'meta': {'max_items': 40},
    },
    {
        'name': 'Mining Weekly RSS',
        'url': 'https://www.miningweekly.com/page/rss',
        'source_type': SourceType.RSS,
        'domain': 'www.miningweekly.com',
        'crawl_interval': 45,
        'keyword_filter': 'Debswana, Lucara, Khoemacau, copper, diamond',
        'use_playwright': False,
        'meta': {},
    },
    {
        'name': 'Botswana Government sitemap',
        'url': 'https://www.gov.bw/sitemap.xml',
        'source_type': SourceType.SITEMAP,
        'domain': 'www.gov.bw',
        'crawl_interval': 720,
        'keyword_filter': '',
        'use_playwright': False,
        'meta': {'max_depth': 2},
    },
    {
        'name': 'Business Weekly & Review — business section',
        'url': 'https://www.businessweekly.co.bw/category/business/',
        'source_type': SourceType.SEED_URL,
        'domain': 'www.businessweekly.co.bw',
        'crawl_interval': 90,
        'keyword_filter': 'FNBB, Absa, Stanbic, BTC, Mascom',
        'use_playwright': True,
        'meta': {'max_links': 40},
    },
    {
        'name': 'The Voice — news',
        'url': 'https://www.thevoicebw.com/category/news/',
        'source_type': SourceType.SEED_URL,
        'domain': 'www.thevoicebw.com',
        'crawl_interval': 120,
        'keyword_filter': '',
        'use_playwright': False,
        'meta': {'max_links': 30},
    },
    {
        'name': 'Search API — Botswana banking',
        'url': 'https://api.search.local/botswana-banking',
        'source_type': SourceType.SEARCH_API,
        'domain': 'news.google.com',
        'crawl_interval': 180,
        'keyword_filter': 'FNB Botswana, Absa Botswana, Stanbic Botswana',
        'use_playwright': False,
        'meta': {'provider': 'bing', 'query': '"FNB Botswana" OR "Absa Botswana"', 'count': 25},
    },
    {
        'name': 'Manually added — TechCabal Botswana',
        'url': 'https://techcabal.com/tag/botswana/',
        'source_type': SourceType.MANUAL,
        'domain': 'techcabal.com',
        'crawl_interval': 0,
        'keyword_filter': 'BTC, Mascom, Orange Botswana, fibre',
        'use_playwright': False,
        'meta': {'added_by': 'ops'},
    },
]

# Crawler-side organisations. `platform` mirrors the platform tenant of the same
# name; the crawler matcher and the platform push both key off these names.
ORGS = [
    {
        'name': 'First National Bank of Botswana',
        'short': 'FNBB',
        'industry': 'Banking & Financial Services',
        'email': 'comms@fnbbotswana.co.bw',
        'website': 'https://www.fnbbotswana.co.bw',
        'keywords': ['FNBB', 'FNB Botswana', 'First National Bank Botswana', 'eWallet'],
        'domains': ['www.fnbbotswana.co.bw', 'fnbbotswana.co.bw'],
        'competitors': [
            ('Absa Bank Botswana', 'Absa, Barclays Botswana'),
            ('Stanbic Bank Botswana', 'Stanbic, Standard Bank Botswana'),
        ],
    },
    {
        'name': 'Debswana Diamond Company',
        'short': 'Debswana',
        'industry': 'Mining & Metals',
        'email': 'corporateaffairs@debswana.bw',
        'website': 'https://www.debswana.com',
        'keywords': ['Debswana', 'Jwaneng', 'Orapa', 'Cut-9'],
        'domains': ['www.debswana.com'],
        'competitors': [
            ('Lucara Diamond Corp', 'Lucara, Karowe'),
            ('Khoemacau Copper Mining', 'Khoemacau, KCM Botswana'),
        ],
    },
    {
        'name': 'Botswana Telecommunications Corporation',
        'short': 'BTC',
        'industry': 'Telecommunications',
        'email': 'media@btc.bw',
        'website': 'https://www.btc.bw',
        'keywords': ['BTC', 'Botswana Telecommunications', 'Smega', 'beMOBILE'],
        'domains': ['www.btc.bw'],
        'competitors': [
            ('Mascom Wireless', 'Mascom, MyZaka'),
            ('Orange Botswana', 'Orange BW, Orange Money'),
        ],
    },
]

HEADLINES = [
    ('FNBB', 'FNB Botswana lifts half-year profit on retail lending growth'),
    ('FNBB', 'eWallet volumes surge as FNB Botswana widens its agent network'),
    ('FNBB', 'Absa Bank Botswana takes on FNBB in the SME lending market'),
    ('FNBB', 'FNBB Foundation backs a new youth skills programme in Francistown'),
    ('Debswana', 'Debswana raises Jwaneng output as Cut-9 ramps up'),
    ('Debswana', 'De Beers and Botswana settle terms of the diamond sales agreement'),
    ('Debswana', 'Lucara reports a large stone recovery at Karowe'),
    ('Debswana', 'Khoemacau copper expansion draws new investor interest'),
    ('BTC', 'BTC extends fibre coverage to more Gaborone suburbs'),
    ('BTC', 'Smega adds merchant payments as mobile money competition sharpens'),
    ('BTC', 'Mascom and Orange Botswana respond to BOCRA quality-of-service review'),
    ('BTC', 'Undersea cable fault disrupts Botswana internet services'),
    (None, 'Bank of Botswana holds the policy rate steady'),
    (None, 'Botswana Stock Exchange closes the week marginally higher'),
    (None, 'Government tables the mid-term budget review'),
]

ANGLES = [
    'sets out the detail behind the announcement',
    'reports reaction from industry analysts',
    'examines what the move means for customers',
    'looks at the regulatory backdrop',
    'compares the figures with the previous year',
    'carries comment from the organisation and its rivals',
]
PLACES = ['Gaborone', 'Francistown', 'Maun', 'Selebi-Phikwe', 'Lobatse', 'Palapye', 'Kasane']

BODY = (
    '{headline}. Reporting from {place}, the publication {angle} and its likely effect on '
    'the wider {sector} sector in Botswana. Analysts quoted in the piece expect the impact '
    'to be felt over the coming quarters, and note that comparable moves elsewhere in the '
    'region have produced mixed results. The organisation declined to comment beyond its '
    'published statement issued on {when}.'
)

SECTORS = {'FNBB': 'financial services', 'Debswana': 'mining', 'BTC': 'telecommunications', None: 'business'}


class Command(BaseCommand):
    help = 'Seed every crawler model, plus the platform mirror it pushes into.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Delete existing crawler + mirror data before seeding.')
        parser.add_argument('--urls', type=int, default=90,
                            help='How many DiscoveredURLs to generate (default 90).')

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260818)
        self.now = timezone.now()

        if options['reset']:
            for model in (AlertMatch, CrawlerAlert, PlatformAlertNotification, ArticleMatch,
                          OrganisationKeyword, OrganisationSource, Organisation,
                          ParsedArticle, FetchedPage, CrawlJob, DiscoveredURL,
                          ExtractionRule, SeedSource,
                          CompetitorArticle, OnlineArticle, Keyword, Competitor,
                          PlatformAlert, Organization):
                model.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.WARNING('  reset: crawler + mirror tables cleared'))

        operators = self.seed_operators()
        seeds = self.seed_sources()
        self.seed_extraction_rules(seeds)
        urls = self.seed_discovered_urls(seeds, options['urls'])
        self.seed_crawl_jobs(urls)
        articles = self.seed_pages_and_articles(urls)
        crawler_orgs = self.seed_matching(articles)
        alerts = self.seed_alerts(articles)
        mirror = self.seed_platform_mirror(articles)

        self.report(operators, seeds, urls, articles, crawler_orgs, alerts, mirror)

    # ── Crawler operators (Django's default auth.User) ───────────────────────

    def seed_operators(self):
        people = [
            ('crawler-admin', 'crawler-admin@sociallightbw.com', 'Crawler', 'Admin', True, True),
            ('crawler-ops', 'ops@sociallightbw.com', 'Ops', 'Engineer', False, True),
            ('crawler-analyst', 'analyst@sociallightbw.com', 'Data', 'Analyst', False, False),
        ]
        out = []
        for username, email, first, last, is_super, is_staff in people:
            user = User.objects.filter(username=username).first()
            if not user:
                maker = User.objects.create_superuser if is_super else User.objects.create_user
                user = maker(username=username, email=email, password=SEED_PASSWORD,
                             first_name=first, last_name=last)
                user.is_staff = is_staff
                user.save()
            out.append(user)
        return out

    # ── Discovery ────────────────────────────────────────────────────────────

    def seed_sources(self):
        out = []
        for spec in SEEDS:
            seed, _ = SeedSource.objects.get_or_create(
                url=spec['url'],
                defaults={
                    'name': spec['name'],
                    'source_type': spec['source_type'],
                    'crawl_interval': spec['crawl_interval'],
                    'keyword_filter': spec['keyword_filter'],
                    'use_playwright': spec['use_playwright'],
                    'meta': spec['meta'],
                    'is_active': spec['source_type'] != SourceType.MANUAL,
                    'last_crawled_at': self.now - timedelta(minutes=random.randint(5, 400)),
                },
            )
            seed._domain = spec['domain']
            out.append(seed)
        return out

    def seed_extraction_rules(self, seeds):
        rules = {
            'www.mmegi.bw': ('h1.entry-title', 'div.entry-content', 'span.author', 'time.entry-date', ''),
            'www.miningweekly.com': ('h1.article-title', 'div.article-body', 'div.byline', 'span.date', '%d %B %Y'),
            'www.businessweekly.co.bw': ('h1.post-title', 'div.post-body', '', 'time', ''),
        }
        for seed in seeds:
            selectors = rules.get(seed._domain)
            if not selectors:
                continue
            title, body, author, date_sel, fmt = selectors
            ExtractionRule.objects.get_or_create(
                seed=seed, domain=seed._domain,
                defaults={'title_selector': title, 'body_selector': body,
                          'author_selector': author, 'date_selector': date_sel,
                          'date_format': fmt},
            )

    def seed_discovered_urls(self, seeds, count):
        """Spread URLs across seeds with a realistic status mix."""
        statuses = (
            [URLStatusChoices.PARSED] * 12
            + [URLStatusChoices.FETCHED] * 2
            + [URLStatusChoices.PENDING] * 3
            + [URLStatusChoices.FAILED, URLStatusChoices.SKIPPED, URLStatusChoices.FETCHING]
        )
        out = []
        for i in range(count):
            seed = seeds[i % len(seeds)]
            org_short, headline = HEADLINES[i % len(HEADLINES)]
            slug = headline.lower().replace(' ', '-')[:60].strip('-')
            url = f'https://{seed._domain}/{2026}/{(i % 12) + 1:02d}/{slug}-{i}'
            status = random.choice(statuses)
            published = self.now - timedelta(days=random.randint(0, 120), hours=random.randint(0, 23))
            discovered, _ = DiscoveredURL.objects.get_or_create(
                url_hash=DiscoveredURL.hash_url(url),
                defaults={
                    'seed': seed,
                    'url': url,
                    'title': headline,
                    'snippet': headline + '. Read the full report.',
                    'published_at': published,
                    'status': status,
                    'error_message': 'HTTP 403 from origin' if status == URLStatusChoices.FAILED else '',
                },
            )
            discovered._headline = headline
            discovered._org_short = org_short
            discovered._domain = seed._domain
            out.append(discovered)
        return out

    def seed_crawl_jobs(self, urls):
        for discovered in urls:
            if discovered.status == URLStatusChoices.PENDING:
                continue
            attempts = 2 if discovered.status == URLStatusChoices.FAILED else 1
            for attempt in range(attempts):
                started = self.now - timedelta(days=random.randint(0, 30), minutes=random.randint(0, 600))
                finished = started + timedelta(seconds=random.uniform(0.8, 9.0))
                failed = discovered.status == URLStatusChoices.FAILED
                # Deterministic task id so re-running the seed tops up rather
                # than stacking a fresh job onto every URL.
                task_id = uuid.uuid5(ORG_NAMESPACE, f'crawljob:{discovered.url_hash}:{attempt}')
                CrawlJob.objects.get_or_create(
                    discovered_url=discovered,
                    celery_task_id=str(task_id),
                    defaults={
                        'trigger': (CrawlJob.TriggerType.RETRY if attempt
                                    else random.choice([CrawlJob.TriggerType.SCHEDULED,
                                                        CrawlJob.TriggerType.SCHEDULED,
                                                        CrawlJob.TriggerType.MANUAL])),
                        'status': discovered.status,
                        'started_at': started,
                        'finished_at': finished,
                        'http_status': 403 if failed else 200,
                        'error_message': 'HTTP 403 from origin' if failed else '',
                    },
                )

    # ── Fetch + parse ────────────────────────────────────────────────────────

    def seed_pages_and_articles(self, urls):
        """Fetch + parse everything past the fetched stage.

        Every seventh parsed article deliberately reuses the previous article's
        body verbatim — syndicated copy — so the deduplication fields
        (content_hash, duplicate_of, is_duplicate) have something real to show.
        """
        articles = []
        previous_body = None
        for index, discovered in enumerate(urls):
            if discovered.status not in (URLStatusChoices.FETCHED, URLStatusChoices.PARSED):
                continue

            headline = discovered._headline
            sector = SECTORS[discovered._org_short]
            syndicated = previous_body is not None and index % 7 == 0
            body = previous_body if syndicated else BODY.format(
                headline=headline,
                place=PLACES[index % len(PLACES)],
                angle=ANGLES[index % len(ANGLES)],
                sector=sector,
                when=(discovered.published_at or self.now).strftime('%d %B %Y'),
            )
            html = f'<html><head><title>{headline}</title></head><body><article><h1>{headline}</h1><p>{body}</p></article></body></html>'

            page, _ = FetchedPage.objects.get_or_create(
                discovered_url=discovered,
                defaults={
                    'status_code': 200,
                    'content_type': 'text/html; charset=utf-8',
                    'encoding': 'utf-8',
                    'raw_html': html,
                    'fetch_duration_ms': random.randint(180, 4200),
                },
            )
            if discovered.status != URLStatusChoices.PARSED:
                continue

            content_hash = hashlib.sha256(body.lower().encode()).hexdigest()
            original = (ParsedArticle.objects
                        .filter(content_hash=content_hash, is_duplicate=False)
                        .first())
            sentiment = random.choice(['positive', 'positive', 'neutral', 'neutral', 'negative'])
            article, created = ParsedArticle.objects.get_or_create(
                fetched_page=page,
                defaults={
                    'title': headline,
                    'body_text': body,
                    'summary': body[:180] + '…',
                    'author': random.choice(['B. Kgosi', 'L. Tebogo', 'Staff Reporter', 'M. Dintwe', '']),
                    'published_at': discovered.published_at,
                    'source_domain': discovered._domain,
                    'country': random.choice(['Botswana', 'Botswana', 'South Africa', 'Namibia']),
                    'language': 'en',
                    'tags': [t for t in [discovered._org_short, sector] if t],
                    'signals': {
                        'keywords': [w for w in headline.split() if len(w) > 5][:5],
                        'entities': {
                            'ORG': [discovered._org_short] if discovered._org_short else [],
                            'GPE': ['Botswana'],
                        },
                        'sentiment': sentiment,
                    },
                    'content_hash': content_hash,
                    'canonical_url': discovered.url,
                    'duplicate_of': original,
                    'is_duplicate': original is not None,
                },
            )
            if created:
                previous_body = body
            article._org_short = discovered._org_short
            articles.append(article)
        return articles

    # ── Crawler-side organisation matching ───────────────────────────────────

    def seed_matching(self, articles):
        crawler_orgs = {}
        for spec in ORGS:
            org, _ = Organisation.objects.get_or_create(name=spec['name'])
            crawler_orgs[spec['short']] = org
            for keyword in spec['keywords']:
                OrganisationKeyword.objects.get_or_create(organisation=org, keyword=keyword)
            for domain in spec['domains']:
                OrganisationSource.objects.get_or_create(organisation=org, domain=domain)

        for article in articles:
            if article.is_duplicate:
                continue
            title_lower = article.title.lower()
            body_lower = article.body_text.lower()
            for short, org in crawler_orgs.items():
                for keyword_obj in org.keywords.all():
                    term = keyword_obj.keyword.lower()
                    in_title = term in title_lower
                    if not in_title and term not in body_lower:
                        continue
                    ArticleMatch.objects.get_or_create(
                        parsed_article=article, organisation=org,
                        matched_keyword=keyword_obj.keyword,
                        defaults={
                            'matched_in': ArticleMatch.MatchedIn.TITLE if in_title
                            else ArticleMatch.MatchedIn.BODY,
                            'confidence': 1.0 if in_title else 0.5,
                        },
                    )
        return crawler_orgs

    # ── Crawler-native alerts ────────────────────────────────────────────────

    def seed_alerts(self, articles):
        specs = [
            ('Botswana banking watch', 'FNB Botswana, FNBB, Absa, Stanbic, eWallet',
             '', 'en', '', 'ops@sociallightbw.com', ''),
            ('Diamond & mining watch', 'Debswana, Jwaneng, Orapa, Lucara, Khoemacau',
             'www.miningweekly.com', 'en', '', 'analyst@sociallightbw.com',
             'https://hooks.sociallightbw.com/mining'),
            ('Telecom negative sentiment', 'BTC, Mascom, Orange Botswana, outage',
             '', 'en', CrawlerAlert.SentimentFilter.NEGATIVE, 'ops@sociallightbw.com', ''),
        ]
        alerts = []
        for name, keywords, domain, language, sentiment, email, webhook in specs:
            alert, _ = CrawlerAlert.objects.get_or_create(
                name=name,
                defaults={'keywords': keywords, 'source_domain': domain, 'language': language,
                          'sentiment': sentiment, 'email': email, 'webhook_url': webhook},
            )
            alerts.append(alert)

        for alert in alerts:
            terms = alert.keywords_list
            for article in articles:
                if article.is_duplicate:
                    continue
                if alert.source_domain and article.source_domain != alert.source_domain:
                    continue
                if alert.sentiment and (article.signals or {}).get('sentiment') != alert.sentiment:
                    continue
                haystack = f'{article.title} {article.body_text}'.lower()
                hits = [t for t in terms if t in haystack]
                if not hits:
                    continue
                notified = random.random() < 0.7
                AlertMatch.objects.get_or_create(
                    alert=alert, article=article,
                    defaults={'matched_keywords': hits, 'notified': notified,
                              'notified_at': self.now - timedelta(hours=random.randint(1, 200))
                              if notified else None},
                )
        return alerts

    # ── The platform mirror (owned by the platform in production) ────────────

    def seed_platform_mirror(self, articles):
        mirror = {'orgs': [], 'online': 0, 'competitor': 0}

        for spec in ORGS:
            org, _ = Organization.objects.get_or_create(
                id=org_uuid(spec['name']),
                defaults={'name': spec['name'], 'email': spec['email'],
                          'industry': spec['industry'], 'country': 'Botswana',
                          'website': spec['website']},
            )
            mirror['orgs'].append(org)

            for keyword in spec['keywords']:
                Keyword.objects.get_or_create(organization=org, keyword=keyword, category='brand')

            competitors = []
            for name, aliases in spec['competitors']:
                competitor, _ = Competitor.objects.get_or_create(
                    organization=org, name=name,
                    defaults={'aliases': aliases, 'website': ''},
                )
                competitors.append(competitor)

            PlatformAlert.objects.get_or_create(
                organization=org, name=f'{spec["short"]} brand mentions',
                defaults={'keywords': ', '.join(spec['keywords']),
                          'email': spec['email'], 'frequency': 'daily',
                          'email_subject': f'{spec["short"]} — daily media digest',
                          'start_date': (self.now - timedelta(days=90)).date()},
            )

            # Push: every non-duplicate parsed article that matches this org's
            # keywords becomes OnlineArticle; competitor hits become CompetitorArticle.
            terms = [k.lower() for k in spec['keywords']]
            competitor_terms = {c: [t.lower() for t in c.match_terms()] for c in competitors}

            for article in articles:
                if article.is_duplicate:
                    continue
                title_lower = article.title.lower()
                body_lower = article.body_text.lower()
                reach = random.randint(3_000, 260_000)
                sentiment = (article.signals or {}).get('sentiment', 'neutral')
                published = (article.published_at or self.now).date()

                if any(t in title_lower or t in body_lower for t in terms):
                    _, created = OnlineArticle.objects.get_or_create(
                        organization=org, url=article.url,
                        defaults={
                            'source': article.source_domain,
                            'headline': article.title,
                            'summary': article.summary,
                            'date_published': published,
                            'country': article.country,
                            'sentiment': sentiment,
                            'ave': Decimal(str(round(reach * random.uniform(0.3, 0.9), 2))),
                            'coverage': 'Earned',
                            'reach': reach,
                            'relevancy': 1.0 if any(t in title_lower for t in terms) else 0.5,
                        },
                    )
                    mirror['online'] += int(created)

                for competitor, ctems in competitor_terms.items():
                    if not any(t in title_lower or t in body_lower for t in ctems):
                        continue
                    _, created = CompetitorArticle.objects.get_or_create(
                        organization=org, competitor=competitor, url=article.url,
                        defaults={
                            'company_name': competitor.name,
                            'headline': article.title,
                            'summary': article.summary,
                            'source': article.source_domain,
                            'date_published': published,
                            'country': article.country,
                            'matched_keywords': competitor.match_terms()[0],
                            'sentiment_score': round(random.uniform(-1, 1), 3),
                            'sentiment': sentiment,
                            'reach': reach,
                            'cpm': round(random.uniform(4, 40), 2),
                            'ave': Decimal(str(round(reach * random.uniform(0.3, 0.8), 2))),
                            'rank': round(random.uniform(1, 5), 2),
                            'coverage_type': 'Earned',
                        },
                    )
                    mirror['competitor'] += int(created)

        # Dedup/audit rows for notifications fired off the *platform's* alerts.
        # These live in the crawler DB because the platform schema is read-only here.
        for alert in PlatformAlert.objects.all():
            pushed = OnlineArticle.objects.filter(organization=alert.organization)[:4]
            for article in pushed:
                sent = random.random() < 0.75
                PlatformAlertNotification.objects.get_or_create(
                    alert_id=alert.pk, article_id=article.pk,
                    defaults={
                        'organization_id': str(alert.organization_id),
                        'alert_name': alert.name,
                        'recipient': alert.email,
                        'matched_keywords': alert.keyword_list()[:3],
                        'frequency': alert.frequency,
                        'sent': sent,
                        'sent_at': self.now - timedelta(hours=random.randint(1, 72)) if sent else None,
                    },
                )
        return mirror

    # ── Output ───────────────────────────────────────────────────────────────

    def report(self, operators, seeds, urls, articles, crawler_orgs, alerts, mirror):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Crawler seeded.'))
        self.stdout.write('  Crawler operators (Django auth.User - admin/dashboard only):')
        for user in operators:
            self.stdout.write(f'    {user.username}  {user.email}  '
                              f'staff={user.is_staff} superuser={user.is_superuser}')
        self.stdout.write('')
        self.stdout.write('  Pipeline (crawler-owned):')
        self.stdout.write(f'    SeedSource       {SeedSource.objects.count()}'
                          f'  (active {SeedSource.objects.filter(is_active=True).count()})')
        self.stdout.write(f'    ExtractionRule   {ExtractionRule.objects.count()}')
        self.stdout.write(f'    DiscoveredURL    {DiscoveredURL.objects.count()}')
        for status, _label in URLStatusChoices.choices:
            count = DiscoveredURL.objects.filter(status=status).count()
            if count:
                self.stdout.write(f'      {status:<9} {count}')
        self.stdout.write(f'    CrawlJob         {CrawlJob.objects.count()}')
        self.stdout.write(f'    FetchedPage      {FetchedPage.objects.count()}')
        self.stdout.write(f'    ParsedArticle    {ParsedArticle.objects.count()}'
                          f'  (duplicates {ParsedArticle.objects.filter(is_duplicate=True).count()})')
        self.stdout.write('')
        self.stdout.write('  Matching (crawler-owned):')
        self.stdout.write(f'    Organisation     {Organisation.objects.count()}')
        self.stdout.write(f'    Keywords         {OrganisationKeyword.objects.count()}')
        self.stdout.write(f'    Sources          {OrganisationSource.objects.count()}')
        self.stdout.write(f'    ArticleMatch     {ArticleMatch.objects.count()}')
        self.stdout.write('')
        self.stdout.write('  Alerts (crawler-owned):')
        self.stdout.write(f'    Alert            {CrawlerAlert.objects.count()}')
        self.stdout.write(f'    AlertMatch       {AlertMatch.objects.count()}'
                          f'  (notified {AlertMatch.objects.filter(notified=True).count()})')
        self.stdout.write(f'    PlatformAlertNotification {PlatformAlertNotification.objects.count()}')
        self.stdout.write('')
        self.stdout.write('  Platform mirror (platform-owned in production, local here):')
        self.stdout.write(f'    Organization     {Organization.objects.count()}')
        self.stdout.write(f'    Keyword          {Keyword.objects.count()}')
        self.stdout.write(f'    Competitor       {Competitor.objects.count()}')
        self.stdout.write(f'    Alert            {PlatformAlert.objects.count()}')
        self.stdout.write(f'    OnlineArticle    {OnlineArticle.objects.count()}  (pushed this run: {mirror["online"]})')
        self.stdout.write(f'    CompetitorArticle {CompetitorArticle.objects.count()}  (pushed this run: {mirror["competitor"]})')
        self.stdout.write('')
        self.stdout.write(f'  Every seeded operator logs in with the password: {SEED_PASSWORD}')
