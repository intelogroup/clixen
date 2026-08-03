"""
USMLE Step 1 Questions Job
===========================

Generates USMLE Step 1 practice questions using Gemma4's medical knowledge.
Sends questions directly to Telegram without fetching external content.

Configuration (via .env):
    USMLE_MAX_QUESTIONS          Max questions to generate per run (default: 2)
    USMLE_SUMMARY_MODEL          Ollama model to use (default: gemma4:latest)
"""

from __future__ import annotations

import os
import re
import sys
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.telegram_send import send_telegram
from jobs import job_queue
from store import usmle_store

OLLAMA_URL = "http://localhost:11434/api/chat"
SUMMARY_MODEL = os.environ.get("USMLE_SUMMARY_MODEL", "deepseek/deepseek-v4-flash")
OLLAMA_TIMEOUT = int(os.environ.get("USMLE_OLLAMA_TIMEOUT", "60"))
MAX_QUESTIONS = int(os.environ.get("USMLE_MAX_QUESTIONS", "2"))
MAX_CACHE_SIZE = 200

_WS_RE = re.compile(r"\s+")

# Medical topics for question generation
MEDICAL_TOPICS = [
    "pharmacology",
    "pathology", 
    "physiology",
    "anatomy",
    "microbiology",
    "biochemistry",
    "immunology",
    "cardiology",
    "pulmonology",
    "gastroenterology",
    "neurology",
    "psychiatry",
    "dermatology",
    "orthopedics",
    "obstetrics",
    "pediatrics",
]

def _clean_generated_question(raw: str, question_num: int) -> str:
    """Clean up the generated question to ensure proper format."""
    # Remove any leading "Q:" or "Question:" to avoid duplication
    cleaned = re.sub(r'^(q:|question:)\s*', '', raw, flags=re.IGNORECASE).strip()
    
    # Check for required elements
    required = ["A)", "B)", "C)", "D)", "E)", "Answer:"]
    if not all(token in cleaned for token in required):
        return _fallback_question("general medicine", question_num)
    
    # Add question number with Telegram-friendly formatting
    final = f"📝 Q{question_num}:\n{cleaned}"
    return final

def _generate_question(topic: str, question_num: int) -> str:
    """Generate a single USMLE Step 1 question using Gemma4."""
    prompt = f"""Generate one USMLE Step 1 style multiple choice question on {topic}.

Write a clinical vignette, then provide 5 answer choices (A through E), indicate the correct answer, and give a brief 1-2 sentence explanation.

Output exactly in this format (no extra text):
Q: [clinical vignette]
A) [choice]
B) [choice]
C) [choice]
D) [choice]
E) [choice]
Answer: [single letter A-E]
Explanation: [1-2 sentences]

Keep total output under 250 words. Do not mention AI, model, or generation process."""

    for attempt in range(2):
        try:
            import requests
            
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": SUMMARY_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 500, "temperature": 0.3, "think": False},
                },
                timeout=OLLAMA_TIMEOUT,
            )
            if resp.ok:
                data = resp.json()
                raw = data.get("message", {}).get("content", "").strip()
                if raw and "Answer:" in raw and "Explanation:" in raw:
                    return _clean_generated_question(raw, question_num)
                print(f"[usmle] Q{question_num} incomplete (attempt {attempt+1}), retrying...")
        except Exception as e:
            print(f"[usmle] LLM error for {topic}: {e}")
    
    return _fallback_question(topic, question_num)

def _fallback_question(topic: str, question_num: int) -> str:
    """Deterministic fallback question when generation fails."""
    question_bank = [
        {
            "q": (
                "A 45-year-old man presents with chest pain that radiates to his left arm. "
                "ECG shows ST-segment elevation in leads II, III, and aVF. "
                "Which coronary artery is most likely occluded?"
            ),
            "choices": [
                "Left anterior descending",
                "Left circumflex",
                "Right coronary artery",
                "Left main coronary artery",
                "Posterior descending artery",
            ],
            "answer": "C",
            "explanation": "ST elevation in II, III, aVF indicates inferior wall MI, supplied by RCA.",
        },
        {
            "q": (
                "A 25-year-old woman presents with fatigue and pallor. "
                "Laboratory studies show Hb 9 g/dL, MCV 110 fL, low reticulocyte count. "
                "Which vitamin deficiency is most likely?"
            ),
            "choices": [
                "Vitamin B12",
                "Vitamin B6",
                "Vitamin C",
                "Vitamin D",
                "Folate",
            ],
            "answer": "A",
            "explanation": "Macrocytic anemia with low reticulocytes suggests B12 or folate deficiency.",
        },
        {
            "q": (
                "A patient taking haloperidol develops rigidity, tremor, and elevated temperature. "
                "Which receptor blockade is primarily responsible for these extrapyramidal symptoms?"
            ),
            "choices": [
                "D2 receptors in nigrostriatal pathway",
                "5-HT2 receptors in cortex",
                "H1 receptors in CNS",
                "Alpha-1 receptors in brainstem",
                "Muscarinic receptors in basal ganglia",
            ],
            "answer": "A",
            "explanation": "D2 blockade in nigrostriatal pathway causes EPS; treat with benztropine.",
        },
    ]
    
    item = question_bank[question_num % len(question_bank)]
    return (
        f"📝 Q{question_num}:\n"
        f"Q: {item['q']}\n"
        f"A) {item['choices'][0]}\n"
        f"B) {item['choices'][1]}\n"
        f"C) {item['choices'][2]}\n"
        f"D) {item['choices'][3]}\n"
        f"E) {item['choices'][4]}\n"
        f"✅ Answer: {item['answer']}\n"
        f"💡 Explanation: {item['explanation']}"
    )

def run_briefing() -> str:
    """Generate USMLE questions and send to Telegram, skipping ones already sent before."""
    today = datetime.now().strftime("%A, %b %d")
    print(f"[usmle] generating USMLE questions for {today}")

    import random
    topics = list(MEDICAL_TOPICS)
    random.shuffle(topics)

    formatted = []
    i = 0
    for topic in topics:
        if len(formatted) >= MAX_QUESTIONS:
            break
        i += 1
        print(f"[usmle] generating Q{i} candidate on {topic}")
        question = _generate_question(topic, len(formatted) + 1)
        chash = usmle_store.content_hash(topic, question)
        if usmle_store.seen(chash):
            print(f"[usmle] dup on {topic}, skipping")
            continue
        usmle_store.record(chash, topic, question)
        formatted.append(question)

    if not formatted:
        print("[usmle] all candidates were dupes, nothing to send")
        return ""

    message = f"📚 USMLE Step 1 Practice - {today}\n"
    message += "━" * 30 + "\n\n"
    message += "\n\n".join(formatted)
    message += f"\n\n{'━' * 30}\n"
    message += "💡 Study smart!"
    
    try:
        send_telegram(message)
        print(f"[usmle] sent {len(formatted)} unique questions to Telegram")
    except Exception as e:
        print(f"[usmle] telegram error: {e}")
        raise
    
    return message

def run_as_job(params: dict, job_id: str) -> None:
    """Entry point called by the worker."""
    job_queue.init()
    job_queue.checkpoint(job_id, "started")
    run_briefing()

if __name__ == "__main__":
    result = run_briefing()
    print("\n--- QUESTIONS PREVIEW ---")
    print(result[:1000])
