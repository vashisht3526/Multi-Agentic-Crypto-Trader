"""
LLM Fallback Chain — per-agent model distribution + automatic fallback.

Each agent gets a DIFFERENT primary Groq model so that rate limits (which
are per-model) are spread across the pool.  If the primary model for an
agent hits a rate limit, it falls back through the remaining models in
order, and finally to Gemini keys.

Agent → Primary Model mapping:
  Orderflow Calculator  → llama-3.1-8b-instant        (simple tool call)
  Orderflow Analyst     → llama-3.3-70b-versatile     (best analysis)
  Opportunity Scout     → meta-llama/llama-4-scout-17b-16e-instruct  (reasoning)
  Signal Challenger     → qwen/qwen3-32b              (skeptical review)
  Signal Reviewer       → llama-3.1-8b-instant        (simple review)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)


# ── Available Groq models (all use the same GROQ_API_KEY) ──────────────
GROQ_MODELS: list[str] = [
    "groq/llama-3.3-70b-versatile",
    "groq/llama-3.1-8b-instant",
    "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/qwen/qwen3-32b",
]

# Per-agent primary model assignment (spreads rate limits)
AGENT_MODEL_MAP: dict[str, str] = {
    "Orderflow Calculator":       "groq/llama-3.1-8b-instant",
    "Orderflow Analyst":          "groq/llama-3.3-70b-versatile",
    "BTC Opportunity Scout":      "groq/meta-llama/llama-4-scout-17b-16e-instruct",
    "Signal Challenger":          "groq/qwen/qwen3-32b",
    "Signal Performance Reviewer": "groq/llama-3.1-8b-instant",
}


@dataclass
class _ModelSlot:
    model: str          # e.g. "groq/llama-3.3-70b-versatile"
    api_key: str
    label: str
    cooldown_until: float = 0.0
    fail_count: int = 0


@dataclass
class LLMFallbackChain:
    """Manages a pool of LLM model slots with automatic fallback on rate-limit errors."""

    slots: list[_ModelSlot] = field(default_factory=list)
    cooldown_seconds: int = 65  # Groq resets per minute

    @classmethod
    def from_env(cls) -> "LLMFallbackChain":
        """Build the chain from environment variables."""
        slots: list[_ModelSlot] = []

        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            for model in GROQ_MODELS:
                short = model.replace("groq/", "")
                slots.append(_ModelSlot(
                    model=model,
                    api_key=groq_key,
                    label=f"Groq {short}",
                ))

        # Gemini fallback keys
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        for i in range(1, 4):
            key = os.getenv(f"GEMINI_API_KEY_{i}", "")
            if key:
                slots.append(_ModelSlot(
                    model=f"gemini/{gemini_model}",
                    api_key=key,
                    label=f"Gemini {gemini_model} key-{i}",
                ))

        if not slots:
            raise RuntimeError("No LLM API keys configured. Set GROQ_API_KEY or GEMINI_API_KEY_1 in .env")

        chain = cls(slots=slots)
        LOGGER.info(
            "LLM fallback chain (%d slots): %s",
            len(slots),
            " → ".join(s.label for s in slots),
        )
        return chain

    # ── Public API ─────────────────────────────────────────────────────

    def get_slot_for_agent(self, agent_role: str) -> _ModelSlot:
        """Get the best available ModelSlot for an agent."""
        primary = AGENT_MODEL_MAP.get(agent_role)
        now = time.time()

        if primary:
            for slot in self.slots:
                if slot.model == primary and now >= slot.cooldown_until:
                    return slot

        for slot in self.slots:
            if now >= slot.cooldown_until:
                return slot

        soonest = min(self.slots, key=lambda s: s.cooldown_until)
        wait = max(0.0, soonest.cooldown_until - now)
        LOGGER.warning("All LLM slots on cooldown. Waiting %.0fs for %s", wait, soonest.label)
        time.sleep(wait + 1)
        soonest.cooldown_until = 0
        return soonest

    def get_model_for_agent(self, agent_role: str) -> str:
        """
        Get the best available model for a specific agent.
        Uses the agent's primary model if available, otherwise falls back.
        """
        return self.get_slot_for_agent(agent_role).model

    def get_current_model(self) -> str:
        """Get any available model (for backward compat / general use)."""
        return self.get_model_for_agent("_default")

    def mark_rate_limited(self, model: str) -> None:
        """Mark a specific model as rate-limited with cooldown."""
        for slot in self.slots:
            if slot.model == model:
                slot.cooldown_until = time.time() + self.cooldown_seconds
                slot.fail_count += 1
                LOGGER.warning(
                    "Rate limited: %s (fails=%d). Cooling down for %ds.",
                    slot.label, slot.fail_count, self.cooldown_seconds,
                )
                return

    def mark_model_error(self, model: str, permanent: bool = False) -> None:
        """Mark a model as errored (e.g. decommissioned). Long cooldown."""
        for slot in self.slots:
            if slot.model == model:
                # 24h cooldown for permanent errors like decommissioned models
                cooldown = 86400 if permanent else self.cooldown_seconds
                slot.cooldown_until = time.time() + cooldown
                slot.fail_count += 1
                LOGGER.error(
                    "Model error: %s (permanent=%s). Disabled for %ds.",
                    slot.label, permanent, cooldown,
                )
                return

    @property
    def active_label(self) -> str:
        now = time.time()
        available = [s for s in self.slots if now >= s.cooldown_until]
        if available:
            return available[0].label
        return "all cooldown"

    @property
    def status_summary(self) -> dict[str, Any]:
        """Return a dict summarizing chain status for logging/API."""
        now = time.time()
        return {
            "total_slots": len(self.slots),
            "available": [s.label for s in self.slots if now >= s.cooldown_until],
            "cooling_down": [
                {"label": s.label, "remaining_s": round(s.cooldown_until - now)}
                for s in self.slots if now < s.cooldown_until
            ],
        }


# ── Global singleton ───────────────────────────────────────────────────
_chain: LLMFallbackChain | None = None


def get_llm_chain() -> LLMFallbackChain:
    """Get or create the global LLM fallback chain."""
    global _chain
    if _chain is None:
        _chain = LLMFallbackChain.from_env()
    return _chain
