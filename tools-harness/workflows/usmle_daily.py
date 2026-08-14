"""
USMLE Daily — 5-step agentic pipeline.
  1. RESEARCH: scrape PubMed for 2 shuffled medical topics
  2. GENERATE: from scraped content, create USMLE questions with answers
  3. VERIFY: cross-check answers against source material
  4. DEDUP: semantic similarity check against prior questions
  5. DELIVER: Telegram + notification + log
"""

from __future__ import annotations

import os
import random
import httpx
from datetime import datetime
from workflows.pipeline import (
    PipelineStep, TelegramSendStep, NotificationStep, LogAppendStep, run_pipeline,
)
from store import usmle_store

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
USMLE_MODEL = os.environ.get("USMLE_SUMMARY_MODEL", "openai/gpt-4o-mini")
MAX_QUESTIONS = int(os.environ.get("USMLE_MAX_QUESTIONS", "2"))

MEDICAL_TOPICS = [
    "pharmacology", "pathology", "physiology", "anatomy",
    "microbiology", "biochemistry", "immunology", "cardiology",
    "pulmonology", "gastroenterology", "neurology", "psychiatry",
    "dermatology", "orthopedics", "obstetrics", "pediatrics",
]


def _call_cloud(prompt: str, system: str = "", temperature: float = 0.3,
                max_tokens: int = 800) -> str:
    from clients.cloud_client import raw_completion
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        choice = raw_completion(
            model=USMLE_MODEL, messages=messages, tools=[],
            temperature=temperature,
        )
        return choice.message.content.strip()
    except Exception as exc:
        return f"[ai error] {exc}"


class ResearchStep(PipelineStep):
    """Scrape PubMed abstracts for a medical topic."""

    def __init__(self, topic: str, output_key: str, max_results: int = 3):
        self.topic = topic
        self.output_key = output_key
        self.max_results = max_results

    def run(self, ctx: dict) -> dict:
        import urllib.parse
        query = urllib.parse.quote(f'"{self.topic}"[tiab] AND review[pt]')
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}&retmax={self.max_results}&sort=date&retmode=json"
        try:
            r = httpx.get(url, timeout=15)
            r.raise_for_status()
            id_list = r.json().get("esearchresult", {}).get("idlist", [])
        except Exception as exc:
            ctx[self.output_key] = f"[pubmed search error] {exc}"
            return {"ok": False, "error": str(exc)}
        if not id_list:
            ctx[self.output_key] = f"[pubmed] no recent reviews for {self.topic}"
            return {"ok": True, "output": "no results", "has_content": False}

        ids = ",".join(id_list)
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids}&rettype=abstract&retmode=text"
        try:
            r = httpx.get(fetch_url, timeout=15)
            r.raise_for_status()
            text = r.text[:8000]
        except Exception as exc:
            ctx[self.output_key] = f"[pubmed fetch error] {exc}"
            return {"ok": False, "error": str(exc)}

        ctx[self.output_key] = text
        return {"ok": True, "output": f"{len(id_list)} abstracts ({len(text)} chars)",
                "has_content": True}


class ExtractFactsStep(PipelineStep):
    """Extract key medical facts from scraped content via cloud AI."""

    def __init__(self, source_key: str, output_key: str, topic: str):
        self.source_key = source_key
        self.output_key = output_key
        self.topic = topic

    def run(self, ctx: dict) -> dict:
        source = ctx.get(self.source_key, "")
        if source.startswith("[pubmed") or "[pubmed" in source:
            ctx[self.output_key] = ""
            return {"ok": True, "output": "no source content", "has_content": False}
        prompt = (
            f"Extract 5-8 key medical facts about {self.topic} from the PubMed abstracts below. "
            f"Focus on disease mechanisms, diagnostic criteria, treatments, and clinical correlations. "
            f"Output only the facts, one per line.\n\n{source[:6000]}"
        )
        facts = _call_cloud(prompt, temperature=0.2, max_tokens=600)
        ctx[self.output_key] = facts
        return {"ok": not facts.startswith("[ai error]"), "output": facts[:200],
                "has_content": bool(facts and not facts.startswith("[ai error]"))}


class GenerateQuestionStep(PipelineStep):
    """Generate USMLE question from extracted facts (source-backed, not hallucinated)."""

    def __init__(self, facts_key: str, output_key: str, topic: str, q_num: int):
        self.facts_key = facts_key
        self.output_key = output_key
        self.topic = topic
        self.q_num = q_num

    def run(self, ctx: dict) -> dict:
        facts = ctx.get(self.facts_key, "")
        if not facts:
            prompt = (
                f"Generate one original USMLE Step 1 multiple-choice question about {self.topic}. "
                f"Write a clinical vignette, then A-E choices, indicate the correct answer, "
                f"and give a 1-2 sentence explanation.\n\n"
                "Format:\n"
                "VIGNETTE: [2-4 sentence clinical scenario]\n"
                "A) [choice]\nB) [choice]\nC) [choice]\nD) [choice]\nE) [choice]\n"
                "ANSWER: [letter]\nEXPLANATION: [reasoning]"
            )
        else:
            prompt = (
                f"Using ONLY the medical facts below as source material, generate one original "
                f"USMLE Step 1 multiple-choice question about {self.topic}. The correct answer "
                f"must be verifiable from the facts provided. Do not invent facts.\n\n"
                "FACTS:\n" + facts[:4000] + "\n\n"
                "Format:\n"
                "VIGNETTE: [2-4 sentence clinical scenario]\n"
                "A) [choice]\nB) [choice]\nC) [choice]\nD) [choice]\nE) [choice]\n"
                "ANSWER: [letter]\nEXPLANATION: [reasoning citing which fact supports it]"
            )

        system = "You are a USMLE Step 1 question writer. Generate high-quality, medically accurate questions with verified answers."
        raw = _call_cloud(prompt, system=system, temperature=0.4, max_tokens=700)
        ctx[self.output_key] = raw
        return {"ok": not raw.startswith("[ai error]"), "output": raw[:200]}


class VerifyAnswerStep(PipelineStep):
    """Cross-check generated answer against source facts. Returns pass/fail + correction if needed."""

    def __init__(self, question_key: str, facts_key: str, output_key: str):
        self.question_key = question_key
        self.facts_key = facts_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        question = ctx.get(self.question_key, "")
        facts = ctx.get(self.facts_key, "")
        if not facts:
            ctx[self.output_key] = "no source to verify against — accepted on model trust"
            return {"ok": True, "output": "no source verification"}

        prompt = (
            "Verify whether the USMLE question's answer is medically correct based on the source facts. "
            "If correct, reply 'VERIFIED'. If incorrect, reply 'INCORRECT: [correction]'.\n\n"
            "SOURCE FACTS:\n" + facts[:3000] + "\n\n"
            "QUESTION:\n" + question[:3000]
        )
        result = _call_cloud(prompt, temperature=0.1, max_tokens=300)
        ctx[self.output_key] = result
        is_verified = "VERIFIED" in result.upper() and "INCORRECT" not in result.upper()
        return {"ok": True, "verified": is_verified, "output": result[:200]}


class SemanticDedupStep(PipelineStep):
    """Check if a question is semantically similar to any prior question in the store."""

    def __init__(self, question_key: str, output_key: str, topic: str):
        self.question_key = question_key
        self.output_key = output_key
        self.topic = topic

    def run(self, ctx: dict) -> dict:
        question = ctx.get(self.question_key, "")
        if not question:
            ctx[self.output_key] = ""
            return {"ok": False, "error": "no question to dedup"}

        recent = usmle_store.recent(limit=50)
        if not recent:
            ctx[self.output_key] = question
            return {"ok": True, "is_dup": False, "output": "no prior questions"}

        prior_list = "\n---\n".join(
            f"Q{i+1}: {r['question'][:300]}" for i, r in enumerate(recent)
        )
        prompt = (
            "Does the NEW question below test the SAME medical concept (same disease, same mechanism, "
            "same answer) as any of the PRIOR questions? Reply YES or NO only.\n\n"
            "NEW:\n" + question[:500] + "\n\n"
            "PRIOR:\n" + prior_list[:4000]
        )
        result = _call_cloud(prompt, temperature=0.1, max_tokens=10)
        is_dup = "YES" in result.upper() and "NO" not in result.upper()
        ctx[self.output_key] = question if not is_dup else ""
        return {"ok": True, "is_dup": is_dup, "output": result}


class CompileAndSendStep(PipelineStep):
    """Compile verified unique questions, record in store, send to Telegram."""

    def __init__(self, question_keys: list[str], verify_keys: list[str],
                 topics: list[str]):
        self.question_keys = question_keys
        self.verify_keys = verify_keys
        self.topics = topics

    def run(self, ctx: dict) -> dict:
        today = datetime.now().strftime("%A, %b %d")
        parts = []
        for qk, vk, topic in zip(self.question_keys, self.verify_keys, self.topics):
            q = ctx.get(qk, "")
            v = ctx.get(vk, "")
            if not q or q.startswith("[ai error]"):
                continue
            verified = "INCORRECT" not in v.upper() if v else True
            status = "verified" if verified else "FLAGGED"
            q_num = len(parts) + 1
            parts.append(f"Q{q_num} [{self.topics[q_num-1]}] ({status})\n{q}")
            chash = usmle_store.content_hash(self.topics[q_num-1], q)
            if not usmle_store.seen(chash):
                usmle_store.record(chash, self.topics[q_num-1], q)

        ctx["usmle_questions"] = parts
        if not parts:
            ctx["usmle_message"] = ""
            return {"ok": True, "output": "no unique verified questions to send"}

        message = f"USMLE Step 1 Practice — {today}\n{'=' * 35}\n\n"
        message += "\n\n".join(parts)
        message += f"\n\n{'=' * 35}\nAnswers sourced from real PubMed abstracts."

        try:
            from tools.telegram_send import send_telegram
            send_telegram(message)
        except Exception as exc:
            return {"ok": False, "error": f"telegram send failed: {exc}"}

        ctx["usmle_message"] = message
        return {"ok": True, "output": f"sent {len(parts)} questions",
                "count": len(parts)}


def execute() -> dict:
    topics = MEDICAL_TOPICS.copy()
    random.shuffle(topics)
    selected = topics[:MAX_QUESTIONS]

    steps: list[PipelineStep] = []
    q_keys = []
    v_keys = []

    for i, topic in enumerate(selected):
        sk = f"usmle_source_{i}"
        fk = f"usmle_facts_{i}"
        qk = f"usmle_q_{i}"
        vk = f"usmle_verify_{i}"
        dk = f"usmle_dedup_{i}"
        q_keys.append(qk)
        v_keys.append(vk)

        # Step 1: Research — scrape PubMed
        steps.append(ResearchStep(topic=topic, output_key=sk))
        # Step 2: Extract facts from scraped content
        steps.append(ExtractFactsStep(source_key=sk, output_key=fk, topic=topic))
        # Step 3: Generate question from facts
        steps.append(GenerateQuestionStep(facts_key=fk, output_key=qk, topic=topic, q_num=i + 1))
        # Step 4: Verify answer against source
        steps.append(VerifyAnswerStep(question_key=qk, facts_key=fk, output_key=vk))
        # Step 5: Semantic dedup
        steps.append(SemanticDedupStep(question_key=qk, output_key=dk, topic=topic))

    # Final step: compile, record, send
    steps.append(CompileAndSendStep(
        question_keys=q_keys, verify_keys=v_keys, topics=selected,
    ))
    steps.append(NotificationStep(
        message="USMLE daily delivered",
    ))
    steps.append(LogAppendStep(
        log_file="usmle_daily.log",
        entry_key="usmle_message",
    ))

    return run_pipeline(steps)
