"""Skill definitions — split out of skills_hub.py by category. See skills_hub.py for Skill/_s/SKILLS."""

from __future__ import annotations

from skills_hub import SKILLS, _s

# ── Clutter Fixer ────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Clutter Fixer", "Clean up messy directories (Downloads, Desktop). Analyze, preview, then reorganize files by month, topic, or both.",
    "Files",
    ["analyze_directory", "suggest_organization", "apply_organization",
     "read_file", "list_directory", "delete_file"],
    "You are a clutter-fixing assistant. Guide the user through these steps — do not skip any:\n\n"
    "STEP 1: Ask the user which directory to clean (or default to ~/Downloads).\n"
    "STEP 2: Call analyze_directory(path) to get a full inventory.\n"
    "Present the findings: total files, size, by extension, by month, topics, largest/oldest files, "
    "duplicate candidates, and junk file candidates.\n\n"
    "STEP 3: Ask about DUPLICATES first — show groups with same content, "
    "ask user 'keep one or keep all?' for each group.\n\n"
    "STEP 4: Ask about JUNK FILES — DMGs, packages, temp files. Ask user 'delete these?'\n\n"
    "STEP 5: Present organization schemes and let user CHOOSE:\n"
    "  - monthly: files grouped by YYYY-MM folder\n"
    "  - by_topic: files grouped by detected topic (tax, invoice, photo, code, etc.)\n"
    "  - topic_monthly: topic/ → month-year/ subfolders\n"
    "  - topic_descriptive: topic/ → descriptive subfolders with dated filenames\n\n"
    "STEP 6: Call suggest_organization(path, scheme) to show a DRY-RUN preview.\n"
    "Show the folder structure and sample file moves. Ask user to CONFIRM.\n\n"
    "STEP 7: After user confirms, ask what specific files/paths to delete "
    "(duplicates they want to remove, junk files, empty folders).\n\n"
    "STEP 8: Call apply_organization(path, scheme, delete_paths=[...]) to execute.\n"
    "Report: files moved, files deleted, space freed.\n\n"
    "CRITICAL: NEVER call apply_organization without explicit user confirmation. "
    "ALWAYS show the preview first. ALWAYS ask about duplicates and junk files before deleting.",
    ["clutter fixer", "clean up downloads", "organize desktop", "neaten files",
     "tidy up", "reorganize files", "clean my folder", "messy downloads", "reorganize downloads"],
    max_rounds=15, icon="folder",
))


# ── Transit ──────────────────────────────────────────────────────────────────

SKILLS.append(_s(
    "Bus ETA", "Find MBTA stops and real-time bus/subway arrivals near an address, or get Google Maps transit directions between two locations in Boston.",
    "Transit",
    ["bus_eta"],
    "Call bus_eta(origin=...) to find nearby MBTA stops and real-time arrival predictions. "
    "If the user also provides a destination, pass destination=... to get Google Maps transit directions "
    "(bus/subway only). Optionally use depart_in=N (minutes from now) to plan a future departure.\n\n"
    "Examples:\n"
    "- bus_eta(origin='840 Harrison Ave, Boston MA') — shows nearby stops and arrivals\n"
    "- bus_eta(origin='BMC', destination='Everett MA') — transit directions between two places\n"
    "- bus_eta(origin='BMC', destination='3 Manning Terrace, Everett MA') — full directions\n\n"
    "Be concise. Show the route steps, times, and total duration. Only works for Boston/Massachusetts.",
    ["bus", "MBTA", "transit", "subway", "train schedule", "how to get", "directions to",
     "Boston transit", "commute", "public transit", "T stop", "bus stop", "transit directions",
     "public transport", "route from", "getting around"],
    max_rounds=3, icon="navigation",
))


SKILLS.append(_s(
    "Adaptive Web Scrape", "Scrape a website that has class-name churn or A/B-test DOM changes. "
    "Uses Scrapling adaptive mode to relocate elements after redesign.",
    "Web",
    ["scrapling_extract", "scrapling_fetch_and_extract", "scrapling_fetch", "scrapling_stealthy_fetch"],
    "STEP 1: Choose the right tool:\n"
    "- scrapling_fetch(url) — plain HTTP, for static pages\n"
    "- scrapling_stealthy_fetch(url, headless=True) — for anti-bot sites (use headless=True)\n"
    "- scrapling_extract(url, selector, mode='auto_save', extract_type='text') — CSS extract with fingerprint storage\n"
    "- scrapling_fetch_and_extract(...) — one-call pipeline\n\n"
    "STEP 2: First call should use mode='auto_save' to store a fingerprint. Subsequent calls use mode='adaptive'.\n\n"
    "STEP 3: Common gotchas:\n"
    "- Fetcher doesn't accept headless kwarg — use StealthyFetcher\n"
    "- Selector needs content= and url= (not text=)\n"
    "- Use .html_content (not .html) on results",
    ["scrape this", "extract from page", "adaptive scrape", "site keeps changing",
     "scrapling", "fetch with stealth", "extract by selector", "scrapling_fetch",
     "scrapling_extract", "site redesign broke", "A/B test broke parser"],
    max_rounds=4, icon="web",
))


