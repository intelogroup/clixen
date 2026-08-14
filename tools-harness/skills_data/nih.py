"""NIH-specific skills for biosketch and SciENcv workflows."""

from __future__ import annotations

from skills_hub import SKILLS, _s


SKILLS.append(_s(
    "NIH Biosketch",
    "Prepare an NIH Biographical Sketch Common Form and NIH Biographical Sketch Supplement draft from source documents and verified sources.",
    "Research",
    [
        "read_document", "read_file", "web_search", "search_pubmed",
        "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
        "browser_wait", "browser_get_url", "browser_screenshot",
        "fill_form", "validate_docx", "check_pdf_anomalies",
        "write_file",
    ],
    "Use this workflow for NIH biosketch requests:\n"
    "Gemma execution protocol: act in small, observable steps. First locate and read the source document; then verify requirements; then draft or fill the output; then validate it. After every tool result, reassess the next required step instead of repeating the same call. Use exact absolute paths from tool results.\n"
    "1. Read the supplied DOCX/PDF and extract education, appointments, honors, products, and narrative facts.\n"
    "2. Verify current NIH requirements from official grants.nih.gov pages. For submissions on or after May 8, 2026, use the Biographical Sketch Common Form plus NIH Biographical Sketch Supplement; NIH requires SciENcv for the final digitally certified output.\n"
    "3. Verify biographical claims with authoritative institutional, PubMed, DOI, or official conference sources. Do not invent dates, ORCID/PID, eRA Commons usernames, appointments, honors, publications, or certifications. Mark missing values as NOT PROVIDED / VERIFY.\n"
    "4. Prepare both Common Form content and Supplement content: identifying information, organization/location, professional preparation, appointments/positions, products, Personal Statement, Contributions to Science, and Honors. Include complete product citations and URLs where verified.\n"
    "5. If the user provides an existing form, preserve it and create a separate completed draft. Run validate_docx for DOCX output and check_pdf_anomalies for PDF output. Use browser tools for SciENcv only after the user is authenticated; never request or handle passwords in chat.\n"
    "6. Never digitally sign, certify, or claim NIH submission compliance on behalf of the investigator. Leave certification for the investigator in SciENcv. Report exactly which fields remain unresolved.\n"
    "7. Read the bundled reference at tools-harness/skills_data/references/nih-biosketch.md when detailed field rules or submission timing are needed.\n",
    [
        "nih biosketch", "NIH biographical sketch", "NIH common form",
        "biosketch supplement", "SciENcv", "NIH grant biosketch",
        "fill NIH form", "prepare biosketch",
    ],
    max_rounds=12,
    icon="file-check",
    trigger_regex=r"(?i)(?=.*\b(nih|sciencv|biosketch|biographical sketch|common form)\b)(?=.*\b(fill|prepare|complete|draft|update|form|grant)\b)",
))
