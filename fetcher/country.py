"""
fetcher/country.py

Determines the publication country of a crawled article.

Resolution order (first match wins):
  1. Known-domain table  — explicit mapping for well-known publications
  2. ccTLD extraction    — .co.bw → Botswana, .co.za → South Africa, etc.
  3. GPE entity fallback — most-mentioned country name in the article text,
                           drawn from the spaCy NER signals already computed
  4. "International"     — only used when all three layers return nothing
                           (e.g. a brand-new .com site with no GPE mentions)

Never returns an empty string or "Unknown".
"""
from urllib.parse import urlparse
from collections import Counter

# ── 1. Known publications (domain → country) ─────────────────────────────────
# Covers .com/.org sites where TLD alone is uninformative.
KNOWN_DOMAINS: dict[str, str] = {
    # ── Botswana ──────────────────────────────────────────────────────────────
    "dailynews.co.bw":          "Botswana",
    "mmegi.co.bw":              "Botswana",
    "thepatriot.co.bw":         "Botswana",
    "sundaystandard.info":      "Botswana",
    "botswanapost.co.bw":       "Botswana",
    "voice.co.bw":              "Botswana",
    "weekendpost.co.bw":        "Botswana",
    "botswanagazette.co.bw":    "Botswana",
    "bbc.com":                  "United Kingdom",
    # ── South Africa ──────────────────────────────────────────────────────────
    "miningweekly.com":         "South Africa",
    "engineeringnews.co.za":    "South Africa",
    "businesslive.co.za":       "South Africa",
    "news24.com":               "South Africa",
    "dailymaverick.co.za":      "South Africa",
    "timeslive.co.za":          "South Africa",
    "sowetanlive.co.za":        "South Africa",
    "bdlive.co.za":             "South Africa",
    "fin24.com":                "South Africa",
    "moneyweb.co.za":           "South Africa",
    "defenceweb.co.za":         "South Africa",
    "groundup.org.za":          "South Africa",
    "iol.co.za":                "South Africa",
    "citizen.co.za":            "South Africa",
    "dispatchlive.co.za":       "South Africa",
    # ── East Africa ───────────────────────────────────────────────────────────
    "nation.africa":            "Kenya",
    "businessdailyafrica.com":  "Kenya",
    "standardmedia.co.ke":      "Kenya",
    "monitor.co.ug":            "Uganda",
    "newvision.co.ug":          "Uganda",
    "guardian.co.tz":           "Tanzania",
    "thecitizen.co.tz":         "Tanzania",
    "theeastafrican.co.ke":     "Kenya",
    "newtimes.co.rw":           "Rwanda",
    # ── West Africa ───────────────────────────────────────────────────────────
    "businessdayng.com":        "Nigeria",
    "premiumtimesng.com":       "Nigeria",
    "thisdaylive.com":          "Nigeria",
    "graphic.com.gh":           "Ghana",
    "myjoyonline.com":          "Ghana",
    # ── Southern Africa ───────────────────────────────────────────────────────
    "herald.co.zw":             "Zimbabwe",
    "newsday.co.zw":            "Zimbabwe",
    "lusakatimes.com":          "Zambia",
    "times.co.zm":              "Zambia",
    "namibian.com.na":          "Namibia",
    "mwnation.com":             "Malawi",
    "nyasatimes.com":           "Malawi",
    # ── Pan-African / International ───────────────────────────────────────────
    "theafricareport.com":      "France",
    "africanews.com":           "France",
    "allafrica.com":            "United States",
    "aljazeera.com":            "Qatar",
    "reuters.com":              "United States",
    "bloomberg.com":            "United States",
    "cnn.com":                  "United States",
    "nytimes.com":              "United States",
    "washingtonpost.com":       "United States",
    "apnews.com":               "United States",
    "theguardian.com":          "United Kingdom",
    "independent.co.uk":        "United Kingdom",
    "ft.com":                   "United Kingdom",
    "economist.com":            "United Kingdom",
    "france24.com":             "France",
    "dw.com":                   "Germany",
    "aa.com.tr":                "Turkey",
}

# ── 2. ccTLD → country ────────────────────────────────────────────────────────
# Ordered longest-first so .co.bw is matched before .bw
CCTLD_MAP: dict[str, str] = {
    # Africa
    "co.bw": "Botswana",    "bw":  "Botswana",
    "co.za": "South Africa","za":  "South Africa",
    "co.ke": "Kenya",       "ke":  "Kenya",
    "co.ug": "Uganda",      "ug":  "Uganda",
    "co.tz": "Tanzania",    "tz":  "Tanzania",
    "co.rw": "Rwanda",      "rw":  "Rwanda",
    "co.zm": "Zambia",      "zm":  "Zambia",
    "co.zw": "Zimbabwe",    "zw":  "Zimbabwe",
    "com.na":"Namibia",     "na":  "Namibia",
    "co.mw": "Malawi",      "mw":  "Malawi",
    "co.ls": "Lesotho",     "ls":  "Lesotho",
    "co.sz": "Eswatini",    "sz":  "Eswatini",
    "com.mu":"Mauritius",   "mu":  "Mauritius",
    "co.ng": "Nigeria",     "ng":  "Nigeria",
    "com.gh":"Ghana",       "gh":  "Ghana",
    "co.gh": "Ghana",
    "co.ug": "Uganda",
    "co.ci": "Ivory Coast", "ci":  "Ivory Coast",
    "co.cm": "Cameroon",    "cm":  "Cameroon",
    "co.sn": "Senegal",     "sn":  "Senegal",
    "co.ao": "Angola",      "ao":  "Angola",
    "co.mz": "Mozambique",  "mz":  "Mozambique",
    "co.mg": "Madagascar",  "mg":  "Madagascar",
    "co.et": "Ethiopia",    "et":  "Ethiopia",
    "co.eg": "Egypt",       "eg":  "Egypt",
    "co.ma": "Morocco",     "ma":  "Morocco",
    "co.tn": "Tunisia",     "tn":  "Tunisia",
    "co.dz": "Algeria",     "dz":  "Algeria",
    # Global
    "co.uk": "United Kingdom", "uk": "United Kingdom",
    "com.au":"Australia",   "au":  "Australia",
    "co.nz": "New Zealand", "nz":  "New Zealand",
    "co.in": "India",       "in":  "India",
    "com.sg":"Singapore",   "sg":  "Singapore",
    "com.my":"Malaysia",    "my":  "Malaysia",
    "com.br":"Brazil",      "br":  "Brazil",
    "com.ae":"United Arab Emirates", "ae": "United Arab Emirates",
    "com.sa":"Saudi Arabia","sa":  "Saudi Arabia",
    "com.pk":"Pakistan",    "pk":  "Pakistan",
    "ca":  "Canada",
    "de":  "Germany",
    "fr":  "France",
    "jp":  "Japan",
    "cn":  "China",
    "ru":  "Russia",
    "nl":  "Netherlands",
    "se":  "Sweden",
    "no":  "Norway",
    "dk":  "Denmark",
    "fi":  "Finland",
    "it":  "Italy",
    "es":  "Spain",
    "pt":  "Portugal",
    "pl":  "Poland",
    "ie":  "Ireland",
    "ch":  "Switzerland",
    "at":  "Austria",
    "be":  "Belgium",
    "us":  "United States",
}

# ── 3. GPE entity filter — names that are actual countries ────────────────────
# Used to distinguish country GPE entities from city GPE entities.
COUNTRY_NAMES: frozenset[str] = frozenset({
    "Afghanistan","Albania","Algeria","Angola","Argentina","Armenia","Australia",
    "Austria","Azerbaijan","Bahrain","Bangladesh","Belgium","Bolivia","Botswana",
    "Brazil","Bulgaria","Cambodia","Cameroon","Canada","Chile","China","Colombia",
    "Croatia","Cuba","Cyprus","Czech Republic","Denmark","Ecuador","Egypt",
    "Eritrea","Estonia","Ethiopia","Finland","France","Germany","Ghana","Greece",
    "Guatemala","Honduras","Hungary","India","Indonesia","Iran","Iraq","Ireland",
    "Israel","Italy","Ivory Coast","Jamaica","Japan","Jordan","Kazakhstan","Kenya",
    "Kuwait","Latvia","Lebanon","Lesotho","Libya","Lithuania","Luxembourg","Malawi",
    "Malaysia","Mali","Mauritius","Mexico","Morocco","Mozambique","Myanmar","Namibia",
    "Nepal","Netherlands","New Zealand","Nigeria","North Korea","Norway","Oman",
    "Pakistan","Panama","Paraguay","Peru","Philippines","Poland","Portugal","Qatar",
    "Romania","Russia","Rwanda","Saudi Arabia","Senegal","Serbia","Singapore",
    "Slovakia","Slovenia","Somalia","South Africa","South Korea","South Sudan","Spain",
    "Sri Lanka","Sudan","Sweden","Switzerland","Syria","Taiwan","Tanzania","Thailand",
    "Tunisia","Turkey","Uganda","Ukraine","United Arab Emirates","United Kingdom",
    "United States","Uruguay","Uzbekistan","Venezuela","Vietnam","Yemen","Zambia",
    "Zimbabwe","Eswatini","Swaziland","Burkina Faso","Burundi","Benin","Chad",
    "Republic of Congo","Democratic Republic of Congo","DR Congo","DRC",
    "Central African Republic","Comoros","Djibouti","Equatorial Guinea","Gabon",
    "Gambia","Guinea","Guinea-Bissau","Liberia","Madagascar","Mauritania","Niger",
    "Seychelles","Sierra Leone","Togo","Cape Verde","São Tomé and Príncipe",
    "UK","US","USA","UAE","DRC",
    # Common aliases
    "Britain","Great Britain","England","Scotland","Wales","Northern Ireland",
    "America","Korea",
})

_ALIAS: dict[str, str] = {
    "UK": "United Kingdom", "Britain": "United Kingdom",
    "Great Britain": "United Kingdom", "England": "United Kingdom",
    "Scotland": "United Kingdom", "Wales": "United Kingdom",
    "Northern Ireland": "United Kingdom",
    "US": "United States", "USA": "United States", "America": "United States",
    "UAE": "United Arab Emirates",
    "DRC": "Democratic Republic of Congo",
    "DR Congo": "Democratic Republic of Congo",
    "Korea": "South Korea",
    "Swaziland": "Eswatini",
}


def _strip_www(domain: str) -> str:
    return domain[4:] if domain.startswith("www.") else domain


def _tld_from_domain(domain: str) -> str | None:
    """
    Return the country name for domain using ccTLD matching.
    Tries longest suffix first so co.za beats za.
    """
    d = _strip_www(domain).lower()
    # Try two-level suffixes first (co.bw, co.za, com.au …)
    for suffix, country in CCTLD_MAP.items():
        if d.endswith("." + suffix):
            return country
    # Single-level (.bw, .za, .ke …) — but skip generic TLDs
    generic = {"com", "org", "net", "io", "info", "co", "biz", "app", "dev", "media"}
    parts = d.rsplit(".", 1)
    if len(parts) == 2 and parts[1] not in generic:
        return CCTLD_MAP.get(parts[1])
    return None


def _country_from_gpe(signals: dict) -> str | None:
    """
    Return the most-mentioned country name found in the spaCy GPE entity list.
    Filters to actual country names to avoid picking up city names.
    """
    gpe_list: list[str] = signals.get("entities", {}).get("GPE", [])
    if not gpe_list:
        return None
    counter: Counter = Counter()
    for gpe in gpe_list:
        name = gpe.strip()
        if name in COUNTRY_NAMES:
            normalized = _ALIAS.get(name, name)
            counter[normalized] += 1
    if counter:
        return counter.most_common(1)[0][0]
    return None


def detect_country(source_domain: str, signals: dict) -> str:
    """
    Return the publication country for an article. Never returns empty or 'Unknown'.

    Args:
        source_domain: netloc of the article URL (e.g. 'www.mmegi.co.bw')
        signals:       NLP signals dict from ParsedArticle.signals

    Returns:
        Country name string, e.g. 'Botswana', 'South Africa', 'International'
    """
    domain = _strip_www(source_domain.lower())

    # 1. Known-domain table
    if domain in KNOWN_DOMAINS:
        return KNOWN_DOMAINS[domain]

    # 2. ccTLD
    country = _tld_from_domain(domain)
    if country:
        return country

    # 3. GPE entities in the article
    country = _country_from_gpe(signals)
    if country:
        return country

    return "International"
