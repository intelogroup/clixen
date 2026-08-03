"""
Query completeness guard — catches underspecified or ambiguous queries before dispatch.

Returns a clarifying question string if the query is missing context that would
make the response ambiguous or irrelevant (team name, year, delivery service, etc.).

Single entrypoint: check_all(query, has_history=False) -> str | None

No model call. Zero latency. Fires before classify() / LLM dispatch.
"""

import re

# ── Named entity presence heuristics ──────────────────────────────────────────
# A word that is TitleCase and not the first word of the query (or an all-caps ticker)
# is a reasonable signal that a named entity is present.

_HAS_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_HAS_INTERIOR_PROPER = re.compile(r"(?<!\A)(?<!\.\s)(?<=\s)[A-Z][a-z]{2,}")
_HAS_CAPS_TICKER = re.compile(r"\b[A-Z]{2,5}\b")  # e.g. AAPL, BTC, S&P


def _has_entity(query: str) -> bool:
    """True if the query appears to contain a named entity or year."""
    return bool(
        _HAS_YEAR.search(query)
        or _HAS_INTERIOR_PROPER.search(query)
        or _HAS_CAPS_TICKER.search(query)
    )


# ── Deictic reference patterns ────────────────────────────────────────────────
# Catches "explain this process", "tell me about it", "how does that work" etc.
# when the model has no prior context to resolve the referent.

_DEICTIC_REF = re.compile(
    r"\b(this|that|these|those|it)\s+\w+"
    r"|\b(explain|describe|tell me about|how does|what is|summarize|clarify|understand)\s+(it|this|that)\b"
    r"|\b(this|that|it|these|those)\s*[?.!]?\s*\Z",
    re.I,
)

_DEICTIC_SELF_CONTAINED = re.compile(
    r"\b(this year|this month|this week|this morning|this evening|this afternoon|"
    r"this time|these days|that said|that is|that means|that way|that much|"
    r"this is|that's|it is|it's|it seems|it looks|it depends|"
    # Dev-environment self-reference — "this repo/codebase/file" names the current
    # working project, not a prior conversation turn (found 2026-07-02: "show me the
    # recent git log for this repo" fired the guard cold with no history).
    r"this (?:repo|repository|codebase|project|file|directory|folder|script|module|package))\b"
    # Relative clause: "a/the/an <noun> that <word>" — "that" has its antecedent in the
    # same sentence ("a function that reverses a string"), so it isn't a dangling deictic
    # reference at all (found 2026-07-02: this collided with code-request classification).
    r"|\b(?:a|an|the|any)\s+(?:\w+\s+){0,3}that\s+\w+"
    # Memory command: "remember/note/keep in mind that <clause>" — "that" introduces a
    # complement clause with its content right there in the sentence, not a dangling
    # deictic reference (found 2026-07-04: this was silently blocking the #1 natural
    # phrasing for the remember tool, e.g. "Remember that my favorite language is Rust").
    r"|\b(?:remember|recall|note|keep in mind)\s+that\b"
    # Expletive "it" — subject of a weather/time question, refers to nothing
    # (found 2026-07-10: "what time is it in Tokyo" / "is it going to rain"
    # false-positived as a dangling deictic reference needing clarification).
    r"|\b(?:what|which)\s+\w+\s+is\s+it\b"
    r"|\bis\s+it\s+(?:going to|about to|likely to|supposed to|raining|snowing|"
    r"sunny|cloudy|windy|hot|cold|warm|cool|humid)\b",
    re.I,
)

# A named/quoted token that can serve as a same-sentence antecedent for a
# pronoun: snake_case or dotted identifiers (resource/automation/file names),
# or a quoted string. If one appears BEFORE the pronoun match, the pronoun
# refers back to it within the same sentence and isn't a dangling reference
# (found 2026-07-11: "Delete ella_sheet_tracker, it was just a test..." and
# "hard-delete ella_urgent_watch, wipe it completely..." both false-positived
# — the first patched narrowly for the ", it was" shape, but "wipe it
# completely" showed the real gap is any pronoun after a same-sentence named
# antecedent, not just that one clause shape. General check replaces the
# narrower one.)
_HAS_NAMED_ANTECEDENT = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"   # snake_case identifier
    r"|\b[a-z0-9][a-z0-9-]*\.[a-z]{2,4}\b"       # dotted name / filename
    r"|\"[^\"]+\"|'[^']+'",                       # quoted string
    re.I,
)

_HAS_INLINE_CONTEXT = re.compile(
    r":\s*\S"            # colon followed by content  ("explain this: <error>")
    r"|[a-z]\.\s+[A-Z]", # two sentences (end of sentence + new sentence)
)

_LONG_QUERY_WORDS = 12


def _deictic_inner(query: str) -> str | None:
    """Check for deictic reference without resolved referent."""
    q = query.strip()

    m = _DEICTIC_REF.search(q)
    if not m:
        return None

    if _HAS_NAMED_ANTECEDENT.search(q, 0, m.start()):
        return None

    if _DEICTIC_SELF_CONTAINED.search(q):
        return None

    if _HAS_INLINE_CONTEXT.search(q):
        return None

    if len(q.split()) > _LONG_QUERY_WORDS:
        return None

    return (
        "What are you referring to? Could you give me more context or paste "
        "the relevant text you'd like me to explain?"
    )


# ── Sports / event ambiguity patterns ─────────────────────────────────────────

# 1. Bare pronoun without a referent
_BARE_PRONOUN = re.compile(
    r"\b(they|them|their|he|she|him|his|her)\s+(play|plays|played|will play|score|scored|win|won|lost|beat|beats|beat)\b",
    re.I,
)

# 2. "the team / the club / the player / the company" without naming it
_VAGUE_ENTITY = re.compile(
    r"\b(the\s+(team|club|player|squad|side|company|firm|stock|artist|band|group))\b",
    re.I,
)

# 3. "the game / the match / the race / the fight" without naming who's involved
_VAGUE_EVENT = re.compile(
    r"\b(the\s+(game|match|race|fight|bout|final|series|championship|tournament|election|vote|debate))\b",
    re.I,
)

# 4. Asking for score/result/price/standing without specifying what
_VAGUE_METRIC = re.compile(
    r"\bwhat.s\s+the\s+(score|result|price|rate|standing|ranking|record|stats?)\b",
    re.I,
)

# 5. "who won/lost/scored/leads" without a year or named event
_MISSING_YEAR = re.compile(
    r"\b(who\s+(won|lost|scored|leads?|topped?)|what\s+(team|country|player)\s+(won|lost|came\s+first))\b",
    re.I,
)
_HAS_TEMPORAL_QUALIFIER = re.compile(
    r"\b(last|latest|most recent|recent|current|this year.?s|previous|prior|just)\b",
    re.I,
)

# 6. Generic "when is the [sport/event]" without naming who
_WHEN_EVENT = re.compile(
    r"\bwhen\s+(is|was|will)\s+(the\s+)?(next\s+)?(game|match|race|fight|final|draft|deadline|election)\b",
    re.I,
)

# 7. Ambiguous named entities — bare short queries with no disambiguating context
_AMBIGUOUS_ENTITIES: dict[str, str] = {
    "apple":   "Apple (the tech company) or apple (the fruit)?",
    "mercury": "Mercury (the planet), mercury (the element), or Mercury (the car)?",
    "amazon":  "Amazon (the company), Amazon (the river), or Amazon (the rainforest)?",
    "python":  "Python (the programming language) or python (the snake)?",
    "jaguar":  "Jaguar (the car) or jaguar (the animal)?",
    "mars":    "Mars (the planet), Mars (the chocolate bar), or something else?",
    "shell":   "Shell (the oil company), a command-line shell, or a literal shell?",
    "oracle":  "Oracle (the database company) or oracle (the concept)?",
    "brave":   "Brave (the web browser) or brave (the adjective)?",
    "nest":    "Nest (Google's smart home product) or a bird's nest?",
}

_AMBIGUITY_WORD_LIMIT = 2

# Ambiguous toponyms — cities/places that exist in multiple well-known locations.
_AMBIGUOUS_TOPONYMS: list[tuple] = [
    (
        re.compile(r"\bsantiago\b", re.I),
        {"chile", "compostela", "cuba", "dominican", "spain", "galicia"},
        "Which Santiago? Santiago, Chile — Santiago de Compostela, Spain — or Santiago de Cuba?",
    ),
    (
        re.compile(r"\blima\b", re.I),
        {"peru", "ohio", "peruvian"},
        "Which Lima? Lima, Peru or Lima, Ohio?",
    ),
    (
        re.compile(r"\bflorence\b", re.I),
        {"italy", "italian", "alabama", "tuscany", "sc", "south carolina"},
        "Which Florence? Florence, Italy or Florence, Alabama (or another city)?",
    ),
    (
        re.compile(r"\bportland\b", re.I),
        {"oregon", "maine", "or\b", r"\bme\b"},
        "Which Portland? Portland, Oregon or Portland, Maine?",
    ),
    (
        re.compile(r"\bmemphis\b", re.I),
        {"tennessee", "egypt", "tn"},
        "Which Memphis? Memphis, Tennessee or Memphis, Egypt?",
    ),
    (
        re.compile(r"\bvienna\b", re.I),
        {"austria", "virginia", "austrian", "va\b"},
        "Which Vienna? Vienna, Austria or Vienna, Virginia?",
    ),
]

# 8. Two teams named but no competition/league context
_TEAM_MATCHUP = re.compile(
    r"\b(Arsenal|Chelsea|Man[chester]*\s*U[nited]*|Liverpool|Man[chester]*\s*City|"
    r"Barcelona|Real\s+Madrid|Bayern|PSG|Juventus|Tottenham|Brighton|Aston\s+Villa|"
    r"Newcastle|West\s+Ham|Fulham|Crystal\s+Palace|Wolves|Everton|Leicester|Leeds|"
    r"Rome|Lazio|Inter|Milan|Ajax|Benfica|Porto|Dortmund|RB\s+Leipzig)\b.*\b"
    r"(Arsenal|Chelsea|Man[chester]*\s*U[nited]*|Liverpool|Man[chester]*\s*City|"
    r"Barcelona|Real\s+Madrid|Bayern|PSG|Juventus|Tottenham|Brighton|Aston\s+Villa|"
    r"Newcastle|West\s+Ham|Fulham|Crystal\s+Palace|Wolves|Everton|Leicester|Leeds|"
    r"Rome|Lazio|Inter|Milan|Ajax|Benfica|Porto|Dortmund|RB\s+Leipzig)\b",
    re.I,
)

_COMPETITION_KEYWORDS = {
    "premier", "champions league", "cup", "super league", "league one", "league two",
    "serie a", "la liga", "bundesliga", "ligue 1", "eredivisie",
    "fa cup", "carabao", "efl", "europa", "conference",
    "women's", "men's", "youth", "reserve", "u21", "u23",
    "2024", "2025", "2026", "2027", "2028",
}


def _has_competition_context(query: str) -> bool:
    q_lower = query.lower()
    return any(keyword in q_lower for keyword in _COMPETITION_KEYWORDS)


# Leading question/filler phrases stripped before the word-count check, so a natural
# phrasing like "tell me about Mercury" reduces to the bare entity "mercury" and is
# still caught (the ≤2-word gate alone missed anything but a one-word query).
_ENTITY_LEADIN_RE = re.compile(
    r"^(?:tell me about|what(?:'s| is| are)|who(?:'s| is)|whats|about|explain|describe|define|tell me)\s+",
    re.I,
)


def _is_ambiguous_named_entity(query: str) -> str | None:
    cleaned = _ENTITY_LEADIN_RE.sub("", query.strip(), count=1)
    # Strip surrounding punctuation per token so "Mercury?" matches the key "mercury".
    words = [w.strip(".,!?;:'\"()[]") for w in cleaned.lower().split()]
    words = [w for w in words if w]
    if len(words) > _AMBIGUITY_WORD_LIMIT:
        return None
    for word in words:
        hint = _AMBIGUOUS_ENTITIES.get(word)
        if hint:
            return f"Could you clarify what you mean? {hint}"
    return None


def _ambiguous_toponym(query: str) -> str | None:
    q_lower = query.lower()
    for pattern, context_tokens, hint in _AMBIGUOUS_TOPONYMS:
        if pattern.search(query):
            if not any(tok in q_lower for tok in context_tokens):
                return hint
    return None


def _sports_ambiguity_inner(query: str) -> str | None:
    q = query.strip()

    # 1. Bare pronoun with no named entity
    if _BARE_PRONOUN.search(q) and not _has_entity(q):
        return "Which team or player are you asking about? Please give me the name."

    # 2. "the team / the player / the company" — unnamed
    if _VAGUE_ENTITY.search(q) and not _has_entity(q):
        return "Which team, player, or entity are you referring to? Please give me the name."

    # 3. "the game / the match / the final" — unnamed
    if _VAGUE_EVENT.search(q) and not _has_entity(q):
        return "Which match or event are you asking about? Please give me the team names or event name."

    # 4. "what's the score/price" — unnamed subject
    if _VAGUE_METRIC.search(q) and not _has_entity(q):
        return "Which team's score or which stock/asset are you asking about? Please be specific."

    # 5. "who won the championship" — year likely needed
    if (
        _MISSING_YEAR.search(q)
        and not _HAS_YEAR.search(q)
        and not _has_entity(q)
        and not _HAS_TEMPORAL_QUALIFIER.search(q)
        and len(q.split()) <= 8
    ):
        return "Which year or edition are you asking about?"

    # 6. "when is the game" — no team or event named
    if _WHEN_EVENT.search(q) and not _has_entity(q):
        return "Which team or event are you asking about? Please give me the name."

    # 7. Two teams named but no competition/league context
    if _TEAM_MATCHUP.search(q) and not _has_competition_context(q):
        return "Which match? Please specify: Premier League, Champions League, Cup, Women's, etc."

    # 8. Ambiguous toponym — multiple cities with same name
    toponym_hint = _ambiguous_toponym(q)
    if toponym_hint:
        return toponym_hint

    # 9. Ambiguous named entity (bare, short query)
    ambiguity = _is_ambiguous_named_entity(q)
    if ambiguity:
        return ambiguity

    return None


# ── Food/restaurant intent guard ───────────────────────────────────────────────
# Catches "I'm hungry", "I want a bowl of rice with black beans", etc. without
# specifying delivery platform, location, or walk-vs-deliver preference.

_FOOD_HUNGER = re.compile(
    r"\b(i'm hungry|i want\s+(food|to eat|to order|dinner|lunch|breakfast|snack|something|"
    r"to get something)|i need\s+(food|lunch|dinner|breakfast)|i crave|i could eat|"
    r"what'?s good to eat|where can i get|i'm (starving|famished))\b",
    re.I,
)

_FOOD_HUNGER_REPLY = (
    "What are you in the mood for, and where are you? I can check DoorDash, "
    "Uber Eats, or find nearby restaurants."
)

_FOOD_DISH = re.compile(
    r"\b(bowl\s+of|plate\s+of|some\s+(rice|pasta|noodles|soup|salad|tacos|sushi|"
    r"pizza|burger|sandwich|curry|ramen|chicken|wings|fries)|"
    r"a\s+(burrito|wrap|bowl|sub|pho|ramen|pad\s+thai|curry|taco|slice|"
    r"bottle|glass|cup)\s+of)\b",
    re.I,
)

_FOOD_DISH_REPLY = (
    "Would you like to walk to a restaurant nearby, or have it delivered? "
    "I can check DoorDash or Uber Eats."
)

_FOOD_WHERE = re.compile(
    r"\bwhere\s+(can|do|does)\s+i\s+(get|find|order|buy|eat)\b",
    re.I,
)

_FOOD_WHERE_REPLY = (
    "Are you looking for a nearby restaurant, or would you like delivery "
    "from DoorDash or Uber Eats?"
)

_FOOD_HAS_SERVICE = re.compile(
    r"\b(doordash|uber\s*eats|grubhub|postmates|seamless|delivery|"
    r"takeout|take.out|to\s+go|pickup|dine.in)\b",
    re.I,
)

_FOOD_HAS_PLACE = re.compile(
    r"\b(near\s+me|in\s+\w+|at\s+\w+|restaurant|chipotle|mcdonald|"
    r"taco\s+bell|burger\s+king|wendy|kfc|pizza\s+hut|domino|"
    r"subway|panda\s+express|popeyes|five\s+guys|shack\s+shake|"
    r"panera|starbucks|dunkin|chick.fil.a|q[uo]doba|"
    r"1?\d{3}\s+\w+\s+(st|ave|blvd|drive|road|ln|way))\b",
    re.I,
)

_FOOD_CITIES = re.compile(r"\b(boston|cambridge|somerville|medford|malden|everett|"
                          r"chelsea|revere|winthrop|quincy|brookline|newton)\b", re.I)

_FOOD_HOME_CITY = "everett"


def _food_inner(query: str) -> str | None:
    q = query.strip()

    # Skip food guard if the query is clearly about weather, transport, coding, or news
    if re.search(
        r"\b(weather|temp|temperature|forecast|rain|snow|degrees?|wind|news|map|directions|traffic|flight|bus|train|subway|lsh|lsof|port|process|job|run|build|git|code|diff|math|calc|lcm|gcd|multiply|divide|add|subtract)\b",
        q,
        re.I
    ):
        return None

    has_service = bool(_FOOD_HAS_SERVICE.search(q))
    has_place = bool(_FOOD_HAS_PLACE.search(q))

    if has_service or has_place:
        if has_place and not has_service:
            cities_found = _FOOD_CITIES.findall(q.lower())
            if cities_found and _FOOD_HOME_CITY not in cities_found:
                return (
                    f"I see you mentioned {cities_found[0].title()}, but your delivery "
                    f"address is in Everett. Where are you right now?"
                )
        return None

    if _FOOD_HUNGER.search(q):
        return _FOOD_HUNGER_REPLY

    if _FOOD_DISH.search(q):
        return _FOOD_DISH_REPLY

    if _FOOD_WHERE.search(q):
        return _FOOD_WHERE_REPLY

    return None


# ── Weather/location ambiguity ──────────────────────────────────────────────────
# "what's the weather" with no city named forced the web_search subagent into
# repeated rounds trying to disambiguate (26.8s first-token, confirmed live
# 2026-07-10) instead of just asking. No user-location config exists anywhere
# in this codebase to default to, so ask instead of guessing.
# ponytail: "in/at/for/near <word>" heuristic, not real NER — false negatives
# (unusual phrasing) just fall through to the normal search path, so the
# ceiling is "still slow sometimes," not "wrong answer."

_WEATHER_NO_LOCATION_RE = re.compile(
    r"\bweather\b(?!.*\b(?:in|at|for|near)\s+\w)", re.I,
)

_WEATHER_REPLY = "Which city's weather do you want?"

# 2026-07-11: fired even when "weather" was just one clause of a longer
# compound request ("add milk and bread to my list, also what's the weather
# tomorrow") — the canned reply short-circuited the ENTIRE query, silently
# dropping every other clause with no acknowledgment (confirmed live: a
# shopping-list request vanished, reply was only "Which city's weather do you
# want?"). Plain weather-only asks are short ("what's the weather", "hows the
# weather today" — all <=6 words in existing test coverage); cap so longer,
# compound queries fall through to the real pipeline instead, which can
# address every clause (slower, but correct beats fast-and-wrong here).
_WEATHER_MAX_WORDS = 12


def _weather_inner(query: str) -> str | None:
    q = query.strip()
    if len(q.split()) > _WEATHER_MAX_WORDS:
        return None
    if _WEATHER_NO_LOCATION_RE.search(q):
        return _WEATHER_REPLY
    return None


# ── Data analysis intent guard ─────────────────────────────────────────────────
# Catches "run stats", "do a regression", "compare the groups" without a file.

_DATA_ANALYSIS_BARE = re.compile(
    r"\b(?:analy[sz]e|run|do|compute|calculate|plot|chart|graph|visualize|compare|correlate|"
    r"describe|summarize|explore|clean|wrangle|tidy|preprocess|test|regress|cluster)\b"
    r".{0,30}\b"
    r"(?:data|dataset|stats?|statistics?|analysis|regression|test|correlation|anova|"
    r"t.test|chart|graph|plot|groups?|columns?|variables?|trend|pattern|result|finding)\b",
    re.I,
)

_DATA_OPERATION_BARE = re.compile(
    r"\b(?:compare|correlate|regress|cluster|pivot|aggregate)\b"
    r".{0,40}\b"
    r"(?:groups?|arms?|cohorts?|variables?|columns?|features?|categories?|treatments?|conditions?)\b",
    re.I,
)

_DATA_HAS_FILE = re.compile(
    r"\b\S+\.(?:csv|tsv|json|parquet|xlsx|feather|arrow)\b",
    re.I,
)

_DATA_HAS_FILE_REF = re.compile(
    r"\b(?:dataset|table|spreadsheet)\b"
    r"|\bfile\b.{0,30}\b(?:name|path|called|named)\b"
    r"|(?:/[\w./-]+\b)",
    re.I,
)

_DATA_HAS_PATH = re.compile(
    r"(?:~/|/[\w/]+\.\w+|[\w./-]*\.\w{2,4})",
)


def _data_inner(query: str) -> str | None:
    q = query.strip()

    if _DATA_HAS_FILE.search(q) or _DATA_HAS_PATH.search(q) or _DATA_HAS_FILE_REF.search(q):
        return None

    is_analysis = bool(_DATA_ANALYSIS_BARE.search(q))
    is_operation = bool(_DATA_OPERATION_BARE.search(q))

    if not is_analysis and not is_operation:
        return None

    if is_analysis:
        return (
            "Which data file would you like me to analyze? "
            "Please provide the file path (CSV, JSON, Parquet, Excel, etc.) "
            "or tell me what you're looking for."
        )
    if is_operation:
        return (
            "I can help with that! Which data file should I work with? "
            "Please provide the file path and I'll run the analysis."
        )

    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def check_all(query: str, has_history: bool = False) -> str | None:
    """
    Run ALL guard checks in priority order. Returns the first clarifying
    question that applies, or None if the query seems specific enough.

    Checks (in order):
      1. Deictic reference       — "explain this" / "tell me about it" (needs history)
      2. Sports/event ambiguity  — "who won" / "when is the game" / "Arsenal beat Chelsea"
      3. Ambiguous entity        — "Python" (language or snake?), "Apple", "Jaguar"
      4. Ambiguous toponym       — "Portland" (Oregon or Maine?), "Santiago", "Vienna"
      5. Weather/location        — "what's the weather" (no city named)
      6. Food/restaurant         — "I'm hungry" / "bowl of rice" (no service/location)
      7. Data analysis           — "run stats" / "compare groups" (no file)
    """
    q = query.strip()
    if not q:
        return "Please enter a valid search query."

    # Return early for completely junk/non-alphanumeric queries.
    # \w is Unicode-aware in Python 3 (matches Chinese/Japanese/Arabic/
    # Cyrillic/etc. letters, not just ASCII a-z0-9) -- the old
    # `[a-zA-Z0-9]` check rejected every purely non-Latin-script query as
    # "garbage" (found 2026-07-10: bare Chinese queries like "今天天气怎么样"
    # were blocked outright, undermining the Chinese-query search path in
    # tools/agent_reach.py). Emoji/punctuation-only queries still correctly
    # fail this check since they aren't \w.
    if not re.search(r"\w", q, re.UNICODE):
        return "Please enter a search query with valid text or characters."

    # 1. Deictic reference — only fires when has_history=False
    if not has_history:
        deictic = _deictic_inner(q)
        if deictic:
            return deictic

    # 2. Sports/event ambiguity (does NOT depend on history — these are
    #    inherently ambiguous regardless of prior conversation)
    sports = _sports_ambiguity_inner(q)
    if sports:
        return sports

    # 3. Weather — fires unless a city/location is named
    weather = _weather_inner(q)
    if weather:
        return weather

    # 4. Food/restaurant — fires unless service/location specified
    food = _food_inner(q)
    if food:
        return food

    # 5. Data analysis — fires unless a file is referenced
    data = _data_inner(q)
    if data:
        return data

    return None
