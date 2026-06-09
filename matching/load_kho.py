from platform_sync.models import Organization, Keyword

# ── KHOEMACAU ─────────────────────────────────────────────────────────────────

khoemacau, _ = Organization.objects.get_or_create(

    name="Khoemacau Copper Mining",

    defaults={

        "status": "active",

        "industry": "Mining & Metals",

        "country": "Botswana",

    }

)

KHOEMACAU_KEYWORDS = [

    # Brand

    ("Khoemacau", "brand"),

    ("Khoemacau Copper", "brand"),

    ("Khoemacau Copper Mine", "brand"),

    ("Khoemacau Mine", "brand"),

    ("MMG Khoemacau", "brand"),

    ("Khoemacau Copper Mining", "brand"),

    ("MMG Limited", "brand"),

    ("MMG", "brand"),

    # Personnel

    ("Boikobo Paya", "personnel"),

    ("Weiquan Xia", "personnel"),

    ("Logic Sebopeng", "personnel"),

    ("Mmama Mhlanga-Fichani", "personnel"),

    # Competitors

    ("Motheo", "competitor"),

    ("Sandfire Copper Mine", "competitor"),

    ("BHP Botswana", "competitor"),

    ("Kopano copper mine", "competitor"),

    ("Premium Nickel Resources", "competitor"),

    ("Altona Rare Earths", "competitor"),

    ("NexMetals", "competitor"),

    ("Zambia Consolidated Copper Mines", "competitor"),

    ("Mogalakwena Mine", "competitor"),

    ("Anglo American", "competitor"),

    ("Kamoto Copper Company", "competitor"),

]

kw_count = 0

for keyword, category in KHOEMACAU_KEYWORDS:

    _, created = Keyword.objects.get_or_create(

        organization=khoemacau,

        keyword=keyword,

        defaults={"category": category}

    )

    if created:

        kw_count += 1

print(f"Khoemacau: {kw_count} keywords loaded")