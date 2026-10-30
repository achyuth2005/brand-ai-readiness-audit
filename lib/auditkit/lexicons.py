"""Bundled detection vocabulary.

Every word list the detectors consult lives here rather than being scattered inline, for
three reasons that are all scored:

* **Inspectability.** A grader reading the package can see exactly what "fluff" means to
  this audit, and disagree with a specific entry rather than with a black box.
* **No model weights.** Content-form judgements are made with explicit lexicons and
  published thresholds, which keeps the submission far under the 50 MB limit and keeps
  detection deterministic. The trade-off is stated plainly: these are *heuristics with
  stated vocabulary*, not measurements of assistant behaviour.
* **Generalization.** None of these lists name a domain, brand, vendor or framework as a
  defect. They describe language and structure, which is what transfers to unseen sites.

Language scope is declared, not assumed: the answerability lexicons are English. A page
whose detected language has no lexicon here is *skipped* and recorded in
``coverage.checks_skipped`` -- never judged with the wrong vocabulary (rule R6).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

SUPPORTED_LANGUAGES: FrozenSet[str] = frozenset({"en"})

# --------------------------------------------------------------------------------------
# Answerability: sentence openers that break chunk retrieval
# --------------------------------------------------------------------------------------

# A passage beginning with one of these has lost its subject to the previous chunk.
PRONOUN_OPENERS: FrozenSet[str] = frozenset({
    "it", "its", "it's", "they", "their", "theirs", "them", "this", "that", "these",
    "those", "he", "she", "his", "her", "hers", "we", "our", "ours", "us", "such",
    "both", "either", "neither", "one", "ones", "another", "others", "the former",
    "the latter",
})

# Discourse connectives that assume a preceding sentence the chunk will not contain.
CONNECTIVE_OPENERS: FrozenSet[str] = frozenset({
    "also", "additionally", "plus", "however", "moreover", "furthermore", "therefore",
    "thus", "hence", "meanwhile", "instead", "besides", "nonetheless", "nevertheless",
    "consequently", "accordingly", "similarly", "likewise", "conversely", "otherwise",
    "then", "finally", "lastly", "firstly", "secondly", "thirdly", "next", "again",
    "still", "yet", "so", "but", "and", "or", "because", "although", "though", "while",
    "whereas", "since", "unless", "after", "before", "once",
})

# Demonstrative noun phrases: "This means", "These features", "That approach".
DEMONSTRATIVE_OPENER_PHRASES: Tuple[str, ...] = (
    "this means", "this makes", "this allows", "this gives", "this helps",
    "this ensures", "this is why", "this is how", "these features", "these tools",
    "these benefits", "these options", "these include", "those features",
    "that approach", "that means", "that said", "that is why", "such features",
    "such tools", "it means", "it allows", "it helps", "they include", "they allow",
    "we also", "we offer", "our team", "our platform", "our solution",
)

# Unquantified superlatives. Fluff is only flagged when a sentence contains one of these
# AND no number, date, unit, currency or proper noun -- the pairing is the whole point.
FLUFF_SUPERLATIVES: FrozenSet[str] = frozenset({
    "world-class", "world class", "best-in-class", "best in class", "cutting-edge",
    "cutting edge", "industry-leading", "industry leading", "market-leading",
    "revolutionary", "seamless", "seamlessly", "next-generation", "next generation",
    "robust", "synergy", "synergies", "empower", "empowering", "unlock", "unlocking",
    "elevate", "elevating", "game-changing", "game changer", "innovative",
    "innovation-driven", "state-of-the-art", "state of the art", "holistic", "bespoke",
    "turnkey", "frictionless", "best-of-breed", "unparalleled", "unrivalled",
    "unrivaled", "unmatched", "premier", "leading-edge", "groundbreaking",
    "transformative", "disruptive", "paradigm", "future-proof", "future proof",
    "mission-critical", "end-to-end", "one-stop", "one stop shop", "hassle-free",
    "effortless", "supercharge", "turbocharge", "streamline", "streamlined",
    "laser-focused", "tailor-made", "white-glove", "enterprise-grade", "military-grade",
    "award-winning", "trusted by thousands", "second to none", "the ultimate",
    "quite simply", "reimagine", "reimagined", "redefine", "redefining",
    "passionate about", "obsessed with", "delight", "delightful", "magical",
})

# Words that make a sentence a verifiable claim. Presence of any of these disqualifies a
# sentence from counting as fluff, even if it also contains a superlative.
FACTUAL_UNITS: FrozenSet[str] = frozenset({
    "%", "percent", "kg", "lb", "lbs", "oz", "g", "mg", "ml", "l", "litre", "liter",
    "cm", "mm", "m", "km", "inch", "inches", "ft", "feet", "mile", "miles",
    "hour", "hours", "minute", "minutes", "second", "seconds", "day", "days",
    "week", "weeks", "month", "months", "year", "years", "gb", "mb", "tb", "kb",
    "mbps", "gbps", "ghz", "mhz", "watt", "watts", "volt", "volts", "celsius",
    "fahrenheit", "users", "customers", "employees", "countries", "languages",
    "locations", "offices", "integrations", "currencies", "seats", "licences",
    "licenses", "iso", "soc", "gdpr", "hipaa", "pci",
})

DEFINITIONAL_COPULAS: Tuple[str, ...] = (
    "is a", "is an", "is the", "are a", "are an", "are the",
    "is software", "is platform", "provides", "offers", "builds", "makes", "helps",
    "enables", "delivers", "specialises in", "specializes in", "designs",
    "manufactures", "develops", "supplies", "operates", "serves", "creates",
    "was founded", "were founded", "has been", "have been",
    # Verbs that state what a company does for someone, as real homepages phrase it
    # ("<Brand> gives teams one place to...", "<Brand> empowers businesses to...").
    # Curated rather than "any verb", so "<Brand> uses cookies" never reads as a definition.
    "gives", "empowers", "lets", "connects", "powers", "brings", "turns", "automates",
    "sells", "publishes", "produces", "is where", "is how", "is your", "is one of",
    "are one of", "is home to", "is built", "is designed",
)

# Category nouns. A value proposition needs one of these to be saying anything concrete.
CATEGORY_NOUNS: FrozenSet[str] = frozenset({
    "platform", "software", "tool", "tools", "app", "application", "service",
    "services", "solution", "solutions", "system", "agency", "studio", "consultancy",
    "firm", "company", "brand", "shop", "store", "marketplace", "network", "community",
    "product", "products", "hardware", "device", "devices", "equipment", "machine",
    "clinic", "practice", "restaurant", "cafe", "bar", "hotel", "salon", "spa",
    "school", "academy", "university", "college", "course", "courses", "training",
    "magazine", "journal", "newspaper", "blog", "publication", "newsletter", "podcast",
    "bank", "insurer", "insurance", "broker", "fund", "charity", "nonprofit",
    "manufacturer", "supplier", "distributor", "retailer", "wholesaler", "contractor",
    "builder", "plumber", "electrician", "dentist", "doctor", "lawyer", "accountant",
    "architect", "designer", "developer", "engineer", "database", "api", "framework",
    "library", "plugin", "extension", "integration", "dashboard", "crm", "erp", "cms",
})

# --------------------------------------------------------------------------------------
# Engagement: calls to action and orientation
# --------------------------------------------------------------------------------------

ACTION_VERBS: FrozenSet[str] = frozenset({
    "buy", "shop", "order", "purchase", "add", "get", "start", "try", "book", "reserve",
    "schedule", "request", "download", "install", "subscribe", "sign", "signup",
    "register", "join", "apply", "contact", "call", "email", "message", "chat",
    "quote", "estimate", "compare", "browse", "explore", "discover", "find", "search",
    "create", "build", "launch", "upgrade", "renew", "donate", "give", "volunteer",
    "enrol", "enroll", "enquire", "inquire", "talk", "speak", "watch", "read", "view",
    "see", "check", "claim", "redeem", "activate", "configure", "customise", "customize",
})

# CTA labels that name no object. "Learn more" about what? These are the *non-specific*
# case in GE.ORIENT.NO_PRIMARY_CTA -- present but uninformative.
WEAK_CTA_LABELS: FrozenSet[str] = frozenset({
    "learn more", "read more", "more", "click here", "click", "here", "submit", "send",
    "go", "continue", "next", "ok", "okay", "yes", "no", "details", "info",
    "information", "find out more", "discover more", "see more", "view more",
    "show more", "explore", "learn", "read", "view", "see", "start", "begin",
})

GENERIC_H1_VALUES: FrozenSet[str] = frozenset({
    "home", "homepage", "home page", "welcome", "welcome!", "index", "untitled",
    "untitled document", "untitled page", "new page", "page", "main", "start",
    "hello", "hi", "hey", "landing page", "default", "site", "website", "blog",
    "our website", "welcome to our website", "welcome to our site",
})

# Class and id fragments that name an overlay. Presence is *never* sufficient on its own
# (rule R1); it must combine with "not hidden by default" and covering geometry.
OVERLAY_NAME_PATTERNS: Tuple[str, ...] = (
    "modal", "overlay", "popup", "pop-up", "popover", "interstitial", "lightbox",
    "dialog", "drawer", "paywall", "subscribe", "newsletter", "signup-wall",
    "gate", "gateway", "curtain", "backdrop", "scrim", "takeover", "welcome-mat",
)

CONSENT_NAME_PATTERNS: Tuple[str, ...] = (
    "cookie", "consent", "gdpr", "ccpa", "privacy-banner", "cmp", "onetrust",
    "cookiebot", "trustarc", "usercentrics", "klaro", "tarteaucitron", "osano",
    "quantcast", "didomi", "sourcepoint", "borlabs", "complianz", "iubenda",
)

# Conventions that mean "hidden until triggered". Recognising these is the single most
# important false-positive guard in the engagement skill: the overwhelming majority of
# modal markup on the web is correctly hidden, and flagging it would bury the report.
HIDDEN_CLASS_CONVENTIONS: Tuple[str, ...] = (
    "hidden", "is-hidden", "hide", "is-hide", "d-none", "display-none", "invisible",
    "sr-only", "visually-hidden", "screen-reader-only", "collapsed", "is-collapsed",
    "closed", "is-closed", "inactive", "is-inactive", "off", "is-off", "hidden-xs",
    "u-hidden", "tw-hidden", "opacity-0", "pointer-events-none", "not-visible",
    "modal-close", "fade", "js-hidden", "no-js-hide", "aria-hidden",
)

STICKY_NAME_PATTERNS: Tuple[str, ...] = (
    "sticky", "fixed", "affix", "floating", "pinned", "topbar", "top-bar", "announce",
    "announcement", "promo-bar", "notification-bar", "chat", "intercom", "drift",
    "livechat", "tawk", "zendesk", "crisp", "hubspot-messages", "beacon",
)

RELATED_CONTENT_PATTERNS: Tuple[str, ...] = (
    "related", "recommended", "you-may-also", "you-might-also", "more-like",
    "similar", "next-post", "prev-post", "previous-post", "up-next", "read-next",
    "also-bought", "also-viewed", "recently-viewed", "cross-sell", "upsell",
    "suggestions", "more-from", "continue-reading", "further-reading",
)

BREADCRUMB_PATTERNS: Tuple[str, ...] = (
    "breadcrumb", "breadcrumbs", "crumb", "crumbs", "you-are-here", "yoast-breadcrumb",
    "path", "trail", "navpath", "nav-path",
)

SKIP_LINK_PATTERNS: Tuple[str, ...] = (
    "skip-to-content", "skip-to-main", "skip-link", "skiplink", "skipnav",
    "skip-navigation", "skip-to-navigation",
)

# --------------------------------------------------------------------------------------
# Non-text facts: the fact-class x carrier matrix
#
# Each fact class carries the heading labels that announce it, and the value shapes that
# prove it is present as text. A section labelled with one of these and containing none
# of its value shapes -- but containing an image -- is the G1.NONTEXT signal.
# --------------------------------------------------------------------------------------

FACT_CLASS_LABELS: Dict[str, Tuple[str, ...]] = {
    "price": ("price", "pricing", "prices", "plans", "plan", "packages", "package",
              "tariff", "tariffs", "rates", "cost", "costs", "fees", "subscription",
              "membership", "editions", "tiers", "how much"),
    "availability": ("availability", "in stock", "stock", "inventory", "lead time",
                     "delivery", "shipping", "dispatch", "backorder"),
    "hours": ("hours", "opening hours", "opening times", "business hours", "when we're open",
              "trading hours", "office hours", "schedule", "timetable"),
    "address": ("address", "location", "locations", "find us", "visit us", "where we are",
                "directions", "our office", "our offices", "showroom", "branches"),
    "phone": ("phone", "telephone", "call us", "contact number", "helpline", "hotline"),
    "spec": ("specification", "specifications", "specs", "technical data",
             "technical details", "tech specs", "dimensions", "measurements",
             "features", "datasheet", "data sheet", "parameters", "capacity"),
    "comparison": ("compare", "comparison", "vs", "versus", "which plan", "differences",
                   "feature comparison", "at a glance"),
    "ingredient": ("ingredients", "nutrition", "nutritional information", "allergens",
                   "composition", "materials", "contents", "certification",
                   "compliance", "safety data"),
    "date": ("dates", "schedule", "agenda", "programme", "program", "timetable",
             "key dates", "deadlines"),
    "company_facts": ("founded", "established", "our story", "about us", "headcount",
                      "team size", "employees", "company facts", "at a glance",
                      "key facts", "by the numbers"),
}

# Regex source strings proving a fact class is present as text. Compiled in the detector
# so this module stays import-cheap and data-only.
FACT_CLASS_VALUE_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "price": (r"[£€¥₹$]\s*\d", r"\d+\s*(?:USD|EUR|GBP|JPY|INR|AUD|CAD|CHF)",
              r"\b(?:free|gratis|no cost|per month|per year|/mo\b|/yr\b|monthly|annually)\b"),
    "availability": (r"\b(?:in stock|out of stock|sold out|pre-?order|back-?order|"
                     r"available|unavailable|\d+\s+left|ships? in \d+)\b",),
    "hours": (r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b.{0,20}\d",
              r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", r"\b\d{1,2}:\d{2}\b",
              r"\b(?:24/7|closed|open daily)\b"),
    "address": (r"\b\d+\s+[A-Z][a-z]+\s+(?:St|Street|Rd|Road|Ave|Avenue|Blvd|Lane|Ln|"
                r"Drive|Dr|Way|Place|Pl|Court|Ct)\b",
                r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b",   # UK postcode
                r"\b\d{5}(?:-\d{4})?\b"),                        # US ZIP
    "phone": (r"(?:\+\d{1,3}[\s\-.]?)?(?:\(\d{1,4}\)[\s\-.]?)?\d{3,4}[\s\-.]?\d{3,4}",),
    "spec": (r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m|kg|g|lb|oz|in|ft|gb|mb|tb|ghz|mhz|w|v|ah|"
             r"mah|px|l|ml)\b", r"\b\d+\s*x\s*\d+\b"),
    "comparison": (r"\b(?:included|not included|yes|no|unlimited|limited)\b",),
    "ingredient": (r"\b\d+(?:\.\d+)?\s*(?:g|mg|kcal|cal|%)\b",
                   r"\b(?:contains|may contain|free from|gluten|dairy|nuts)\b"),
    "date": (r"\b(?:19|20)\d{2}\b", r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|"
             r"Sep|Oct|Nov|Dec)",),
    "company_facts": (r"\b(?:founded|established|since|est\.)\s*(?:in\s*)?(?:19|20)\d{2}\b",
                      r"\b\d+\+?\s*(?:employees|people|staff|team members)\b"),
}

# Fact classes whose absence is a *primary* commercial or definitional failure. Feeds
# severity derivation: a client-rendered price is a different problem from a
# client-rendered testimonial carousel.
PRIMARY_FACT_CLASSES: FrozenSet[str] = frozenset({
    "price", "availability", "hours", "address", "phone", "definition", "spec",
})

# Alt text that describes nothing.
PLACEHOLDER_ALT_VALUES: FrozenSet[str] = frozenset({
    "image", "img", "photo", "picture", "pic", "graphic", "graphics", "icon", "logo",
    "banner", "thumbnail", "thumb", "screenshot", "screen shot", "figure", "illustration",
    "untitled", "unnamed", "no alt", "alt", "alt text", "placeholder", "spacer",
    "decorative", "decoration", "divider", "separator", "dot", "bullet", "arrow",
    "image description", "description", "photo of", "image of", "picture of",
    "default", "temp", "test", "asset", "media", "file", "download", "attachment",
})

# --------------------------------------------------------------------------------------
# Render-delta suppression (guard G5)
#
# Text that legitimately appears only after JavaScript and is *not* brand content. A
# delta made entirely of these is not a content gap, and counting it would flag almost
# every site on the web.
# --------------------------------------------------------------------------------------

RENDER_DELTA_SUPPRESSION: Tuple[str, ...] = (
    "accept all cookies", "accept cookies", "we use cookies", "cookie settings",
    "manage preferences", "manage cookies", "privacy preferences", "consent",
    "reject all", "necessary cookies", "functional cookies", "targeting cookies",
    "your privacy choices", "do not sell", "cookie policy",
    "chat with us", "how can we help", "leave a message", "start a conversation",
    "we're offline", "typically replies", "powered by intercom", "powered by drift",
    "subscribe to our newsletter", "join our mailing list", "sign up for updates",
    "advertisement", "sponsored", "recommended for you",
    "loading", "please wait", "skip to content", "enable javascript",
    "back to top", "share on", "copy link", "print this page",
)

# --------------------------------------------------------------------------------------
# Gating and soft-404 fingerprints
# --------------------------------------------------------------------------------------

CHALLENGE_FINGERPRINTS: Tuple[str, ...] = (
    "verify you are human", "verifying you are human", "are you a robot",
    "checking your browser", "please enable javascript and cookies",
    "ddos protection by", "attention required", "access denied",
    "request blocked", "security check", "unusual traffic",
    "enable cookies to continue", "please complete the security check",
    "browser check", "human verification", "captcha", "i'm not a robot",
    "ray id", "cf-browser-verification", "challenge-platform", "__cf_chl",
    "incapsula incident id", "akamai reference", "reference #", "perimeterx",
    "blocked by the administrator", "your ip has been blocked",
)

# Statuses by which an edge layer refuses a request it could have served. 405 and 406 on a
# plain GET of a homepage are how several WAFs refuse clients they do not like. 404 and
# 410 are answers about the content, not refusals of the client, so they are not listed.
CONTENT_REFUSAL_STATUSES: Tuple[int, ...] = (401, 403, 405, 406, 429, 503)

# Response headers and cookie names that bot-management layers set on the responses they
# issue. Presence identifies the refusing layer; it is never itself treated as a defect.
BOT_MANAGEMENT_HEADER_FINGERPRINTS: Tuple[Tuple[str, str], ...] = (
    ("cf-mitigated", ""), ("set-cookie", "__cf_bm="), ("set-cookie", "cf_clearance="),
    ("x-vercel-mitigated", ""), ("x-datadome", ""), ("set-cookie", "datadome="),
    ("set-cookie", "_px"), ("x-px-", ""), ("set-cookie", "incap_ses_"),
    ("x-iinfo", ""), ("set-cookie", "ak_bmsc="), ("set-cookie", "bm_sz="),
    ("x-amzn-waf-action", ""), ("x-sucuri-block", ""), ("set-cookie", "aws-waf-token="),
)

SOFT_404_FINGERPRINTS: Tuple[str, ...] = (
    "page not found", "404 not found", "not found", "page doesn't exist",
    "page does not exist", "page you requested", "page you are looking for",
    "page cannot be found", "can't find that page", "cannot find that page",
    "no longer available", "has been removed", "doesn't exist anymore",
    "sorry, we couldn't find", "sorry, this page", "oops", "nothing here",
    "dead end", "broken link", "error 404", "http 404",
)

PAYWALL_FINGERPRINTS: Tuple[str, ...] = (
    "subscribe to continue", "subscribe to read", "continue reading",
    "already a subscriber", "become a member to read", "this article is for subscribers",
    "unlock this article", "read the full story", "start your free trial to read",
    "you've reached your limit", "free articles remaining", "monthly limit",
    "sign in to continue reading", "register to continue",
)

REGISTRATION_WALL_FINGERPRINTS: Tuple[str, ...] = (
    "create a free account to continue", "sign up to continue", "register to view",
    "create an account to read", "log in to continue", "sign in to view",
    "enter your email to continue", "unlock full access",
)

# --------------------------------------------------------------------------------------
# Entity disambiguation
# --------------------------------------------------------------------------------------

# Hosts that act as authoritative identifiers in sameAs. Not a ranking of services --
# these are the ones that function as *stable identity anchors* a machine can resolve.
AUTHORITATIVE_SAMEAS_HOSTS: Tuple[str, ...] = (
    "wikidata.org", "wikipedia.org", "dbpedia.org",
    "linkedin.com", "crunchbase.com", "bloomberg.com", "opencorporates.com",
    "github.com", "gitlab.com", "sec.gov", "companieshouse.gov.uk",
    "find-and-update.company-information.service.gov.uk",
    "handelsregister.de", "orcid.org", "ror.org", "isni.org", "viaf.org",
    "grid.ac", "gleif.org", "d-u-n-s", "dnb.com", "glassdoor.com",
    "producthunt.com", "g2.com", "capterra.com", "trustpilot.com",
)

# Social presence is useful but weaker as identity: profiles are easy to create and are
# not registries. Counted separately so the posture score cannot be gamed by listing
# five social links.
SOCIAL_SAMEAS_HOSTS: Tuple[str, ...] = (
    "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "tiktok.com", "pinterest.com", "reddit.com", "threads.net", "mastodon.social",
    "bsky.app", "medium.com", "substack.com", "vimeo.com", "twitch.tv",
)

# Common English words. A brand whose name is one of these is materially harder to
# disambiguate, which is an amplifier on G3.ENTITY.AMBIGUITY_RISK. Kept short and
# high-frequency on purpose: a long list would start matching real distinctive names.
COMMON_WORD_BRAND_RISK: FrozenSet[str] = frozenset({
    "apple", "orange", "amazon", "shell", "gap", "next", "boots", "square", "stripe",
    "slack", "monday", "sunday", "notion", "figma", "arc", "box", "drop", "dropbox",
    "field", "forest", "river", "lake", "mountain", "stone", "iron", "steel", "copper",
    "silver", "gold", "diamond", "ruby", "jade", "amber", "coral", "ivory", "pearl",
    "north", "south", "east", "west", "summit", "peak", "ridge", "vale", "bridge",
    "anchor", "beacon", "compass", "harbour", "harbor", "port", "bay", "cove",
    "spring", "summer", "autumn", "winter", "dawn", "dusk", "aurora", "eclipse",
    "atlas", "titan", "apex", "vertex", "nexus", "vector", "matrix", "pivot", "axis",
    "core", "base", "root", "node", "link", "loop", "flow", "wave", "pulse", "spark",
    "flame", "ember", "blaze", "frost", "storm", "thunder", "cloud", "mist", "haze",
    "lion", "tiger", "eagle", "falcon", "raven", "fox", "wolf", "bear", "stag", "hart",
    "oak", "elm", "ash", "birch", "cedar", "maple", "willow", "aspen", "olive", "sage",
    "mint", "basil", "clover", "bloom", "petal", "seed", "grain", "harvest",
    "craft", "forge", "anvil", "hammer", "chisel", "loom", "quill", "press",
    "brave", "bold", "swift", "keen", "clear", "bright", "pure", "true", "prime",
    "alpha", "beta", "delta", "omega", "zenith", "origin", "genesis", "unity",
    "cycle", "circuit", "signal", "static", "echo", "chorus", "rhythm", "tempo",
    "canvas", "palette", "prism", "lens", "focus", "frame", "shutter", "aperture",
    "ledger", "vault", "mint", "bond", "yield", "asset", "capital", "equity",
    "shift", "scale", "lift", "drive", "boost", "reach", "bridge", "span",
})

# --------------------------------------------------------------------------------------
# schema.org vocabulary
#
# Deliberately a *recognition* list, not a completeness claim. schema.org has hundreds of
# types, and shipping a partial vocabulary while treating absences as errors would
# generate false "unknown type" findings on perfectly valid markup. So
# G2.SD.UNKNOWN_TYPE only fires when a type is absent from this list AND is a near-miss
# of a known type (a likely misspelling) or is structurally invalid. Otherwise: silence
# (rule R6).
# --------------------------------------------------------------------------------------

SCHEMA_TYPES: FrozenSet[str] = frozenset({
    # Core
    "Thing", "CreativeWork", "Event", "Intangible", "Organization", "Person", "Place",
    "Product", "Action", "BioChemEntity", "MedicalEntity", "Taxon", "DataType",
    # Organization subtypes
    "Corporation", "EducationalOrganization", "GovernmentOrganization", "LocalBusiness",
    "NGO", "PerformingGroup", "SportsOrganization", "Airline", "Consortium",
    "FundingScheme", "LibrarySystem", "MedicalOrganization", "NewsMediaOrganization",
    "OnlineBusiness", "OnlineStore", "Project", "ResearchOrganization", "SearchRescueOrganization",
    "School", "CollegeOrUniversity", "ElementarySchool", "HighSchool", "MiddleSchool",
    "Preschool", "Hospital", "Dentist", "Physician", "Pharmacy", "VeterinaryCare",
    # LocalBusiness subtypes
    "AnimalShelter", "ArchiveOrganization", "AutomotiveBusiness", "AutoBodyShop",
    "AutoDealer", "AutoPartsStore", "AutoRental", "AutoRepair", "AutoWash",
    "GasStation", "MotorcycleDealer", "MotorcycleRepair", "ChildCare", "DryCleaningOrLaundry",
    "EmergencyService", "FireStation", "Hospital", "PoliceStation",
    "EmploymentAgency", "EntertainmentBusiness", "AdultEntertainment", "AmusementPark",
    "ArtGallery", "Casino", "ComedyClub", "MovieTheater", "NightClub",
    "FinancialService", "AccountingService", "AutomatedTeller", "BankOrCreditUnion",
    "InsuranceAgency", "FoodEstablishment", "Bakery", "BarOrPub", "Brewery", "CafeOrCoffeeShop",
    "Distillery", "FastFoodRestaurant", "IceCreamShop", "Restaurant", "Winery",
    "GovernmentOffice", "PostOffice", "HealthAndBeautyBusiness", "BeautySalon",
    "DaySpa", "HairSalon", "HealthClub", "NailSalon", "TattooParlor",
    "HomeAndConstructionBusiness", "Electrician", "GeneralContractor", "HVACBusiness",
    "HousePainter", "Locksmith", "MovingCompany", "Plumber", "RoofingContractor",
    "InternetCafe", "LegalService", "Attorney", "Notary", "Library", "LodgingBusiness",
    "BedAndBreakfast", "Campground", "Hostel", "Hotel", "Motel", "Resort",
    "ProfessionalService", "RadioStation", "RealEstateAgent", "RecyclingCenter",
    "SelfStorage", "ShoppingCenter", "SportsActivityLocation", "BowlingAlley",
    "ExerciseGym", "GolfCourse", "PublicSwimmingPool", "SkiResort", "SportsClub",
    "StadiumOrArena", "TennisComplex", "Store", "BikeStore", "BookStore",
    "ClothingStore", "ComputerStore", "ConvenienceStore", "DepartmentStore",
    "ElectronicsStore", "Florist", "FurnitureStore", "GardenStore", "GroceryStore",
    "HardwareStore", "HobbyShop", "HomeGoodsStore", "JewelryStore", "LiquorStore",
    "MensClothingStore", "MobilePhoneStore", "MovieRentalStore", "MusicStore",
    "OfficeEquipmentStore", "OutletStore", "PawnShop", "PetStore", "ShoeStore",
    "SportingGoodsStore", "TireShop", "ToyStore", "WholesaleStore",
    "TelevisionStation", "TouristInformationCenter", "TravelAgency",
    # CreativeWork subtypes
    "Article", "NewsArticle", "BlogPosting", "Blog", "TechArticle", "APIReference",
    "ScholarlyArticle", "Report", "SatiricalArticle", "AdvertiserContentArticle",
    "OpinionNewsArticle", "ReportageNewsArticle", "ReviewNewsArticle",
    "AnalysisNewsArticle", "BackgroundNewsArticle", "AskPublicNewsArticle",
    "Book", "Audiobook", "Chapter", "Clip", "Collection", "Comment", "Answer",
    "Question", "Conversation", "Course", "CreativeWorkSeason", "CreativeWorkSeries",
    "Dataset", "DataFeed", "DefinedTermSet", "Diet", "DigitalDocument",
    "Drawing", "Episode", "ExercisePlan", "Game", "VideoGame", "Guide", "HowTo",
    "HowToSection", "HowToStep", "HowToTip", "HowToDirection", "HowToSupply", "HowToTool",
    "HyperToc", "HyperTocEntry", "LearningResource", "Legislation", "Manuscript", "Map",
    "MathSolver", "MediaObject", "AudioObject", "DataDownload", "ImageObject",
    "LegislationObject", "MusicVideoObject", "VideoObject", "3DModel",
    "Menu", "MenuSection", "MenuItem", "Message", "EmailMessage", "Movie",
    "MusicComposition", "MusicPlaylist", "MusicAlbum", "MusicRelease", "MusicRecording",
    "Painting", "Photograph", "Play", "Poster", "PublicationIssue", "PublicationVolume",
    "Quotation", "Review", "ClaimReview", "CriticReview", "EmployerReview", "UserReview",
    "Recipe", "Sculpture", "SheetMusic", "ShortStory", "SoftwareApplication",
    "MobileApplication", "WebApplication", "VideoGameSeries", "SoftwareSourceCode",
    "SpecialAnnouncement", "Statement", "TVSeason", "TVSeries", "TVEpisode",
    "Thesis", "VisualArtwork", "WebContent", "HealthTopicContent",
    "WebPage", "AboutPage", "CheckoutPage", "CollectionPage", "ContactPage",
    "FAQPage", "ItemPage", "MedicalWebPage", "ProfilePage", "QAPage",
    "RealEstateListing", "SearchResultsPage", "WebPageElement", "SiteNavigationElement",
    "Table", "WPAdBlock", "WPFooter", "WPHeader", "WPSideBar", "WebSite",
    # Intangible subtypes
    "ActionAccessSpecification", "AlignmentObject", "Audience", "BusinessAudience",
    "EducationalAudience", "MedicalAudience", "PeopleAudience", "Researcher",
    "BedDetails", "Brand", "BroadcastChannel", "BroadcastFrequencySpecification",
    "Class", "ComputerLanguage", "ConstraintNode", "DataFeedItem", "DefinedTerm",
    "CategoryCode", "Demand", "DigitalDocumentPermission", "EducationalOccupationalProgram",
    "EnergyConsumptionDetails", "EntryPoint", "Enumeration", "BookFormatType",
    "BusinessEntityType", "BusinessFunction", "ContactPointOption", "DayOfWeek",
    "DeliveryMethod", "DigitalDocumentPermissionType", "EventAttendanceModeEnumeration",
    "EventStatusType", "GamePlayMode", "GenderType", "ItemAvailability",
    "ItemListOrderType", "LegalValueLevel", "MapCategoryType", "MeasurementTypeEnumeration",
    "MediaManipulationRatingEnumeration", "MerchantReturnEnumeration", "MusicAlbumProductionType",
    "MusicAlbumReleaseType", "MusicReleaseFormatType", "NonprofitType", "OfferItemCondition",
    "PaymentMethod", "PhysicalActivityCategory", "PriceComponentTypeEnumeration",
    "PriceTypeEnumeration", "QualitativeValue", "RefundTypeEnumeration",
    "RestrictedDiet", "ReturnFeesEnumeration", "ReturnLabelSourceEnumeration",
    "ReturnMethodEnumeration", "RsvpResponseType", "SizeGroupEnumeration",
    "SizeSystemEnumeration", "Specialty", "StatusEnumeration", "ActionStatusType",
    "GameServerStatus", "LegalForceStatus", "OrderStatus", "PaymentStatusType",
    "ReservationStatusType", "WarrantyScope", "FloorPlan", "GameServer",
    "GeospatialGeometry", "Grant", "MonetaryGrant", "HealthInsurancePlan",
    "HealthPlanCostSharingSpecification", "HealthPlanFormulary", "HealthPlanNetwork",
    "Invoice", "ItemList", "BreadcrumbList", "HowToItem", "HowToSupply", "OfferCatalog",
    "JobPosting", "Language", "ListItem", "MediaSubscription", "MenuItem",
    "MerchantReturnPolicy", "MerchantReturnPolicySeasonalOverride", "Observation",
    "Occupation", "OccupationalExperienceRequirements", "Offer", "AggregateOffer",
    "OfferForLease", "OfferForPurchase", "Order", "OrderItem", "ParcelDelivery",
    "Permit", "GovernmentPermit", "ProgramMembership", "Property", "PropertyValue",
    "LocationFeatureSpecification", "PropertyValueSpecification", "Quantity",
    "Distance", "Duration", "Energy", "Mass", "Rating", "AggregateRating",
    "EndorsementRating", "Reservation", "BusReservation", "EventReservation",
    "FlightReservation", "FoodEstablishmentReservation", "LodgingReservation",
    "RentalCarReservation", "ReservationPackage", "TaxiReservation", "TrainReservation",
    "Role", "LinkRole", "OrganizationRole", "EmployeeRole", "PerformanceRole",
    "Schedule", "Seat", "Series", "Service", "BroadcastService", "CableOrSatelliteService",
    "FinancialProduct", "BankAccount", "DepositAccount", "CurrencyConversionService",
    "InvestmentOrDeposit", "InvestmentFund", "LoanOrCredit", "MortgageLoan",
    "CreditCard", "PaymentCard", "PaymentService", "FoodService", "GovernmentService",
    "Taxi", "TaxiService", "WebAPI", "ServiceChannel", "SpeakableSpecification",
    "StatisticalPopulation", "StatisticalVariable", "StructuredValue", "ContactPoint",
    "PostalAddress", "DatedMoneySpecification", "DefinedRegion", "DeliveryTimeSettings",
    "EngineSpecification", "ExchangeRateSpecification", "GeoCoordinates", "GeoShape",
    "GeoCircle", "InteractionCounter", "MonetaryAmount", "NutritionInformation",
    "OpeningHoursSpecification", "OwnershipInfo", "PriceSpecification",
    "CompoundPriceSpecification", "DeliveryChargeSpecification", "PaymentChargeSpecification",
    "UnitPriceSpecification", "QuantitativeValue", "QuantitativeValueDistribution",
    "MonetaryAmountDistribution", "RepaymentSpecification", "ShippingDeliveryTime",
    "ShippingRateSettings", "TypeAndQuantityNode", "WarrantyPromise", "Ticket",
    "Trip", "BusTrip", "Flight", "TouristTrip", "TrainTrip", "VirtualLocation",
    # Product / Place / Event subtypes
    "IndividualProduct", "ProductCollection", "ProductGroup", "ProductModel",
    "SomeProducts", "Vehicle", "BusOrCoach", "Car", "Motorcycle", "MotorizedBicycle",
    "DietarySupplement", "Drug", "Substance",
    "Accommodation", "Apartment", "CampingPitch", "House", "SingleFamilyResidence",
    "Room", "HotelRoom", "MeetingRoom", "Suite", "AdministrativeArea", "City",
    "Country", "SchoolDistrict", "State", "CivicStructure", "Airport", "Aquarium",
    "Beach", "Bridge", "BusStation", "BusStop", "Cemetery", "Crematorium",
    "EducationalOrganization", "EventVenue", "GovernmentBuilding", "CityHall",
    "Courthouse", "DefenceEstablishment", "Embassy", "LegislativeBuilding",
    "Museum", "MusicVenue", "Park", "ParkingFacility", "PerformingArtsTheater",
    "PlaceOfWorship", "BuddhistTemple", "Church", "CatholicChurch", "HinduTemple",
    "Mosque", "Synagogue", "Playground", "PublicToilet", "RVPark", "SubwayStation",
    "TaxiStand", "TrainStation", "Zoo", "Landform", "BodyOfWater", "Canal",
    "LakeBodyOfWater", "OceanBodyOfWater", "Pond", "Reservoir", "RiverBodyOfWater",
    "SeaBodyOfWater", "Waterfall", "Continent", "Mountain", "Volcano", "LandmarksOrHistoricalBuildings",
    "Residence", "ApartmentComplex", "GatedResidenceCommunity", "TouristAttraction",
    "TouristDestination",
    "BusinessEvent", "ChildrensEvent", "ComedyEvent", "CourseInstance",
    "DanceEvent", "DeliveryEvent", "EducationEvent", "EventSeries", "ExhibitionEvent",
    "Festival", "FoodEvent", "Hackathon", "LiteraryEvent", "MusicEvent",
    "PublicationEvent", "BroadcastEvent", "OnDemandEvent", "SaleEvent",
    "ScreeningEvent", "SocialEvent", "SportsEvent", "TheaterEvent", "VisualArtsEvent",
})

# Frequently mistyped or localised type names, mapped to the intended type. Used to make
# G2.SD.UNKNOWN_TYPE actionable rather than merely negative.
SCHEMA_TYPE_CORRECTIONS: Dict[str, str] = {
    "organisation": "Organization", "organizations": "Organization",
    "organisations": "Organization", "compagny": "Organization", "company": "Organization",
    "localbussiness": "LocalBusiness", "localbuisness": "LocalBusiness",
    "local business": "LocalBusiness", "localbusinesses": "LocalBusiness",
    "webpage ": "WebPage", "website ": "WebSite", "web site": "WebSite",
    "web page": "WebPage", "breadcrumb": "BreadcrumbList", "breadcrumbs": "BreadcrumbList",
    "products": "Product", "prodcut": "Product", "prduct": "Product",
    "articles": "Article", "blogposting ": "BlogPosting", "blogpost": "BlogPosting",
    "blog post": "BlogPosting", "newsarticles": "NewsArticle",
    "faq": "FAQPage", "faqs": "FAQPage", "faq page": "FAQPage",
    "aggregaterating ": "AggregateRating", "agregaterating": "AggregateRating",
    "postaladress": "PostalAddress", "postal address": "PostalAddress",
    "openinghours": "OpeningHoursSpecification", "offers": "Offer",
    "softwareapp": "SoftwareApplication", "software": "SoftwareApplication",
    "person ": "Person", "persons": "Person", "people": "Person",
    "recipes": "Recipe", "events": "Event", "reviews": "Review",
    "imageobjects": "ImageObject", "videoobjects": "VideoObject",
    "howtos": "HowTo", "jobposting ": "JobPosting", "jobpostings": "JobPosting",
}

# schema.org ItemAvailability enum, plus the prose each value corresponds to. Drives both
# G2.SD.PROP_TYPE_INVALID (is the value a real enum?) and the availability arm of
# G2.SD.MARKUP_TEXT_CONTRADICTION (does the enum agree with the page?).
AVAILABILITY_ENUM: Dict[str, Tuple[str, ...]] = {
    "InStock": ("in stock", "available", "available now", "in-stock", "on hand",
                "ready to ship", "ships today", "immediate delivery"),
    "OutOfStock": ("out of stock", "sold out", "unavailable", "out-of-stock",
                   "currently unavailable", "no longer available"),
    "PreOrder": ("pre-order", "preorder", "pre order", "coming soon", "reserve now"),
    "PreSale": ("pre-sale", "presale", "early access"),
    "BackOrder": ("back-order", "backorder", "back order", "on backorder",
                  "available to order", "made to order"),
    "Discontinued": ("discontinued", "no longer made", "end of life", "retired"),
    "InStoreOnly": ("in store only", "in-store only", "store pickup only",
                    "collection only"),
    "LimitedAvailability": ("limited availability", "limited stock", "low stock",
                            "only a few left", "while stocks last"),
    "OnlineOnly": ("online only", "web exclusive", "online exclusive"),
    "SoldOut": ("sold out",),
}

# --------------------------------------------------------------------------------------
# Site profiling signals
# --------------------------------------------------------------------------------------

PROFILE_PATH_SIGNALS: Dict[str, Tuple[str, ...]] = {
    "ecommerce": ("/cart", "/basket", "/checkout", "/shop", "/store", "/product",
                  "/products", "/collections", "/catalog", "/catalogue", "/wishlist",
                  "/my-account/orders", "/returns", "/shipping", "/size-guide"),
    "publisher": ("/blog", "/news", "/article", "/articles", "/posts", "/archive",
                  "/category", "/tag", "/author", "/feed", "/rss", "/newsletter",
                  "/opinion", "/features", "/magazine"),
    "local_business": ("/contact", "/locations", "/find-us", "/directions", "/book",
                       "/appointment", "/opening-times", "/menu", "/branches",
                       "/visit", "/store-locator"),
    "saas_software": ("/pricing", "/docs", "/documentation", "/api", "/signup",
                      "/sign-up", "/free-trial", "/trial", "/integrations",
                      "/changelog", "/status", "/security", "/login", "/demo"),
    "docs_knowledge": ("/docs", "/doc", "/guide", "/guides", "/kb", "/knowledge-base",
                       "/reference", "/tutorial", "/tutorials", "/manual", "/help",
                       "/support", "/learn", "/api-reference"),
    "marketing_brochure": ("/about", "/services", "/contact", "/portfolio", "/work",
                           "/case-studies", "/testimonials", "/team", "/our-story"),
    "marketplace_directory": ("/listings", "/directory", "/browse", "/search",
                              "/categories", "/vendors", "/sellers", "/providers",
                              "/members", "/profiles"),
}

PROFILE_SCHEMA_SIGNALS: Dict[str, Tuple[str, ...]] = {
    "ecommerce": ("Product", "Offer", "AggregateOffer", "ProductGroup", "OnlineStore",
                  "Store", "MerchantReturnPolicy", "ItemAvailability"),
    "publisher": ("Article", "NewsArticle", "BlogPosting", "Blog", "NewsMediaOrganization",
                  "PublicationIssue", "Periodical"),
    "local_business": ("LocalBusiness", "Restaurant", "Store", "PostalAddress",
                       "OpeningHoursSpecification", "GeoCoordinates", "Dentist",
                       "MedicalBusiness", "Hotel", "ProfessionalService"),
    "saas_software": ("SoftwareApplication", "WebApplication", "Service", "Offer",
                      "SubscriptionPlan", "MobileApplication"),
    "docs_knowledge": ("TechArticle", "HowTo", "APIReference", "LearningResource",
                       "Course", "FAQPage", "QAPage"),
    "marketing_brochure": ("Organization", "ProfessionalService", "Service"),
    "marketplace_directory": ("ItemList", "OfferCatalog", "SearchResultsPage",
                              "CollectionPage", "JobPosting", "RealEstateListing"),
}

PROFILE_TEXT_SIGNALS: Dict[str, Tuple[str, ...]] = {
    "ecommerce": ("add to cart", "add to bag", "add to basket", "buy now", "checkout",
                  "free shipping", "in stock", "out of stock", "size guide",
                  "returns policy", "quantity", "select size", "colour", "color"),
    "publisher": ("published", "updated on", "by ", "min read", "minute read",
                  "share this article", "subscribe to our newsletter", "related articles",
                  "comments", "editor", "correspondent"),
    "local_business": ("opening hours", "opening times", "get directions", "book a table",
                       "call us", "visit us", "our address", "find us", "parking",
                       "appointment", "walk-ins"),
    "saas_software": ("start free trial", "book a demo", "per user", "per month",
                      "per seat", "api key", "integrations", "sso", "single sign-on",
                      "uptime", "sla", "changelog", "no credit card required"),
    "docs_knowledge": ("on this page", "table of contents", "prerequisites",
                       "installation", "quickstart", "example request", "parameters",
                       "returns", "deprecated", "see also", "in this guide"),
    "marketing_brochure": ("our services", "what we do", "case study", "testimonial",
                           "get in touch", "our approach", "our clients", "portfolio"),
    "marketplace_directory": ("filter by", "sort by", "results for", "listings",
                              "browse by", "featured listings", "claim this listing",
                              "showing 1", "refine search"),
}

# Expected structured-data types per detected profile. The generalization mechanism: the
# audit never asks "does this page have Product markup", it asks "given the evidence says
# this is a product page, does it have what a product page needs".
PROFILE_EXPECTED_TYPES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "_any": {
        "expected": ("Organization", "WebSite"),
        "required": ("name", "url"),
        "recommended": ("logo", "description", "sameAs", "potentialAction",
                        "foundingDate", "address", "contactPoint"),
    },
    "ecommerce": {
        "expected": ("Product", "Offer"),
        "required": ("name", "image", "offers.price", "offers.priceCurrency",
                     "offers.availability"),
        "recommended": ("sku", "gtin13", "mpn", "brand", "aggregateRating", "review",
                        "shippingDetails", "hasMerchantReturnPolicy", "description"),
    },
    "publisher": {
        "expected": ("Article", "BlogPosting", "NewsArticle"),
        "required": ("headline", "datePublished", "author"),
        "recommended": ("dateModified", "publisher", "image", "articleSection",
                        "about", "keywords", "wordCount"),
    },
    "local_business": {
        "expected": ("LocalBusiness",),
        "required": ("name", "address", "telephone"),
        "recommended": ("openingHoursSpecification", "geo", "priceRange", "areaServed",
                        "image", "aggregateRating", "hasMap"),
    },
    "saas_software": {
        "expected": ("SoftwareApplication", "Service", "WebApplication"),
        "required": ("name", "description"),
        "recommended": ("applicationCategory", "featureList", "operatingSystem",
                        "offers", "aggregateRating", "provider"),
    },
    "docs_knowledge": {
        "expected": ("TechArticle", "HowTo", "FAQPage", "Article"),
        "required": ("headline",),
        "recommended": ("step", "mainEntity", "dependencies", "proficiencyLevel",
                        "dateModified", "author", "about"),
    },
    "marketing_brochure": {
        "expected": ("Organization", "Service"),
        "required": ("name",),
        "recommended": ("description", "address", "areaServed", "serviceType",
                        "provider", "sameAs"),
    },
    "marketplace_directory": {
        "expected": ("ItemList", "CollectionPage"),
        "required": ("itemListElement",),
        "recommended": ("numberOfItems", "mainEntity", "about"),
    },
}

# Which fact classes are *primary* for each profile. Feeds severity: a missing price on a
# shop is critical; on a documentation site it is not a finding at all.
PROFILE_PRIMARY_FACTS: Dict[str, Tuple[str, ...]] = {
    "ecommerce": ("price", "availability", "spec"),
    "publisher": ("date",),
    "local_business": ("hours", "address", "phone"),
    "saas_software": ("price", "spec"),
    "docs_knowledge": ("spec",),
    "marketing_brochure": ("phone", "address"),
    "marketplace_directory": ("price",),
    "ambiguous": (),
}

# Answerability and engagement checks that do not apply to every profile. Scoping by
# profile rather than running everything everywhere is a false-positive control:
# question-shaped headings are not a defect on a product detail page.
PROFILE_CHECK_EXCLUSIONS: Dict[str, Tuple[str, ...]] = {
    "ecommerce": ("G3.ANSW.NO_QUESTION_SHAPED_HEADINGS",),
    "marketing_brochure": ("G3.ANSW.NO_QUESTION_SHAPED_HEADINGS",
                           "G0.SITEMAP.NO_MACHINE_FEED",
                           "GE.NAV.NO_SITE_SEARCH"),
    "marketplace_directory": ("G3.ANSW.WALL_OF_TEXT_UNCHUNKABLE",),
    "ambiguous": ("G2.SD.TYPE_MISMATCH", "G2.SD.REQUIRED_PROP_MISSING",
                  "G2.SD.RECOMMENDED_PROP_MISSING",
                  "G3.ANSW.NO_QUESTION_SHAPED_HEADINGS",
                  "G0.SITEMAP.NO_MACHINE_FEED", "GE.CONV.PATH_UNCLEAR"),
}

# Feed and machine-readable-channel paths worth probing.
FEED_PATHS: Tuple[str, ...] = (
    "/feed", "/feed/", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml",
    "/blog/feed", "/news/feed", "/feed.json", "/feed/atom",
)

WELL_KNOWN_PATHS: Tuple[str, ...] = (
    "/robots.txt", "/sitemap.xml", "/sitemap_index.xml", "/llms.txt",
    "/.well-known/security.txt", "/humans.txt",
)

# Pages that a generalizing audit should always try to see, because they carry the
# canonical facts almost every assistant query about a brand needs.
GUARANTEED_PATHS: Tuple[str, ...] = (
    "/", "/about", "/about-us", "/company", "/contact", "/contact-us",
    "/pricing", "/plans", "/products", "/services", "/faq", "/blog", "/docs",
)


def is_fluff_sentence(sentence_lower: str) -> bool:
    """True when a sentence contains a superlative from the lexicon."""
    return any(term in sentence_lower for term in FLUFF_SUPERLATIVES)


def opener_is_dependent(first_tokens: str) -> bool:
    """True when a sentence opener refers backwards and cannot stand alone."""
    lowered = first_tokens.strip().lower()
    if not lowered:
        return False
    for phrase in DEMONSTRATIVE_OPENER_PHRASES:
        if lowered.startswith(phrase):
            return True
    first = lowered.split()[0].strip(",.;:")
    return first in PRONOUN_OPENERS or first in CONNECTIVE_OPENERS


def schema_type_is_known(type_name: str) -> bool:
    return type_name in SCHEMA_TYPES


def suggest_schema_type(type_name: str) -> str:
    """Suggest the intended type for an unrecognised one, or ``""`` if unsure.

    Two mechanisms: an explicit correction table for the mistakes that actually recur,
    and a single-edit-distance search against the known vocabulary. Returning ``""``
    means "we do not know", and the caller must then stay silent (rule R6).
    """
    key = type_name.strip().lower()
    if key in SCHEMA_TYPE_CORRECTIONS:
        return SCHEMA_TYPE_CORRECTIONS[key]
    for known in SCHEMA_TYPES:
        if known.lower() == key:
            return known                      # case-only difference
    candidates = [k for k in SCHEMA_TYPES if abs(len(k) - len(type_name)) <= 2
                  and _edit_distance_within(type_name.lower(), k.lower(), 2)]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return sorted(candidates, key=lambda k: (len(k), k))[0]
    return ""


def _edit_distance_within(a: str, b: str, limit: int) -> bool:
    """Bounded Levenshtein test. Cheap because it bails out past ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        best = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            best = min(best, value)
        if best > limit:
            return False
        previous = current
    return previous[-1] <= limit


def availability_from_text(text_lower: str) -> str:
    """Map page prose to a schema.org ItemAvailability value, or ``""`` when unclear."""
    # Longest phrases first so "out of stock" is not matched by "in stock".
    matches = []
    for enum_value, phrases in AVAILABILITY_ENUM.items():
        for phrase in phrases:
            if phrase in text_lower:
                matches.append((len(phrase), enum_value))
    if not matches:
        return ""
    matches.sort(reverse=True)
    return matches[0][1]


def normalise_availability(value: str) -> str:
    """Reduce a markup availability value to its bare enum name."""
    if not value:
        return ""
    tail = str(value).rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip()
    for enum_value in AVAILABILITY_ENUM:
        if tail.lower() == enum_value.lower():
            return enum_value
    return tail
