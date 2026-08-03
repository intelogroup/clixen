"""
Multi-step workflow pipeline engine.

Chains sequential steps sharing a context dict. Each step reads from
context, acts, writes back. Steps can be AI summarization, Gmail search,
iMessage/Telegram send, calendar check, etc.
"""

import logging
import os
from datetime import datetime, timezone

_log = logging.getLogger(__name__)


class PipelineStep:
    def run(self, ctx: dict) -> dict:
        raise NotImplementedError


class GmailSearchStep(PipelineStep):
    def __init__(self, query: str, max_results: int = 10):
        self.query = query
        self.max_results = max_results

    def run(self, ctx: dict) -> dict:
        from tools.gmail import list_emails
        result = list_emails(query=self.query, max_results=self.max_results)
        ctx["gmail_raw"] = result
        return {"ok": "error" not in result.lower(), "output": result}


class AiSummarizeStep(PipelineStep):
    def __init__(self, input_key: str, output_key: str, instruction: str = None):
        self.input_key = input_key
        self.output_key = output_key
        self.instruction = instruction or "Summarize the above in 2-3 sentences."

    def run(self, ctx: dict) -> dict:
        raw = ctx.get(self.input_key, "")
        prompt = f"{raw}\n\n{self.instruction}"
        text = _call_ai(prompt, max_tokens=512, temperature=0.3)
        ctx[self.output_key] = text
        return {"ok": not text.startswith("[error]"), "output": text}


class iMessageSendStep(PipelineStep):
    def __init__(self, recipient: str, message_key: str = None,
                 message: str = None, from_: str = None):
        self.recipient = recipient
        self.message_key = message_key
        self.message = message
        self.from_ = from_

    def run(self, ctx: dict) -> dict:
        from tools.imessage_search import send as send_imessage
        msg = self.message or ctx.get(self.message_key or "", "")
        if not msg:
            return {"ok": False, "error": "no message content"}
        result = send_imessage(to=self.recipient, message=str(msg)[:4000],
                               from_=self.from_)
        ctx["imessage_result"] = result
        return {"ok": "error" not in result.lower(), "output": result}


class TelegramSendStep(PipelineStep):
    def __init__(self, message_key: str = None, message: str = None,
                 chat_id: str = None):
        self.message_key = message_key
        self.message = message
        self.chat_id = chat_id

    def run(self, ctx: dict) -> dict:
        from tools.telegram_send import send_telegram
        msg = self.message or ctx.get(self.message_key or "", "")
        if not msg:
            return {"ok": False, "error": "no message content"}
        cid = self.chat_id or ctx.get("telegram_chat_id", "")
        result = send_telegram(str(msg)[:4000], chat_id=cid or None)
        ctx["telegram_result"] = result
        return {"ok": "error" not in result.lower(), "output": result}


class AiClassifyStep(PipelineStep):
    """Classify input text into a category using local AI."""

    def __init__(self, input_key: str, output_key: str, categories: list[str]):
        self.input_key = input_key
        self.output_key = output_key
        self.categories = categories

    def run(self, ctx: dict) -> dict:
        raw = ctx.get(self.input_key, "")
        cats = ", ".join(self.categories)
        prompt = f"Classify into one of: {cats}\n\n{raw}\n\nCategory:"
        label = _call_ai(prompt, max_tokens=20, temperature=0.1).lower()
        ctx[self.output_key] = label
        matched = any(c.lower() in label for c in self.categories)
        return {"ok": matched, "output": label, "category": label}


class ConditionStep(PipelineStep):
    """Branch on a context key value. Routes to next step or marks skip."""

    def __init__(self, key: str, match_values: list[str], if_match: str, if_not: str = None):
        self.key = key
        self.match_values = match_values
        self.if_match = if_match
        self.if_not = if_not

    def run(self, ctx: dict) -> dict:
        val = str(ctx.get(self.key, "")).lower()
        matched = any(v.lower() in val for v in self.match_values)
        nxt = self.if_match if matched else self.if_not
        ctx["_next_step"] = nxt
        label = "match" if matched else "no-match"
        msg = "branch: {}={} -> {}".format(self.key, label, nxt)
        return {"ok": True, "output": msg}


class WebhookCallStep(PipelineStep):
    def __init__(self, url_key: str = None, url: str = None, payload: dict = None):
        self.url_key = url_key
        self.url = url
        self.payload = payload or {}

    def run(self, ctx: dict) -> dict:
        import httpx
        target = self.url or ctx.get(self.url_key or "", "")
        if not target:
            return {"ok": False, "error": "no webhook URL"}
        try:
            resp = httpx.post(target, json=self.payload, timeout=15)
            ctx["webhook_result"] = f"status={resp.status_code}"
            return {"ok": resp.is_success, "output": f"status={resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class NotificationStep(PipelineStep):
    def __init__(self, message_key: str = None, message: str = None, level: str = "info"):
        self.message_key = message_key
        self.message = message
        self.level = level

    def run(self, ctx: dict) -> dict:
        from store import workflow_store
        msg = self.message or ctx.get(self.message_key or "", "")
        workflow_store.add_notification(message=str(msg)[:200], source="pipeline", level=self.level)
        return {"ok": True, "output": "notification sent"}


class WebScrapeStep(PipelineStep):
    """Fetch URL, return text content."""

    def __init__(self, url: str, output_key: str = "scraped_content", css_selector: str = None):
        self.url = url
        self.output_key = output_key
        self.css_selector = css_selector

    def run(self, ctx: dict) -> dict:
        import httpx
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._text = []
                self._skip = False
            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style"):
                    self._skip = True
            def handle_endtag(self, tag):
                if tag in ("script", "style"):
                    self._skip = False
            def handle_data(self, data):
                if not self._skip:
                    self._text.append(data.strip())

        try:
            resp = httpx.get(self.url, timeout=20, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            parser = _TextExtractor()
            parser.feed(resp.text)
            text = " ".join(t for t in parser._text if t)[:8000]
            ctx[self.output_key] = text
            return {"ok": True, "output": f"{len(text)} chars scraped"}
        except Exception as e:
            ctx[self.output_key] = f"[scrape error] {e}"
            return {"ok": False, "error": str(e)}


class AiExtractStep(PipelineStep):
    """Extract structured data from text via AI."""

    def __init__(self, input_key: str, output_key: str, schema_description: str):
        self.input_key = input_key
        self.output_key = output_key
        self.schema_description = schema_description

    def run(self, ctx: dict) -> dict:
        raw = ctx.get(self.input_key, "")
        prompt = (
            f"Extract structured data from the text below.\n"
            f"Schema: {self.schema_description}\n"
            f"Return valid JSON only, no explanation.\n\n{raw[:4000]}"
        )
        extracted = _call_ai(prompt, max_tokens=300, temperature=0.1)
        ctx[self.output_key] = extracted
        failed = extracted.startswith("[error]") or (extracted.startswith("{") and '"error"' in extracted[:50])
        return {"ok": not failed,
                "output": extracted[:200]}


class SheetAppendStep(PipelineStep):
    """Append row to Google Sheet."""

    def __init__(self, spreadsheet_id: str, row_values_key: str, sheet_name: str = "Sheet1"):
        self.spreadsheet_id = spreadsheet_id
        self.row_values_key = row_values_key
        self.sheet_name = sheet_name

    def run(self, ctx: dict) -> dict:
        raw = ctx.get(self.row_values_key, "")
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(values, dict):
                values = list(values.values())
            if not isinstance(values, list):
                values = [str(values)]
        except Exception:
            values = [str(raw)]
        try:
            import httpx
            resp = httpx.post(
                f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/"
                f"{self.sheet_name}!A1:append?valueInputOption=USER_ENTERED",
                json={"values": [values]},
                headers={"Authorization": f"Bearer {_get_gmail_token()}"},
                timeout=15,
            )
            ok = resp.is_success
            ctx["sheet_result"] = f"status={resp.status_code}"
            return {"ok": ok, "output": f"appended to sheet: status={resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _get_gmail_token():
    """Reuse Gmail token for Sheets API."""
    import json as _json, os as _os
    token_path = _os.path.expanduser("~/.gmail_token.json")
    try:
        with open(token_path) as f:
            return _json.load(f).get("token", "")
    except Exception:
        return ""


class FileWatchStep(PipelineStep):
    """Watch directory for new files matching pattern."""

    def __init__(self, directory: str, pattern: str = "*", output_key: str = "new_files",
                 max_files: int = 5):
        self.directory = directory
        self.pattern = pattern
        self.output_key = output_key
        self.max_files = max_files

    def run(self, ctx: dict) -> dict:
        import glob, os
        seen = set(ctx.get("_seen_files", []))
        all_files = sorted(glob.glob(os.path.join(self.directory, self.pattern)))
        new = [f for f in all_files if f not in seen][:self.max_files]
        ctx["_seen_files"] = list(seen | set(all_files))
        ctx[self.output_key] = new
        return {"ok": True, "output": f"{len(new)} new files" if new else "no new files",
                "has_new": bool(new)}


def _call_ai(prompt: str, model: str = None, max_tokens: int = 512,
             temperature: float = 0.5) -> str:
    """Call local Ollama and return text response."""
    model = model or os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b")
    try:
        import httpx
        resp = httpx.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": model, "prompt": str(prompt), "stream": False,
                   "options": {"num_predict": max_tokens, "temperature": temperature}},
            timeout=60,
        )
        return resp.json().get("response", "").strip()
    except Exception as e:
        return f"[error] {e}"


class AiGenerateStep(PipelineStep):
    """Freeform AI text generation."""

    def __init__(self, prompt_key: str = None, prompt: str = None,
                 output_key: str = "ai_output", max_tokens: int = 512):
        self.prompt_key = prompt_key
        self.prompt = prompt
        self.output_key = output_key
        self.max_tokens = max_tokens

    def run(self, ctx: dict) -> dict:
        p = self.prompt or ctx.get(self.prompt_key or "", "")
        if not p:
            return {"ok": False, "error": "no prompt"}
        text = _call_ai(p, max_tokens=self.max_tokens)
        ctx[self.output_key] = text
        return {"ok": not text.startswith("[error]"), "output": text[:200]}


class OcrStep(PipelineStep):
    """OCR an image file via local tesseract or MLX."""

    def __init__(self, file_key: str, output_key: str = "ocr_text"):
        self.file_key = file_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        filepath = ctx.get(self.file_key, "")
        if isinstance(filepath, list):
            filepath = filepath[0] if filepath else ""
        if not filepath or not os.path.isfile(filepath):
            return {"ok": False, "error": f"file not found: {filepath}"}
        try:
            import subprocess as _sp
            result = _sp.run(["tesseract", filepath, "stdout"], capture_output=True,
                             text=True, timeout=30)
            text = result.stdout.strip()
            ctx[self.output_key] = text
            return {"ok": bool(text), "output": f"{len(text)} chars OCR'd"}
        except FileNotFoundError:
            ctx[self.output_key] = "[ocr error] tesseract not installed"
            return {"ok": False, "error": "tesseract not installed"}
        except Exception as e:
            ctx[self.output_key] = f"[ocr error] {e}"
            return {"ok": False, "error": str(e)}


class DiffCompareStep(PipelineStep):
    """Compare new content against stored previous version."""

    def __init__(self, new_key: str, store_key: str, output_key: str = "diff"):
        self.new_key = new_key
        self.store_key = store_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import difflib
        new = ctx.get(self.new_key, "")
        old = ctx.get(self.store_key, "")
        if isinstance(new, list):
            new = "\n".join(new)
        if isinstance(old, list):
            old = "\n".join(old)
        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
        ctx[self.output_key] = "\n".join(diff[:40])
        ctx[self.store_key] = new  # update stored version
        changed = bool(diff)
        return {"ok": changed, "output": f"{len(diff)} lines changed" if changed else "no changes"}


class EmailSendStep(PipelineStep):
    def __init__(self, to: str, subject_key: str = None, subject: str = None,
                 body_key: str = None, body: str = None):
        self.to = to
        self.subject_key = subject_key
        self.subject = subject
        self.body_key = body_key
        self.body = body

    def run(self, ctx: dict) -> dict:
        from tools.registry import EXECUTORS
        subj = self.subject or ctx.get(self.subject_key or "", "Pipeline result")
        b = self.body or ctx.get(self.body_key or "", "")
        result = EXECUTORS["send_email"]({"to": self.to, "subject": str(subj)[:200], "body": str(b)[:10000]})
        ok = "error" not in str(result).lower()
        return {"ok": ok, "output": str(result)[:200]}


class SleepStep(PipelineStep):
    """Pause pipeline for N seconds."""

    def __init__(self, seconds: int = 60):
        self.seconds = seconds

    def run(self, ctx: dict) -> dict:
        import time
        time.sleep(self.seconds)
        return {"ok": True, "output": f"slept {self.seconds}s"}


class AiCompareStep(PipelineStep):
    """AI compare two texts and describe differences."""

    def __init__(self, old_key: str, new_key: str, output_key: str = "ai_diff",
                 instruction: str = "Summarize key differences between old and new:"):
        self.old_key = old_key
        self.new_key = new_key
        self.output_key = output_key
        self.instruction = instruction

    def run(self, ctx: dict) -> dict:
        old = str(ctx.get(self.old_key, ""))[:2000]
        new = str(ctx.get(self.new_key, ""))[:2000]
        prompt = f"{self.instruction}\n\nOLD:\n{old}\n\nNEW:\n{new}\n\nDifferences:"
        text = _call_ai(prompt, max_tokens=300, temperature=0.3)
        ctx[self.output_key] = text
        return {"ok": not text.startswith("[error]"), "output": text[:200]}


class LogAppendStep(PipelineStep):
    """Append entry to a local log file."""

    def __init__(self, log_file: str, entry_key: str = None, entry: str = None):
        self.log_file = log_file
        self.entry_key = entry_key
        self.entry = entry

    def run(self, ctx: dict) -> dict:
        from datetime import datetime
        msg = self.entry or ctx.get(self.entry_key or "", "")
        line = f"[{datetime.now().isoformat()}] {msg}\n"
        try:
            with open(self.log_file, "a") as f:
                f.write(line)
            return {"ok": True, "output": f"logged to {self.log_file}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ── File I/O step types ──────────────────────────────────────────────────

class WriteFileStep(PipelineStep):
    """Write content to a file. Creates parent dirs."""

    def __init__(self, path: str, content_key: str = None, content: str = None,
                 output_key: str = "write_file_path"):
        self.path = path
        self.content_key = content_key
        self.content = content
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        c = self.content or str(ctx.get(self.content_key or "", ""))
        p = os.path.expanduser(self.path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        try:
            with open(p, "w") as f:
                f.write(c)
            ctx[self.output_key] = p
            return {"ok": True, "output": f"wrote {len(c)}b to {p}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class ReadFileStep(PipelineStep):
    """Read file content into context."""

    def __init__(self, path: str = None, path_key: str = None,
                 output_key: str = "file_content", max_bytes: int = 10000):
        self.path = path
        self.path_key = path_key
        self.output_key = output_key
        self.max_bytes = max_bytes

    def run(self, ctx: dict) -> dict:
        p = self.path or ctx.get(self.path_key or "", "")
        if isinstance(p, list):
            p = p[0] if p else ""
        p = os.path.expanduser(str(p))
        if not p or not os.path.isfile(p):
            return {"ok": False, "error": f"file not found: {p}"}
        try:
            with open(p, "r", errors="replace") as f:
                text = f.read(self.max_bytes)
            ctx[self.output_key] = text
            return {"ok": True, "output": f"read {len(text)}b from {p}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class CopyFileStep(PipelineStep):
    """Copy file from src to dest."""

    def __init__(self, src_key: str = None, src: str = None,
                 dest: str = None, dest_key: str = "copied_path"):
        self.src_key = src_key
        self.src = src
        self.dest = dest
        self.dest_key = dest_key

    def run(self, ctx: dict) -> dict:
        import shutil
        s = self.src or ctx.get(self.src_key or "", "")
        if isinstance(s, list):
            s = s[0] if s else ""
        s = os.path.expanduser(str(s))
        d = os.path.expanduser(str(self.dest))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        try:
            shutil.copy2(s, d)
            ctx[self.dest_key] = d
            return {"ok": True, "output": f"copied {s} -> {d}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class MoveFileStep(PipelineStep):
    """Move/rename file."""

    def __init__(self, src_key: str = None, src: str = None,
                 dest: str = None):
        self.src_key = src_key
        self.src = src
        self.dest = dest

    def run(self, ctx: dict) -> dict:
        import shutil
        s = self.src or ctx.get(self.src_key or "", "")
        if isinstance(s, list):
            s = s[0] if s else ""
        s = os.path.expanduser(str(s))
        d = os.path.expanduser(str(self.dest))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        try:
            shutil.move(s, d)
            return {"ok": True, "output": f"moved {s} -> {d}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class MkdirStep(PipelineStep):
    """Create directory if not exists."""

    def __init__(self, path: str, output_key: str = "mkdir_path"):
        self.path = path
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        p = os.path.expanduser(self.path)
        os.makedirs(p, exist_ok=True)
        ctx[self.output_key] = p
        return {"ok": True, "output": f"mkdir {p}"}


class SvgGenerateStep(PipelineStep):
    """Generate SVG image via local AI."""

    def __init__(self, prompt: str, output_key: str = "svg_code",
                 width: int = 800, height: int = 600):
        self.prompt = prompt
        self.output_key = output_key
        self.width = width
        self.height = height

    def run(self, ctx: dict) -> dict:
        sys_prompt = (
            f"Generate a self-contained SVG ({self.width}x{self.height}). "
            "Return ONLY valid SVG code inside ```svg ... ```. "
            f"Prompt: {self.prompt}"
        )
        try:
            import httpx
            import re
            resp = httpx.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b"), "prompt": sys_prompt, "stream": False,
                       "options": {"num_predict": 2000, "temperature": 0.4}},
                timeout=120,
            )
            raw = resp.json().get("response", "")
            m = re.search(r"```svg\s*\n(.+?)\n```", raw, re.DOTALL)
            svg = m.group(1).strip() if m else raw.strip()
            if not svg.startswith("<svg"):
                svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.width} {self.height}">{svg}</svg>'
            ctx[self.output_key] = svg
            return {"ok": True, "output": f"SVG generated ({len(svg)} chars)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class SvgToPngStep(PipelineStep):
    """Convert SVG string or file to PNG via rsvg-convert."""

    def __init__(self, svg_key: str = None, svg_path_key: str = None,
                 output_path: str = None, output_key: str = "png_path",
                 width: int = None):
        self.svg_key = svg_key
        self.svg_path_key = svg_path_key
        self.output_path = output_path
        self.output_key = output_key
        self.width = width

    def run(self, ctx: dict) -> dict:
        import tempfile, subprocess
        svg_str = ctx.get(self.svg_key or "", "") if self.svg_key else ""
        svg_file = ctx.get(self.svg_path_key or "", "") if self.svg_path_key else ""

        if svg_str and not svg_file:
            tmp = tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w")
            tmp.write(svg_str)
            svg_file = tmp.name
            tmp.close()

        if not svg_file or not os.path.isfile(str(svg_file)):
            if isinstance(svg_file, list):
                svg_file = svg_file[0] if svg_file else ""
            if not svg_file:
                return {"ok": False, "error": "no SVG source"}

        out = self.output_path or svg_file.replace(".svg", ".png")
        try:
            cmd = ["rsvg-convert", str(svg_file), "-o", out]
            if self.width:
                cmd += ["-w", str(self.width)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            ctx[self.output_key] = out
            return {"ok": True, "output": f"PNG saved: {out}"}
        except FileNotFoundError:
            return {"ok": False, "error": "rsvg-convert not installed (brew install librsvg)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class ImageMagickStep(PipelineStep):
    """Run ImageMagick on an image file."""

    def __init__(self, src_key: str = None, src: str = None,
                 operation: str = "resize", arg: str = "50%",
                 output_path: str = None, output_key: str = "magick_output"):
        self.src_key = src_key
        self.src = src
        self.operation = operation
        self.arg = arg
        self.output_path = output_path
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import subprocess
        s = self.src or ctx.get(self.src_key or "", "")
        if isinstance(s, list):
            s = s[0] if s else ""
        s = os.path.expanduser(str(s))
        if not os.path.isfile(s):
            return {"ok": False, "error": f"file not found: {s}"}
        out = self.output_path or s.rsplit(".", 1)[0] + "_edited." + s.rsplit(".", 1)[-1]
        try:
            if self.operation == "resize":
                subprocess.run(["magick", s, "-resize", self.arg, out], check=True, capture_output=True, timeout=30)
            elif self.operation == "crop":
                subprocess.run(["magick", s, "-crop", self.arg, out], check=True, capture_output=True, timeout=30)
            elif self.operation == "convert":
                subprocess.run(["magick", s, out], check=True, capture_output=True, timeout=30)
            elif self.operation == "grayscale":
                subprocess.run(["magick", s, "-colorspace", "gray", out], check=True, capture_output=True, timeout=30)
            elif self.operation == "thumbnail":
                subprocess.run(["magick", s, "-thumbnail", self.arg, out], check=True, capture_output=True, timeout=30)
            elif self.operation == "border":
                subprocess.run(["magick", s, "-border", self.arg, "-bordercolor", "white", out], check=True,
                               capture_output=True, timeout=30)
            elif self.operation == "rotate":
                subprocess.run(["magick", s, "-rotate", self.arg, out], check=True, capture_output=True, timeout=30)
            else:
                subprocess.run(["magick", s, out], check=True, capture_output=True, timeout=30)
            ctx[self.output_key] = out
            return {"ok": True, "output": f"{self.operation} -> {out}"}
        except FileNotFoundError:
            return {"ok": False, "error": "ImageMagick not installed (brew install imagemagick)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class ImageGenerateStep(PipelineStep):
    """Generate PNG image via Ollama multimodal model."""

    def __init__(self, prompt: str, output_path: str = None,
                 output_key: str = "image_path"):
        self.prompt = prompt
        self.output_path = output_path
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import tempfile
        out = self.output_path or tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        try:
            import httpx
            resp = httpx.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": "llama3.2-vision:11b", "prompt": self.prompt,
                       "stream": False, "options": {"num_predict": 100}},
                timeout=120,
            )
            data = resp.json()
            b64 = data.get("response", "")
            if b64:
                import base64
                raw = base64.b64decode(b64)
                with open(out, "wb") as f:
                    f.write(raw)
                ctx[self.output_key] = out
                return {"ok": True, "output": f"image saved: {out}"}
            return {"ok": False, "error": "no image data in response"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class TemplateRenderStep(PipelineStep):
    """Render a Jinja2 template with context data."""

    def __init__(self, template_key: str = None, template: str = None,
                 template_path: str = None,
                 output_key: str = "rendered_output"):
        self.template_key = template_key
        self.template = template
        self.template_path = template_path
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        tpl = self.template or ctx.get(self.template_key or "", "")
        if not tpl and self.template_path:
            p = os.path.expanduser(self.template_path)
            if not os.path.isfile(p):
                return {"ok": False, "error": f"template not found: {p}"}
            with open(p) as f:
                tpl = f.read()
        if not tpl:
            return {"ok": False, "error": "no template content"}
        try:
            from jinja2 import Template
            rendered = Template(tpl).render(ctx)
            ctx[self.output_key] = rendered
            return {"ok": True, "output": f"rendered ({len(rendered)} chars)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class LsStep(PipelineStep):
    """List files in directory into context."""

    def __init__(self, directory: str, pattern: str = "*",
                 output_key: str = "ls_files", max_files: int = 20):
        self.directory = directory
        self.pattern = pattern
        self.output_key = output_key
        self.max_files = max_files

    def run(self, ctx: dict) -> dict:
        import glob
        d = os.path.expanduser(self.directory)
        files = sorted(glob.glob(os.path.join(d, self.pattern)))[:self.max_files]
        ctx[self.output_key] = files
        return {"ok": True, "output": f"{len(files)} files in {d}"}


STEP_REGISTRY = {
    "gmail_search": GmailSearchStep,
    "ai_summarize": AiSummarizeStep,
    "imessage_send": iMessageSendStep,
    "telegram_send": TelegramSendStep,
    "ai_classify": AiClassifyStep,
    "condition": ConditionStep,
    "webhook_call": WebhookCallStep,
    "notification": NotificationStep,
    "web_scrape": WebScrapeStep,
    "ai_extract": AiExtractStep,
    "sheet_append": SheetAppendStep,
    "file_watch": FileWatchStep,
    "ai_generate": AiGenerateStep,
    "ocr": OcrStep,
    "diff_compare": DiffCompareStep,
    "email_send": EmailSendStep,
    "sleep": SleepStep,
    "ai_compare": AiCompareStep,
    "log_append": LogAppendStep,
    "write_file": WriteFileStep,
    "read_file": ReadFileStep,
    "copy_file": CopyFileStep,
    "move_file": MoveFileStep,
    "mkdir": MkdirStep,
    "svg_generate": SvgGenerateStep,
    "svg_to_png": SvgToPngStep,
    "image_magick": ImageMagickStep,
    "image_generate": ImageGenerateStep,
    "template_render": TemplateRenderStep,
    "ls": LsStep,
}


def run_pipeline(steps: list[PipelineStep]) -> dict:
    """Run a list of pipeline steps sequentially, sharing context."""
    ctx: dict = {}
    results = []
    i = 0
    while i < len(steps):
        step = steps[i]
        try:
            result = step.run(ctx)
        except Exception as e:
            _log.exception("pipeline step %d failed: %s", i, e)
            result = {"ok": False, "error": str(e)}
        results.append({"step": type(step).__name__, "result": result})
        next_override = ctx.pop("_next_step", None)
        if next_override is not None:
            for j, s in enumerate(steps):
                if type(s).__name__ == next_override or next_override in str(type(s).__name__).lower():
                    i = j
                    break
            else:
                break
        else:
            i += 1
        if not result.get("ok") and not isinstance(step, ConditionStep):
            break
    return {"context": ctx, "steps": results}


def step_from_config(cfg: dict) -> PipelineStep:
    kind = cfg.pop("kind", "")
    cls = STEP_REGISTRY.get(kind)
    if not cls:
        raise ValueError(f"unknown step kind: {kind}")
    return cls(**cfg)
