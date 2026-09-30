"""ARC-hosted model catalog built from real ARC filesystem metadata.

Inventory is discovered with read-only shell commands (``find``/``head``/``grep``)
that are safe on a login node. Nothing is hardcoded about which models exist;
sizes come from each model's own ``config.json`` and safetensors index. GPU
memory figures are estimates and are labelled as such.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MODELS_ROOT = "/common/data/models"
INVENTORY_COMMAND = (
    f"find {MODELS_ROOT} -maxdepth 3 -name config.json 2>/dev/null | while read -r f; do "
    'd=$(dirname "$f"); echo "@@MODEL $d"; head -c 8000 "$f"; echo; echo "@@INDEX"; '
    'grep -o \'"total_size": *[0-9]*\' "$d/model.safetensors.index.json" 2>/dev/null | head -1; '
    'if [ -f "$d/LICENSE" ] || [ -f "$d/LICENSE.txt" ]; then echo "@@LICENSE present"; fi; echo "@@END"; done'
)

# Nominal per-GPU memory (GB). ARC hardware variants should be verified; users may override.
NOMINAL_GPU_MEMORY_GB = {"t4": 16, "v100": 32, "a30": 24, "l40s": 48, "a100": 80, "h100": 80, "h200": 141}

# Architecture class names vLLM documents support for (subset); anything else is "unknown", never "no".
VLLM_ARCHITECTURES = {
    "LlamaForCausalLM", "MistralForCausalLM", "MixtralForCausalLM", "Qwen2ForCausalLM", "Qwen3ForCausalLM",
    "Qwen2MoeForCausalLM", "Qwen3MoeForCausalLM", "GemmaForCausalLM", "Gemma2ForCausalLM", "Gemma3ForCausalLM",
    "Phi3ForCausalLM", "PhiForCausalLM", "DeepseekV2ForCausalLM", "DeepseekV3ForCausalLM", "GPTNeoXForCausalLM",
    "FalconForCausalLM", "GPT2LMHeadModel", "GptOssForCausalLM", "InternLM2ForCausalLM", "OlmoForCausalLM",
    "Olmo2ForCausalLM", "CohereForCausalLM", "StableLmForCausalLM", "GraniteForCausalLM",
}

_PARAMS_RE = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*([BbMm])(?![A-Za-z])")
_FAMILIES = ["llama", "mistral", "mixtral", "qwen", "gemma", "phi", "deepseek", "gpt-oss", "glm", "kimi",
             "falcon", "olmo", "granite", "internlm", "command", "yi", "starcoder", "codellama"]


_MOE_RE = re.compile(r"(?<![A-Za-z0-9])(\d+)x(\d+(?:\.\d+)?)[Bb](?![A-Za-z])")


def parse_params_b(name: str) -> float | None:
    """Parameter count in billions from a name such as ``Llama-3.1-70B-Instruct`` or ``Mixtral-8x7B``.

    Returns the largest figure found (so ``Qwen3-235B-A22B`` reports total, not active, parameters).
    """
    moe = _MOE_RE.search(name)
    if moe:
        return float(moe.group(1)) * float(moe.group(2))
    values = [float(v) / (1000 if u in "Mm" else 1) for v, u in _PARAMS_RE.findall(name)]
    return max(values) if values else None


def guess_family(name: str) -> str:
    lower = name.lower()
    return next((f for f in _FAMILIES if f in lower), "other")


@dataclass
class CatalogEntry:
    id: str                      # path relative to MODELS_ROOT
    path: str
    family: str = "other"
    params_b: float | None = None
    weights_gb: float | None = None
    dtype: str = ""
    context_length: int | None = None
    architectures: list[str] = field(default_factory=list)
    quantization: str = ""
    vllm: str = "unknown"        # "likely" | "unknown"
    license_note: str = "Not recorded by ARC Research; check the model card before use."

    def memory_estimate_gb(self) -> float | None:
        """Weights plus ~20% runtime overhead; the KV cache is extra and depends on context/batch."""
        base = self.weights_gb
        if base is None and self.params_b is not None:
            bytes_per_param = 1 if "fp8" in self.quantization.lower() or "int8" in self.quantization.lower() else (
                0.5 if any(q in self.quantization.lower() for q in ("awq", "gptq", "int4", "4bit")) else 2)
            base = self.params_b * bytes_per_param
        return None if base is None else round(base * 1.2 + 2, 1)

    def min_gpus(self, gpu_memory_gb: float) -> int | None:
        need = self.memory_estimate_gb()
        if need is None:
            return None
        count = max(1, math.ceil(need / (gpu_memory_gb * 0.9)))
        return 1 << (count - 1).bit_length()  # tensor parallel sizes are powers of two

    def compatibility_warnings(self, *, gpus: int, gpu_type: str) -> list[str]:
        warnings = []
        mem = NOMINAL_GPU_MEMORY_GB.get(gpu_type.lower())
        need = self.memory_estimate_gb()
        if mem and need and mem * gpus * 0.9 < need:
            warnings.append(f"Needs about {need} GB of GPU memory (estimate); {gpus}x {gpu_type} provides "
                            f"{mem * gpus} GB. Request at least {self.min_gpus(mem)} GPU(s) or pick a larger GPU.")
        if self.vllm == "unknown":
            warnings.append("vLLM support for this architecture is not confirmed; check the vLLM supported-models list.")
        if self.context_length and self.context_length > 131072:
            warnings.append("Long native context: lower --max-model-len unless you have the memory for the KV cache.")
        return warnings

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["memory_estimate_gb"] = self.memory_estimate_gb()
        return value


def parse_inventory(text: str) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    for block in re.split(r"^@@MODEL ", text, flags=re.M)[1:]:
        head, _, rest = block.partition("\n")
        path = head.strip()
        config_text, _, tail = rest.partition("@@INDEX")
        try:
            config = json.loads(config_text.strip())
        except (ValueError, TypeError):
            config = {}
        if not isinstance(config, dict):
            config = {}
        total = re.search(r'"total_size":\s*(\d+)', tail)
        rel = path[len(MODELS_ROOT):].lstrip("/") if path.startswith(MODELS_ROOT) else path
        name = rel.rsplit("/", 1)[-1]
        archs = [a for a in config.get("architectures") or [] if isinstance(a, str)]
        quant = config.get("quantization_config")
        context = config.get("max_position_embeddings")
        entry = CatalogEntry(
            id=rel, path=path, family=guess_family(rel), params_b=parse_params_b(name),
            weights_gb=round(int(total.group(1)) / 1e9, 1) if total else None,
            dtype=str(config.get("torch_dtype") or ""),
            context_length=context if isinstance(context, int) else None,
            architectures=archs,
            quantization=str(quant.get("quant_method", "")) if isinstance(quant, dict) else "",
            vllm="likely" if any(a in VLLM_ARCHITECTURES for a in archs) else "unknown",
        )
        if "@@LICENSE present" in tail:
            entry.license_note = "A LICENSE file is present in the model directory; review it before use."
        entries.append(entry)
    return sorted(entries, key=lambda e: e.id.lower())


class ModelInventory:
    """Searchable catalog with favorites and recently used models (persisted, non-secret)."""

    def __init__(self, entries: list[CatalogEntry] | None = None, path: Path | None = None):
        self.entries = list(entries or [])
        self.path = Path(path) if path else None
        self.favorites: list[str] = []
        self.recent: list[str] = []
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.favorites = [str(i) for i in data.get("favorites", [])]
                self.recent = [str(i) for i in data.get("recent", [])]
            except (OSError, ValueError):
                pass

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"favorites": self.favorites, "recent": self.recent}, handle)
        os.replace(tmp, self.path)

    def get(self, model_id: str) -> CatalogEntry | None:
        return next((e for e in self.entries if e.id == model_id), None)

    def families(self) -> list[str]:
        return sorted({e.family for e in self.entries})

    def search(self, query: str = "", *, family: str = "", max_params_b: float | None = None,
               min_params_b: float | None = None, vllm_only: bool = False, favorites_only: bool = False,
               fits_gpu: tuple[int, str] | None = None) -> list[CatalogEntry]:
        terms = query.lower().split()
        results = []
        for e in self.entries:
            hay = f"{e.id} {e.family} {' '.join(e.architectures)}".lower()
            if not all(t in hay for t in terms):
                continue
            if family and e.family != family:
                continue
            if max_params_b is not None and (e.params_b is None or e.params_b > max_params_b):
                continue
            if min_params_b is not None and (e.params_b is None or e.params_b < min_params_b):
                continue
            if vllm_only and e.vllm != "likely":
                continue
            if favorites_only and e.id not in self.favorites:
                continue
            if fits_gpu:
                count, gpu = fits_gpu
                mem, need = NOMINAL_GPU_MEMORY_GB.get(gpu.lower()), e.memory_estimate_gb()
                if mem and need and mem * count * 0.9 < need:
                    continue
            results.append(e)
        return results

    def toggle_favorite(self, model_id: str) -> bool:
        if self.get(model_id) is None and model_id not in self.favorites:
            raise ValueError(f"Unknown model: {model_id}")
        if model_id in self.favorites:
            self.favorites.remove(model_id)
            result = False
        else:
            self.favorites.append(model_id)
            result = True
        self._save()
        return result

    def mark_used(self, model_id: str, *, limit: int = 10) -> None:
        self.recent = [model_id] + [m for m in self.recent if m != model_id]
        del self.recent[limit:]
        self._save()

    def recent_entries(self) -> list[CatalogEntry]:
        return [e for e in (self.get(i) for i in self.recent) if e]
