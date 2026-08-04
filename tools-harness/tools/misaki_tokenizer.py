"""Load kokoro_onnx.Kokoro without ever importing phonemizer/espeak-ng (GPL-3.0).

kokoro_onnx/__init__.py does `from .tokenizer import Tokenizer` at module import
time, and tokenizer.py does `import phonemizer` at ITS module import time —
unconditionally, before any code of ours runs. Kokoro.__init__ then always
constructs that real Tokenizer() first (which ctypes.cdll.LoadLibrary()s the
actual espeak-ng shared library), and only afterwards could a caller replace
kokoro.tokenizer — by which point the GPL binary has already been loaded and
executed in-process. Swapping the tokenizer *after* construction does not
remove that.

The only way to avoid combining with the GPL code at all is to stop it from
being imported in the first place: stub `phonemizer` / `espeakng_loader` in
sys.modules with harmless fakes before `import kokoro_onnx` ever runs, and
monkeypatch kokoro_onnx.Tokenizer (the name kokoro_onnx/__init__.py copied into
its own namespace — Kokoro.__init__ resolves the bare name there, not via
kokoro_onnx.tokenizer.Tokenizer) to our misaki-backed class before Kokoro() is
constructed. Use load_kokoro() below instead of `from kokoro_onnx import
Kokoro` directly, everywhere kokoro_onnx is loaded.

misaki[en]'s spacy model (en_core_web_sm) is pinned in pyproject.toml as a
direct wheel URL — without it, first G2P() call would try to network-download
it, which breaks offline/sandboxed installs (see pyproject.toml comment).

Known gap: words outside misaki's dictionary (rare/technical terms, some
proper nouns) come back as U+2753 and get silently dropped by the vocab
filter, same as any other out-of-vocab symbol — no espeak fallback.
"""

import sys
import types

MAX_PHONEME_LENGTH = 510

_STUBBED = False


def _stub_gpl_deps() -> None:
    """Register harmless fake modules so kokoro_onnx.tokenizer's unconditional
    `import phonemizer` / `import espeakng_loader` never load the real GPL-3.0
    packages or the espeak-ng shared library."""
    global _STUBBED
    if _STUBBED:
        return

    espeakng_loader = types.ModuleType("espeakng_loader")
    espeakng_loader.get_data_path = lambda: ""
    espeakng_loader.get_library_path = lambda: ""
    sys.modules["espeakng_loader"] = espeakng_loader

    wrapper_mod = types.ModuleType("phonemizer.backend.espeak.wrapper")

    class _FakeEspeakWrapper:
        @staticmethod
        def set_data_path(_path):
            pass

        @staticmethod
        def set_library(_path):
            pass

    wrapper_mod.EspeakWrapper = _FakeEspeakWrapper

    espeak_mod = types.ModuleType("phonemizer.backend.espeak")
    espeak_mod.wrapper = wrapper_mod
    backend_mod = types.ModuleType("phonemizer.backend")
    backend_mod.espeak = espeak_mod
    phonemizer_mod = types.ModuleType("phonemizer")
    phonemizer_mod.backend = backend_mod
    phonemizer_mod.phonemize = lambda *a, **k: ""

    sys.modules["phonemizer"] = phonemizer_mod
    sys.modules["phonemizer.backend"] = backend_mod
    sys.modules["phonemizer.backend.espeak"] = espeak_mod
    sys.modules["phonemizer.backend.espeak.wrapper"] = wrapper_mod

    _STUBBED = True


class MisakiTokenizer:
    """Drop-in for kokoro_onnx.tokenizer.Tokenizer, backed by misaki (Apache-2.0).

    Matches Tokenizer's constructor signature (espeak_config, vocab) since
    Kokoro.__init__/from_session call it positionally, but espeak_config is
    unused — misaki needs no espeak-ng.
    """

    def __init__(self, espeak_config=None, vocab: dict = None):
        from kokoro_onnx.config import DEFAULT_VOCAB

        self.vocab = vocab or DEFAULT_VOCAB
        from misaki import en as _misaki_en

        self._g2p = _misaki_en.G2P(trf=False, british=False, fallback=None)

    @staticmethod
    def normalize_text(text) -> str:
        return text.strip()

    def tokenize(self, phonemes):
        if len(phonemes) > MAX_PHONEME_LENGTH:
            raise ValueError(
                f"text is too long, must be less than {MAX_PHONEME_LENGTH} phonemes"
            )
        return [i for i in map(self.vocab.get, phonemes) if i is not None]

    def phonemize(self, text, lang="en-us", norm=True) -> str:
        if norm:
            text = self.normalize_text(text)
        phonemes, _tokens = self._g2p(text)
        phonemes = "".join(filter(lambda p: p in self.vocab, phonemes))
        return phonemes.strip()


def load_kokoro(onnx_path: str, voices_path: str, **kwargs):
    """Construct a kokoro_onnx.Kokoro instance that never loads phonemizer/espeak-ng.

    kokoro_onnx.__init__ binds its own `Tokenizer` name from `.tokenizer` at
    package-import time; Kokoro.__init__ looks up that name (kokoro_onnx.Tokenizer,
    not kokoro_onnx.tokenizer.Tokenizer) each call. Patching it here means the real
    espeak-backed Tokenizer class is defined (against our harmless stubs) but is
    never instantiated — so no GPL-3.0 code actually runs.
    """
    _stub_gpl_deps()
    import kokoro_onnx

    kokoro_onnx.Tokenizer = MisakiTokenizer
    return kokoro_onnx.Kokoro(onnx_path, voices_path, **kwargs)
