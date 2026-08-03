"""Skill definitions — split out of skills_hub.py by category. See skills_hub.py for Skill/_s/SKILLS."""

from __future__ import annotations

from skills_hub import SKILLS, _s

# ── Research & Intelligence ─────────────────────────────────────────────────

SKILLS.append(_s(
    "ArXiv Search", "Search academic papers on arXiv by keyword or topic.",
    "Research",
    ["search_arxiv", "read_document"],
    "Call search_arxiv(query, max_results=5) to find academic papers. "
    "Present results clearly: title, authors, year, abstract, PDF link. "
    "If the user wants to save results, offer to create a Google Doc.",
    ["arxiv", "academic paper", "research paper", "preprint", "scientific paper", "find paper"],
    max_rounds=4, icon="book", trigger_regex=r"\b(arxiv|find paper|academic paper|research paper)\b",
))

SKILLS.append(_s(
    "PubMed Search", "Search medical literature on PubMed.",
    "Research",
    ["search_pubmed", "read_document"],
    "Call search_pubmed(query, max_results=5) to find medical articles. "
    "Present results: PMID, title, authors, journal, year, abstract, DOI. "
    "For medical/health questions, always use PubMed before answering from memory.",
    ["pubmed", "medical research", "clinical trial", "health study", "disease research"],
    max_rounds=4, icon="book",
))

SKILLS.append(_s(
    "Science Brief", "Multi-source research summary combining web, arXiv, and PubMed.",
    "Research",
    ["web_search", "search_arxiv", "search_pubmed", "create_google_doc"],
    "1. Search web for recent news/context.\n"
    "2. Search arXiv for relevant preprints.\n"
    "3. Search PubMed if health/medical.\n"
    "4. Synthesize a brief: key findings, consensus, gaps.\n"
    "5. Offer to save as Google Doc.",
    ["science brief", "literature review", "what does the research say", "scientific consensus"],
    max_rounds=8, icon="book",
))

SKILLS.append(_s(
    "YouTube Research", "Find YouTube videos on a topic and extract transcripts for summarization.",
    "Research",
    ["search_youtube", "get_youtube_transcript", "read_document"],
    "1. Call search_youtube(query) to find relevant videos.\n"
    "2. If the user wants details, call get_youtube_transcript(url) on the most relevant video.\n"
    "3. Summarize the transcript. Offer to save to Google Doc.",
    ["youtube", "video search", "find video", "watch later", "video transcript"],
    max_rounds=6, icon="play",
))

SKILLS.append(_s(
    "Daily News Digest", "Top news stories by topic — summary sent to WhatsApp/Telegram.",
    "Research",
    ["web_search", "send_whatsapp", "send_telegram"],
    "1. Extract the topic or category from the user's message.\n"
    "2. Call web_search() for the latest news on that topic.\n"
    "3. Compile a concise digest: top 5-7 headlines with one-sentence summaries.\n"
    "4. Send via WhatsApp and/or Telegram based on user preference.\n"
    "5. Confirm what was sent.",
    ["daily news", "news digest", "what's in the news", "top stories", "news briefing"],
    max_rounds=5, icon="newspaper", trigger_regex=r"\b(news (briefing|digest|update|today)|daily news|top stories)\b",
))

SKILLS.append(_s(
    "Market Snapshot", "Quick overview of stock prices, crypto, or market movers.",
    "Research",
    ["web_search", "send_whatsapp"],
    "1. Determine what the user wants (specific ticker, crypto, market indices).\n"
    "2. Call web_search() for current prices and recent moves.\n"
    "3. Present a brief snapshot: price, daily change, key headlines.\n"
    "4. If user wants alerts, offer to send via WhatsApp.",
    ["market snapshot", "stock price", "crypto price", "market update", "how is the market"],
    max_rounds=4, icon="chart",
))

SKILLS.append(_s(
    "Competitor Intel", "Search for competitor updates and extract actionable signals.",
    "Research",
    ["web_search", "create_task", "send_whatsapp", "get_current_time"],
    "1. Extract the competitor name from the user's message.\n"
    "2. Call web_search() for recent news, product launches, funding, hires.\n"
    "3. Identify actionable signals: new features, pricing changes, market moves.\n"
    "4. Create Google Tasks for follow-up items.\n"
    "5. Send summary via WhatsApp.",
    ["competitor", "competitor watch", "what is X doing", "competitive analysis", "market intel"],
    max_rounds=6, icon="radar",
))

SKILLS.append(_s(
    "Finance Report", "Generate a structured finance/market report saved to Google Docs.",
    "Research",
    ["web_search", "create_google_doc", "send_whatsapp"],
    "1. Determine the scope (ticker, sector, market).\n"
    "2. Call web_search() for financial data, news, analyst opinions.\n"
    "3. Create a structured Google Doc: overview, key metrics, news, outlook.\n"
    "4. Share the Doc link and optionally notify via WhatsApp.",
    ["finance report", "market report", "investment research", "company analysis"],
    max_rounds=7, icon="chart",
))


# ── Business Operations ─────────────────────────────────────────────────────

SKILLS.append(_s(
    "Customer Follow-Up", "Find unanswered email threads, create reminders, draft replies.",
    "Business",
    ["list_emails", "read_email", "create_task", "send_whatsapp", "get_current_time"],
    "1. Call list_emails() to find recent threads without replies.\n"
    "2. Read each relevant email with read_email().\n"
    "3. Identify which need follow-up (urgent, client, pending action).\n"
    "4. Create a Google Task for each with context.\n"
    "5. Offer to draft reply drafts or notify via WhatsApp.\n"
    "6. Present a summary: N threads found, M need follow-up.",
    ["customer follow-up", "follow up", "unanswered emails", "who haven't I replied to",
     "client follow-up", "pending replies"],
    max_rounds=8, icon="briefcase",
))

SKILLS.append(_s(
    "Invoice Processor", "Find invoice attachments in email, extract to spreadsheet, alert you.",
    "Business",
    ["run_inbox_monitor", "list_google_sheets", "append_google_sheet", "send_whatsapp"],
    "1. Call run_inbox_monitor() to find invoice emails with PDF attachments.\n"
    "2. Review results and ask the user which sheet to log to.\n"
    "3. Call append_google_sheet() to log each invoice (date, sender, amount, status).\n"
    "4. If no invoice tracking sheet exists, call create_google_sheet() with headers first.\n"
    "5. Send a summary via WhatsApp.",
    ["invoice processor", "process invoices", "track invoices", "invoice tracker", "find invoices"],
    max_rounds=8, icon="file",
))

SKILLS.append(_s(
    "Meeting Prep", "Pre-meeting brief: attendees, recent emails, agenda context.",
    "Business",
    ["list_calendar_events", "list_emails", "web_search", "send_whatsapp", "get_current_time"],
    "1. Find the next meeting from list_calendar_events().\n"
    "2. Check recent emails from attendees for context.\n"
    "3. If it's an external meeting, web_search() the person/company.\n"
    "4. Compile a prep brief: attendees, context, questions to ask, action items.\n"
    "5. Send via WhatsApp before the meeting.",
    ["meeting prep", "prepare for meeting", "meeting brief", "what do I need for this meeting",
     "prepare for my meeting", "prep for meeting", "get ready for meeting"],
    max_rounds=7, icon="calendar", trigger_regex=r"\b(prepare|prep|get ready)\b.*\bmeeting\b",
))

SKILLS.append(_s(
    "Lead Tracker", "Extract contacts from emails and build a lead spreadsheet.",
    "Business",
    ["list_emails", "read_email", "create_google_sheet", "append_google_sheet"],
    "1. Call list_emails() to find emails from new contacts or leads.\n"
    "2. Read each email and extract: name, company, email, phone, context.\n"
    "3. If no leads sheet exists, call create_google_sheet() with headers.\n"
    "4. Call append_google_sheet() with each lead's info.\n"
    "5. Report how many leads were added.",
    ["lead tracker", "track leads", "lead list", "contact list", "new contacts", "customer list",
     "track contacts", "leads"],
    max_rounds=7, icon="users", trigger_regex=r"\b(track\s+leads|track\s+contacts|lead\s+tracker|lead\s+list|my leads)\b",
))

SKILLS.append(_s(
    "Amazon Deal Watch", "Track product prices and alert on drops.",
    "Business",
    ["web_search", "send_whatsapp", "create_task", "get_current_time"],
    "1. Extract the product name/category from the user's message.\n"
    "2. Call web_search() for current prices on Amazon.\n"
    "3. Note price ranges, deals, and ratings.\n"
    "4. Present findings and offer to create a monitoring task.\n"
    "5. If the user wants alerts, create a task to re-check later.",
    ["amazon deal", "amazon price", "deal watch", "price tracker", "shopping deal"],
    max_rounds=5, icon="shopping",
))

SKILLS.append(_s(
    "Subscription Audit", "Find recurring charges in email and build an audit spreadsheet.",
    "Business",
    ["list_emails", "read_email", "create_google_sheet", "append_google_sheet"],
    "1. Call list_emails() searching for keywords: receipt, invoice, subscription, billed, payment.\n"
    "2. Read each relevant email to extract: service, amount, frequency, date.\n"
    "3. Call create_google_sheet() with headers: Service, Amount, Frequency, Last Charge, Status.\n"
    "4. Call append_google_sheet() for each subscription found.\n"
    "5. Present summary: total monthly spend, subscriptions to review.",
    ["subscription audit", "audit subscriptions", "recurring charges", "what am I paying for",
     "cancel subscriptions", "find subscriptions"],
    max_rounds=8, icon="credit-card",
))

SKILLS.append(_s(
    "Invoice Generator", "Create a professional invoice as Google Doc and email it.",
    "Business",
    ["create_google_doc", "send_email", "get_current_time"],
    "1. Ask the user for: client name, items/services, amounts.\n"
    "2. Call create_google_doc() with a formatted invoice template.\n"
    "3. Offer to send_email() with the invoice details or Doc link.\n"
    "4. Confirm what was created and sent.",
    ["invoice generator", "create invoice", "send invoice", "bill client"],
    max_rounds=5, icon="file",
))

SKILLS.append(_s(
    "Contract Reminder", "Track contract/renewal dates from calendar and set reminders.",
    "Business",
    ["list_calendar_events", "create_task", "set_reminder", "get_current_time"],
    "1. Call list_calendar_events() and look for contract/renewal/deadline events.\n"
    "2. For each, check if there's enough lead time.\n"
    "3. Create a Google Task for any contract review needed.\n"
    "4. Set reminders 30, 14, and 7 days before each deadline.\n"
    "5. Summarize all contract dates and reminders set.",
    ["contract reminder", "renewal reminder", "contract date", "when does my contract expire"],
    max_rounds=6, icon="calendar", trigger_regex=r"\b(contract|renewal)\s+(reminder|date|deadline|expir)",
))

SKILLS.append(_s(
    "Expense Report", "Extract expense receipts from inbox into a spreadsheet.",
    "Business",
    ["list_emails", "read_email", "create_google_sheet", "append_google_sheet"],
    "1. Call list_emails() searching for receipt, expense, paid, purchase keywords.\n"
    "2. Read relevant emails and extract: date, vendor, amount, category.\n"
    "3. Create or update an expense tracking sheet.\n"
    "4. Present summary: total expenses, by category, period.",
    ["expense report", "expense tracking", "track expenses", "receipts", "business expenses"],
    max_rounds=7, icon="credit-card",
))


