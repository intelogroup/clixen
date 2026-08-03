"""clients/router.py: pure regex constants and small pure helpers, order-independent."""

from __future__ import annotations

import re

def _tok(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Keyword patterns
# ---------------------------------------------------------------------------

_HARD_MATH_RE = re.compile(
    r"\b(integral|integrate|derivative|matrix|eigenvalue|proof|theorem|calculus|"
    r"statistics|probability|regression|optimization|gradient|fourier|"
    r"differential equation|linear algebra|AIME|competition math)\b",
    re.IGNORECASE,
)

_CODE_HEAVY_RE = re.compile(
    r"\b(implement|algorithm|data structure|time complexity|big.?o|"
    r"refactor|architecture|design pattern|unit test|benchmark|"
    r"concurren|async|thread|multiprocess|"
    r"write\b.*\b(function|class|module|program|script|code|system)|"
    r"fix\b.*\b(bug|error|issue)|"
    r"compare\b.*\b(python|javascript|typescript|rust|golang|java|c\+\+|ruby|php|go|language|approach|method|framework)|"
    r"build|optimize|design|develop)\b",
    re.IGNORECASE,
)

_CODE_QUICK_RE = re.compile(
    r"\b(python|javascript|typescript|rust|golang|java|bash|sql|"
    r"function|class|def |import |error|traceback|debug|syntax|"
    r"regex|json|array|snippet|fix this)\b",
    re.IGNORECASE,
)

_MULTILINGUAL_RE = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u0600-\u06FF\u0900-\u097F"  # Cyrillic, CJK, Arabic, Devanagari
    r"\u00C0-\u024F]",  # Extended Latin (accented French, German, etc.)
)

# Haitian Creole and unaccented French use plain ASCII — won't trigger _MULTILINGUAL_RE.
# Key Creole markers: high-frequency function words and common verb forms.
# French markers: common words that don't appear in English.
_CREOLE_FRENCH_RE = re.compile(
    r"\b("
    # Haitian Creole
    r"kijan|mwen|nou|ayiti|bonjou|bonswa|dakò|pa\s+gen|gen\s+yon|pinga|tanpri|"
    r"kote|poukisa|lè\s+sa|ki\s+jan|anpil|mèsi|li\s+di|"
    # French (accent-free markers that don't appear in English)
    r"bonjour|bonsoir|merci|s'il\s+vous\s+plaît|s'il\s+te\s+plaît|"
    r"qu'est-ce|c'est|n'est\s+pas|je\s+suis|nous\s+sommes|"
    r"comment\s+(vas|allez|tu|vous)|qu'est|pourquoi|parce\s+que|"
    r"d'accord|voici|voilà|maintenant|toujours|"
    r"non\b.{0,10}(merci|sûr|bien|vraiment)"
    r")\b",
    re.IGNORECASE,
)

_ANALYSIS_RE = re.compile(
    r"\b(explain|analyze|analyse|summarize|summarise|compare|research|"
    r"difference|pros|cons|evaluate|thesis|essay|literature|review|"
    r"overview|breakdown|detail|elaborate)\b",
    re.IGNORECASE,
)

_FILESYSTEM_RE = re.compile(
    # "cat " and "show me" are deliberately narrowed below (2026-07-01): bare "cat " matched
    # the pet noun ("adopted a cat named Whiskers" -> filesystem intent, routed to the
    # local-agent-graph, sometimes returning an empty reply), and bare "show me" stole
    # unrelated requests like "show me Chipotle on DoorDash" (see test_food_intent_guard.py).
    r"\b(read file|open file|show file|cat\s+[~./]|grep |find (?:a )?files?|search (?:for )?files?|list (?:the |all |out )?files?|"
    r"look at|check the|what('s| is) in|show me (?:\w+\s+){0,3}(?:files?|folders?|director(?:y|ies)|contents?)|contents of|"
    r"\.py|\.log|\.json|\.yaml|\.yml|\.toml|\.env|\.sh|\.txt|\.md|"
    r"\.docx?|\.pdf|\.pptx?|\.xlsx?|\.csv|"
    r"file path|directory|folder|ls |pwd|which file|source code|codebase|"
    # File management operations
    r"rename|change (?:\w+\s+){0,3}(?:file\s*|folder\s*|directory\s*)?names?|change names? of|filenames?|file names?|move file|delete file|remove file|delete folder|remove folder|"
    r"create folder|create directory|make folder|make directory|mkdir|"
    r"copy file|copy folder|copy [~/]|duplicate file|"
    r"biggest file|largest file|largest files|what('s| is) (the )?(biggest|largest)|"
    r"file size|how (big|large)|disk (space|usage)|taking up space|"
    r"what files|what('s| is) in my (downloads?|documents?|desktop|developer)|"
    r"downloads?|documents? (named|called))\b",
    re.IGNORECASE,
)

# Unambiguous local-storage signal: a known folder name co-occurring with files/folder.
# Used to stop a bare temporal cue ("right now", "currently") from hijacking a filesystem
# query in classify_telegram, where the general temporal check runs before _FILESYSTEM_RE.
_STRONG_LOCAL_FS_RE = re.compile(
    r"\b(downloads?|documents?|desktop)\b.*\b(files?|folders?|directory|directories)\b"
    r"|\b(files?|folders?|directory|directories)\b.*\b(downloads?|documents?|desktop)\b",
    re.IGNORECASE,
)

_LIBRARY_DOCS_RE = re.compile(
    r"\b(docs? for|documentation|api (reference|docs?)|"
    r"what('s| is) the (syntax|api|signature|option|config|parameter|prop) (for|of|in)|"
    r"(nextjs?|next\.js|react|vue|svelte|fastapi|django|flask|express|hono|"
    r"tailwind|prisma|drizzle|supabase|langchain|pydantic|sqlalchemy|celery|"
    r"lancedb|ollama|openai|anthropic|hugging.?face|pytorch|tensorflow|"
    r"numpy|pandas|matplotlib|scikit.?learn|playwright|puppeteer|"
    r"shadcn|radix|tanstack|zod|yup|vite|webpack|esbuild|bun|deno|"
    r"convex|trpc|graphql|apollo|stripe|twilio|sendgrid|"
    r"cargo|golang|java.?spring|rails|laravel)\b)",
    re.IGNORECASE,
)

_REPL_RE = re.compile(
    r"\b(run (this|the|this code|it)|execute|eval|test (this|it|the code)|"
    r"what (does|will) this (print|output|return)|"
    r"run in python|try this|check (this|the) (output|result)|"
    r"plot|matplotlib|pandas|numpy|import |print\(|\.py\b)\b",
    re.IGNORECASE,
)

_GIT_RE = re.compile(
    r"\b(git (status|diff|log|add|commit|push|pull|branch|checkout|merge|rebase|stash)|"
    r"commit (this|the|all|these|changes)|stage (this|the|all|these)|"
    r"what('s| is) (staged|changed|modified|untracked)|"
    r"(show|what|list).{0,20}(commits|branches|diff|log)\b|"
    r"create (a )?(branch|worktree)|new worktree|"
    r"working tree|git history)\b",
    re.IGNORECASE,
)

_CALENDAR_RE = re.compile(
    r"\b(calendar|events?|meetings?|appointments?|schedule (a |an )?meetings?|"
    r"add (to |an? )?events?|what.{0,10}(on my calendar|scheduled|coming up)|"
    r"block (off |out )?time|free slot|when am I (free|busy)|"
    r"create (a |an )?meetings?|book (a |an )?(slot|time|meetings?|appointments?))\b",
    re.IGNORECASE,
)

_AUTOMATION_RE = re.compile(
    r"(?:create|set.?up|add|make|configure|remove|pause|resume|delete|list|show|manage|stop|start|run|trigger)"
    r".*(?:automation|workflow|watcher|reminder|monitor|alert|notification|background.?task)"
    r"|(?:watch|monitor).+(?:email|gmail|sheet|spreadsheet|file|folder)"
    r"|(?:send|notify|alert).+(?:when|each.?time|every.?time|whenever).+(?:new|arrives|changes|appends)",
    re.I | re.S,
)

# Document creation — "make a pdf of this", "export to xlsx", "create a slide deck".
# Distinct from _AUTOMATION_RE (recurring jobs) — this is one-shot file generation.
_DOC_CREATE_RE = re.compile(
    r"(?:create|make|generate|build|export|save|turn|convert).{0,80}"
    r"\b(pdf|docx?|word\s*doc|xlsx?|excel|spreadsheet|pptx?|powerpoint|slide\s*deck|slides?|md|markdown)\b"
    r"|\b(?:as|in|into)\s+an?\s+(?:[a-zA-Z]+\s+){0,3}\.?"
    r"(?:pdf|docx?|word\s*doc|xlsx?|excel|spreadsheet|pptx?|powerpoint|slide\s*deck|slides?|md|markdown)\b",
    re.IGNORECASE | re.S,
)

_TASKS_RE = re.compile(
    r"\b(task|todo|to.do|to.?do list|add (a |an? )?task|"
    r"(my |show )?(tasks?|todos?)|check( off)?( my)? tasks?|"
    r"mark (as |it )?done|complete (a |the )?task|"
    r"what.{0,15}(on my list|need to do|left to do))\b",
    re.IGNORECASE,
)

_REMINDER_RE = re.compile(
    r"\b(remind(er)?|don.?t forget|alert me|notify me|ping me|set (a )?reminder|"
    r"schedule (a )?reminder|remember to|wake me)\b",
    re.IGNORECASE,
)

_SLACK_SEARCH_RE = re.compile(
    r"\b(?:in|on|from|search|find|look\s+up|what.{0,20}said|when.{0,20}said|who.{0,20}said|"
    r"my)\s+slack\b"
    r"|\bslack\s+(?:message|history|archive|search|thread|channel|dm)s?\b"
    r"|\b(?:search|find).{0,30}\bin\s+slack\b",
    re.IGNORECASE,
)

# iMessage / SMS history search + send — matches phrasings like
# "search my imessages for X", "what did mom text", "find that text from Sarah",
# "check imessage history", "did I sms about Y". Plural forms covered.
# Also covers explicit send phrasings: "send an imessage to +1...", "imessage to mom".
_IMESSAGE_SEARCH_RE = re.compile(
    r"\b(?:imessages?|i-messages?|sms|texts?)\b.{0,30}(?:about|for|with|from|history|search|archive)"
    r"|\b(?:search|find|check|look\s+up|browse|read|show).{0,30}\b(?:imessages?|sms|texts?|text\s+messages?)\b"
    r"|\b(?:recent|latest)\s+(?:texts?|imessages?|sms)\b"
    r"|\bwhat\s+did.{0,40}(?:text|imessage|sms)\b"
    r"|\bdid\s+i\s+(?:text|imessage|sms)\b"
    # Send-side phrasings (narrow to avoid stealing generic 'text' verbs):
    r"|\bsend\s+(?:an?\s+)?(?:imessage|i-message|sms|text\s+message)\b"
    r"|\bimessage\s+(?:to\s+)?[+@\w]",
    re.IGNORECASE,
)

# WhatsApp archive — bridge writes to ~/.clixen/whatsapp.db.
_WHATSAPP_SEARCH_RE = re.compile(
    r"\b(?:whatsapp|whats\s*app|wa)\b.{0,30}(?:about|for|with|from|history|search|archive|message|conversation)"
    r"|\b(?:search|find|check|look\s+up|browse|read).{0,30}\bwhatsapp\b"
    r"|\bwhat\s+did.{0,40}(?:whatsapp|whats\s*app)\b",
    re.IGNORECASE,
)

# Spotlight / mdfind — "find that file/PDF/screenshot".
# Also catches noun + time-window phrasings ("screenshots from yesterday",
# "PDFs this week", "docs I edited last 3 days") for the find_recent tool.
_SPOTLIGHT_RE = re.compile(
    r"\b(?:find|locate|where(?:'s| is)?|search\s+(?:for\s+)?(?:my|the)?)\b.{0,30}"
    r"(?:file|pdf|docx?|spreadsheet|xlsx|pptx?|screenshot|image|photo|picture|note|presentation)"
    r"|\bspotlight\s+(?:search|find|for)\b"
    r"|\bmdfind\b"
    # Noun-led recency phrases — noun within 30 chars of an explicit time window
    r"|\b(?:screenshots?|pdfs?|docx?|docs?|spreadsheets?|presentations?|videos?|images?|photos?|files?|notes?)\b"
    r".{0,30}?"
    r"\b(?:today|yesterday|this\s+week|last\s+week|this\s+month|last\s+month|last\s+\d+\s+(?:days?|weeks?|months?)|recent(?:ly)?)\b",
    re.IGNORECASE,
)

# Clipboard / native-macOS surface ("read my clipboard", "what's in my clipboard",
# "list my safari tabs", "search my apple notes for X", "make a note about Y").
_MACOS_NATIVE_RE = re.compile(
    r"\bclipboard\b"
    r"|\bsafari\s+tabs?\b"
    r"|\b(?:open|browser)\s+tabs?\b"
    r"|\bapple\s+notes?\b"
    r"|\bmy\s+notes?\b.{0,30}(?:for|about|search|find)"
    r"|\b(?:make|create|save|jot\s+down|new)\s+(?:a\s+|an\s+)?(?:apple\s+)?note\b"
    r"|\bnotes_create\b",
    re.IGNORECASE,
)

# System status — battery / disk / memory / wifi / uptime probes.
_SYSTEM_STATUS_RE = re.compile(
    r"\bbattery\b"
    r"|\b(?:disk|drive)\s+(?:free|space|usage|full)\b"
    r"|\bhow\s+(?:much|many|full)\b.{0,15}\b(?:disk|drive|space|free|gb|ram|memory|storage)\b"
    r"|\b(?:memory|ram)\s+(?:pressure|usage|free)\b"
    r"|\bsystem\s+status\b"
    r"|\bis\s+my\s+(?:mac|laptop|computer)\s+(?:ok|fine|low|overloaded|charged|plugged|charging)\b"
    r"|\bsystem\s+(?:health|info)\b"
    r"|\b(?:wifi|wi-fi|network)\s+(?:status|connection|ssid|name)\b",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(
    r"\b(email|gmail|inbox|unread|mail|send (an? )?email|"
    r"check (my )?(email|inbox|mail)|read (my )?(email|mail)|"
    r"any (new )?emails?|compose|draft)\b",
    re.IGNORECASE,
)

# Agentic: multi-step "fetch → process → deliver" patterns.
# These require chained tool calls. Gemma4 is the only model that executes
# them reliably — gemma4 shortcuts, qwen3 hallucinates content between steps.
# Delivery channels: telegram, slack (future).
_DELIVERY_CHANNEL_RE = re.compile(
    r"\b(telegram|slack)\b",
    re.IGNORECASE,
)
_AGENTIC_RE = re.compile(
    r"(check|read|fetch|get|look\s+at).{0,40}(send|forward|notify|post)|"
    r"(summarize|summarise|brief|digest).{0,30}(send|forward|post|telegram|slack)|"
    r"send\s+(it\s+)?(to\s+)?(me\s+on\s+)?(telegram|slack)\b|"
    r"\band\s+(send|forward|notify)\s+(me|it|to)\b|"
    r"send\s+me\s+(a\s+)?(summary|digest|brief|recap)",
    re.IGNORECASE,
)

_TEMPORAL_RE = re.compile(
    r"\b(current|latest|recent|today|tonight|yesterf?day|tomorrow|this week|upcoming|"
    r"weather|temperature|forecast|rain|storm|"
    r"stock|bitcoin|2024|2025|2026|news|election|live|"
    r"what year|what month|happening now|right now|"
    r"scores?|scorers?|winner|headlines?|sports?|"
    r"nba|nfl|mlb|nhl|playoffs?|standings|roster)\b",
    re.IGNORECASE,
)

# "price"/"cost" are far too common in non-temporal text ("how much does the ball cost",
# "cost function") to trigger web search on their own. They count as temporal ONLY when a
# market/freshness co-cue is present ("stock price", "cost of gold today", "$").
_PRICE_COST_RE = re.compile(r"\b(price|cost)\b", re.IGNORECASE)
_MARKET_CUE_RE = re.compile(
    # Word cues only — a bare currency symbol ($) also appears in math riddles
    # ("the ball costs $0.05"), so it is intentionally NOT a market cue.
    r"\b(stock|share|shares|crypto|bitcoin|btc|eth|ethereum|gold|oil|"
    r"ticker|market|today|current|latest|now|live|ticket|tickets|flight|"
    r"gas|fuel|usd|eur|gbp)\b",
    re.IGNORECASE,
)


# Live-sports phrasing the plain temporal keywords miss: "who won X vs Y", "is the
# soccer match playing now". Without this these route to `casual` → ungrounded chat →
# the model fabricates a scoreline (hallucination). Sport nouns require a nearby
# freshness/result cue so "I love football" doesn't trigger a search.
_SPORTS_RE = re.compile(
    r"\bwho\s+won\b|\bwho'?s\s+winning\b|\bfinal\s+score\b|"
    r"\b(?:world\s+cup|champions\s+league|premier\s+league|la\s+liga)\b|"
    r"\b(?:soccer|football|basketball|baseball|hockey|rugby|cricket|tennis|match|game)\b"
    r"[\s\S]{0,40}\b(?:playing|play|score|tonight|today|now|live|vs\.?|versus|won|winning)\b",
    re.IGNORECASE,
)


# "when did/when will" with a temporal co-cue (today/tomorrow/2025/etc) → temporal.
# Bare "when did world war 2 end" is historical, not a current-events search, and must
# NOT trigger temporal routing — _FACTUAL_QUESTION_RE catches it downstream.
_WHEN_RE = re.compile(
    r"\bwhen (?:did|will|is|was|are)\b.{0,30}"
    r"\b(today|tomorrow|yesterf?day|this (?:week|month|year|morning|afternoon|evening)|"
    r"next (?:week|month|year|time|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\w+)|"
    r"last (?:night|week|month|year|time|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|"
    r"202[4-9]|currently|now|recently|upcoming|later|expected|due|scheduled|"
    r"current|latest|happening)\b",
    re.IGNORECASE,
)


def _is_temporal(msg: str) -> bool:
    """True if the message needs fresh/web info. Generic words like 'price'/'cost'
    require a market/freshness co-cue so everyday phrasing doesn't trigger search."""
    if _TEMPORAL_RE.search(msg):
        return True
    if _SPORTS_RE.search(msg):
        return True
    if _WHEN_RE.search(msg):
        return True
    return bool(_PRICE_COST_RE.search(msg) and _MARKET_CUE_RE.search(msg))


# Self-referential / conversation-memory queries ("what's my name", "what was the first
# thing I told you", "you said earlier"). These must stay on the chat model with history —
# never web search — so they are checked before temporal/factual routing.
_CONVO_REF_RE = re.compile(
    r"\b(my name is|my name|i told you|i said|you said|you told me|you mentioned|"
    r"we (talked|discussed|said|agreed|covered)|earlier (i|you|we)|first thing i|"
    r"what did i (say|tell|do|ask)|remind me what (i|we)|"
    # Self-referential about the ASSISTANT's own recent turn ("what did you just do",
    # "what was the last thing you saw") — found 2026-07-01: without these, such a question
    # fell through to factual_qa, which always forces a web search, and the model searched the
    # open web for "last action" and reported on an unrelated documentary trailer.
    r"what (did|do) you (just |last )?(do|see|say|find|show|tell)|"
    r"what (was|is) the last (thing|action) you (did|saw|said|showed|found)|"
    r"you (just|last) (did|saw|said|showed|found)|"
    # General form covering phrasings the specific alternatives above miss, e.g. "what was
    # the last question I asked you" — any "what was/is the last/first/previous <noun> I/you
    # <recall-verb>" is inherently about this conversation's own history, never the open web.
    r"what (?:was|is) the (?:last|first|previous) \w+ (?:i|you) "
    r"(?:asked|said|told|did|saw|showed|gave|sent|found|answered|searched))\b",
    re.IGNORECASE,
)

# Transit / transportation queries → LLM tool loop with bus_eta + estimate_uber_ride
# Must be checked before _TEMPORAL_RE to catch "uber cost from X to Y".
_TRANSIT_RE = re.compile(
    r"\b(uber|lyft|taxi|cab|rideshare|"
    r"bus|subway|metro|mbta|transit)"
    r"(?!\s+(?:stock|share|ipo|revenue|profit|earnings|valuation|market|company|business|investor))\b"
    r"|"
    r"\b("
    r"directions?\s+(?:from|to|between)|"
    r"(?:uber|lyft|taxi|ride|bus)\s+(?:price|cost|estimate|fare|eta|pickup|dropoff|from|to)|"
    r"(?:price|cost|estimate|fare|eta)\s+(?:of|for|to)\s+(?:an?\s+)?(?:uber|lyft|taxi|ride|bus)|"
    r"(?:get|take|catch|need|want|call|book|hail)\s+(?:a|an|the)?\s*(?:uber|lyft|taxi|bus|ride|cab)|"
    r"(?:via|by)\s+(?:uber|lyft|taxi|bus|subway|train|transit|mbta)|"
    r"(?:how\s+(?:much|do|can|to))\s+.*\b(?:uber|lyft|taxi|bus|subway|ride|mbta)|"
r"(?:how\s+(?:do|can|to|would))\s+.*\b(?:get|go|travel)\b.*\b(?:from|to)\b"
    r")\b",
    re.IGNORECASE,
)

# "Save/export to desktop/file" + any search/temporal signal → force temporal
_DESKTOP_SAVE_RE = re.compile(
    r"\b(save|export|write|put|store|dump)\b.{0,40}\b("
    r"desktop|file|spreadsheet|excel|\.md|\.xlsx|\.csv|markdown|document"
    r")\b",
    re.I | re.S,
)

# Local discovery: place/business lookup with live criteria (hours, seating, ratings, etc.).
# Model memory is unreliable for hours/availability — must go through search graph.
_LOCAL_DISCOVERY_RE = re.compile(
    r"\b(restaurant|cafe|bar|pub|coffee\s+shop|brewery|bakery|diner|bistro|"
    r"hotel|hostel|gym|spa|salon|store|shop|dispensary|pharmacy|clinic|"
    r"museum|gallery|park|venue|theater|cinema)\b"
    r"|"
    r"\b(open\s+until|closes?\s+at|open\s+late|open\s+on|hours?\s+on|"
    r"outdoor\s+seating|patio|rooftop|dine\s+outside|"
    r"vegan|vegetarian|gluten.free|halal|kosher|"
    r"near\s+(me|downtown|the\s+airport|the\s+mall)|"
    r"in\s+(austin|dallas|houston|new\s+york|chicago|la|los\s+angeles|"
    r"san\s+francisco|seattle|miami|denver|boston|portland|nashville|"
    r"atlanta|phoenix|las\s+vegas)|"
    r"highly.rated|top.rated|best\s+(vegan|pizza|sushi|tacos?|brunch)|"
    r"michelin|yelp|tripadvisor|google\s+reviews?)\b",
    re.IGNORECASE,
)

# Factual questions that need knowledge verification — avoid qwen3.5:4b.
# Catches: "what is X", "how is X", "who is X", "when is X", etc.
# Excludes conversational "how are you" (about:you, person, me, etc.)
_FACTUAL_QUESTION_RE = re.compile(
    r"\b(what (is|are|does|was|were|do)|"
    r"who (is|are|was|were|wrote|invented|built|created|made|discovered|painted|composed|designed|founded|directed)(?! (you|i|me|we))|"
    r"when (is|was|did|do)|"
    r"where (is|are|was|were)|"
    r"why (is|are|do|does|did)|"
    r"how (is|are|does|do|did)(?! (you|i|me|we|are you))|"
    r"tell me (about|what)|explain|define|describe|"
    r"give me (info|facts|details) (about|on)|"
    # "do you know who/what/when/where/why/how" — negative lookahead blocks
    # self-referential phrasings like "do you know what I mean" (i/my/me close behind).
    r"do you know\s+(who|what|when|where|why|how|if|whether)\b(?![^.]{0,24}\b(?:i|my|me)\b)|"
    r"can you tell me\s+(who|what|when|where|why|how|if|whether)\b(?!\s+(?:who|what|when|where|why|how)\b)|"
    r"could you tell me\s+(who|what|when|where|why|how|if|whether)\b)\b",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://\S{4,}|www\.\S+\.\S+", re.IGNORECASE)

_BROWSER_RE = re.compile(
    r"\b(open (a |the )?(browser|webpage|website|page|url|link|google|safari|chrome|firefox|edge|brave|arc)|"
    r"go to (https?://|www\.)|navigate to|visit (the |a )?site|"
    r"log.?in (to|into) (the |a )?(website|site|app|portal|dashboard|account)|"
    r"login (to|into)|sign in (to|into)|"
    r"fill (out |in )?(the |a )?(\w+ )?form|click (the |a )?button|"
    r"browser.?(navigate|click|type|snapshot|scrape|automat)|"
    r"playwright|puppeteer|selenium|headless|web scrape|web automat|"
    r"check (my |the )?account (on|at)|"
    r"(submit|send) (the |a )?form|extract (data|text|info) from (the |a )?(page|site|website)|"
    r"browse|"
    r"(?:log\s*(?:in|into|on)|sign\s*(?:in|into|on)|check|open|navigate\s+to|get|fetch|pull|download)\s+(?:my\s+)?(?:account\s+(?:on\s+)?)?(?:doordash|uber|instacart|amazon|orders?\s+(?:from|on))|"
    r"\bapi\s+(?:fetch|call|get|config)\b)",
    re.IGNORECASE,
)

_LOCAL_FILE_RE = re.compile(r"~/|\.(docx|pdf|pptx|xlsx|csv|txt|md)\b", re.IGNORECASE)

# YouTube search/transcript — must beat _BROWSER_RE ("search") and _FILESYSTEM_RE
# (transcript videos often get referred to by name, no fs keyword overlap normally,
# but keep this checked early in classify()/classify_telegram() to be safe).
_YOUTUBE_RE = re.compile(
    r"\b(youtube|yt)\b.{0,30}\b(search|video|videos|transcript|transcripts|channel)\b|"
    r"\b(search|find|look up|look for)\b.{0,20}\b(youtube|yt)\b|"
    r"youtube\.com|youtu\.be|watch\?v=|"
    r"\b(transcript|subtitles?|captions?)\b.{0,25}\b(youtube|video|yt)\b|"
    r"\b(youtube|yt)\b.{0,25}\b(transcript|subtitles?|captions?)\b",
    re.IGNORECASE,
)

# Chinese-internet search → agent-reach tooling (Bilibili / Exa / XiaoHongShu / Weibo / V2EX).
_CHINESE_WEB_RE = re.compile(
    r"(xiaohongshu|xhs|小红书|weibo|微博|douyin|抖音|bilibili|哔哩哔哩|v2ex|"
    r"\b(chinese|china)\b.{0,30}\b(web|search|internet|source|perspective)\b|"
    r"\b(search|find|look up)\b.{0,20}\b(chinese|china|xiaohongshu|weibo|bilibili|xhs)\b|"
    r"中文(搜索|资料|内容|互联网)|在中国(怎么|如何)|"
    r"\b(中文|中国)\b.{0,20}\b(search|find|look up|source)\b)",
    re.IGNORECASE,
)

_OCR_RE = re.compile(
    r"\b(ocr|extract text|read text (from|in)|scan (this |the )?(image|photo|screenshot|doc)|"
    r"what (does|do) (this|the) (image|photo|screenshot) say|"
    r"text in (this|the) (image|photo|screenshot|picture)|"
    r"get text from|transcribe (this )?(image|photo|screenshot)|"
    r"take (?:a\s+)?screenshot|capture (?:my\s+)?screen|screenshot\s+my\s+screen|mac\s+screenshot)\b",
    re.IGNORECASE,
)

# Document automation: summarize papers/docs/citations → deliver via Telegram + optionally create tasks.
# Must be checked before _ANALYSIS_RE (which would otherwise swallow "summarize my papers").
_DOC_AUTOMATION_RE = re.compile(
    r"(summar|digest|brief|recap|review).{0,60}"
    r"(paper|article|citation|doc|publication|research|literature|stud)",
    re.IGNORECASE,
)

# Inbox monitor / PDF attachment pipeline.
# Matches queries asking to check Gmail for PDF attachments from specific senders.
_INBOX_PDF_RE = re.compile(
    r"(run_inbox_monitor|list_pdf_attachments|list.*pdf.*attach"
    r"|check.*inbox.*pdf|check.*gmail.*pdf|pdf.*email|email.*pdf"
    r"|attach.*from.*(gmail|inbox|sender)|check.*attach|new.*pdf.*from|convert_pdf"
    r"|check.*email.*from.*(jayveedz|kalinovjim)|inbox.*monitor)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


