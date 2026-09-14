import os
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROK_CHAT_URL = "https://api.x.ai/v1/chat/completions"

SYSTEM_PROMPT = """You are the explanation layer for RestartAI, an emergency factory parts-recovery agent.

A deterministic Python engine has already:
- called suppliers, collected offers
- computed every plan's cost, arrival time, recovery time, downtime exposure and feasibility
- ranked the plans and selected a recommended plan

Your ONLY job is to explain that existing recommendation to a factory manager in clear, confident,
plain-English business language. You are NOT allowed to:
- change the recommended plan
- invent or alter any number (cost, hours, quantities, exposure)
- recommend a different plan than the one marked as recommended

Use only the numbers given to you. If information is missing, say so rather than guessing.
Keep it to 3-5 short sentences plus, if useful, one short bullet list of trade-offs versus the
next-best alternative. Write for a manager deciding under time pressure at 2am, not an executive report.
"""

CHAT_SYSTEM_PROMPT = """You are the assistant chatbot embedded in RestartAI's operator dashboard, sitting
beside a live emergency parts-recovery run.

You will be given the current run's data: the incident (machine, part, quantity, deadline,
downtime cost), every supplier offer collected so far (status, quantity, price, ETA), the
technician/maintenance intelligence, and the ranked recovery plans (if any exist yet).

Answer the operator's questions about THIS run using ONLY the data you are given. You may:
- explain why a plan was or wasn't recommended
- summarize supplier offers or compare them
- explain technician/installation constraints
- do simple arithmetic on the numbers provided

You must NOT:
- invent numbers, suppliers, or offers that are not in the data
- change or second-guess the recommended plan yourself — you can explain it, not overrule it
- speculate about suppliers who haven't responded yet

If the operator asks something the data doesn't cover, say plainly that you don't have that
information rather than guessing. Keep answers short and conversational — 2-4 sentences unless
the operator explicitly asks for more detail.
"""

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "gemini": "gemini-2.5-flash",
    "grok": "grok-4.1-fast",
}


class LlmError(RuntimeError):
    pass


class LlmService:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        if self.provider not in DEFAULT_MODELS:
            logger.warning("Unknown LLM_PROVIDER=%s, falling back to gemini", self.provider)
            self.provider = "gemini"

        key_env = {
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "grok": "GROK_API_KEY",
        }[self.provider]
        self.api_key = os.getenv(key_env, "")

        model_env = {
            "anthropic": "ANTHROPIC_MODEL",
            "gemini": "GEMINI_MODEL",
            "grok": "GROK_MODEL",
        }[self.provider]
        self.model = os.getenv(model_env, DEFAULT_MODELS[self.provider])
        self.enabled = bool(self.api_key)

    @staticmethod
    def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
        if not plan:
            return {}
        return {
            "legs": [
                {
                    "supplier": leg["supplier"],
                    "quantity": leg["quantity"],
                    "unit_price": leg["unit_price"],
                    "arrival_hours": leg["arrival_hours"],
                    "delivery_method": leg["delivery_method"],
                }
                for leg in plan.get("legs", [])
            ],
            "total_purchase_cost": plan.get("total_purchase_cost"),
            "material_arrival_hours": plan.get("material_arrival_hours"),
            "installation_minutes": plan.get("installation_minutes"),
            "recovery_time_hours": plan.get("recovery_time_hours"),
            "downtime_exposure": plan.get("downtime_exposure"),
            "total_exposure": plan.get("total_exposure"),
            "feasible": plan.get("feasible"),
            "infeasibility_reasons": plan.get("infeasibility_reasons", []),
        }

    def _fallback_summary(self, recommended: dict[str, Any], alternatives: list[dict[str, Any]]) -> str:
        """Deterministic, template-based summary used when no API key is configured."""
        if not recommended:
            return "No feasible recovery plan is available yet. Waiting on supplier offers."
        legs = ", ".join(f"{leg['quantity']} units from {leg['supplier']} (₹{leg['unit_price']}/unit)" for leg in recommended.get("legs", []))
        lines = [
            f"Recommended plan: {legs}.",
            f"Total purchase cost ₹{recommended.get('total_purchase_cost')}, "
            f"material arrival in {recommended.get('material_arrival_hours')}h, "
            f"total recovery time {recommended.get('recovery_time_hours')}h.",
            f"Total economic exposure ₹{recommended.get('total_exposure')} "
            f"(downtime exposure ₹{recommended.get('downtime_exposure')}).",
        ]
        if alternatives:
            alt = alternatives[0]
            lines.append(
                f"Next-best alternative totals ₹{alt.get('total_exposure')} exposure "
                f"with {alt.get('recovery_time_hours')}h recovery time."
            )
        lines.append(f"(AI recommendation is disabled — set {self._key_env_name()} to enable natural-language explanations.)")
        return " ".join(lines[:-1]) + "\n\n" + lines[-1]

    def _key_env_name(self) -> str:
        return {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "grok": "GROK_API_KEY"}[self.provider]

    async def recommend(self, run_snapshot: dict[str, Any]) -> dict[str, Any]:
        plans = run_snapshot.get("plans") or []
        recommended = plans[0] if plans else None
        alternatives = plans[1:4]

        if not self.enabled:
            return {"success": True, "source": "fallback", "text": self._fallback_summary(self._plan_summary(recommended) if recommended else {}, [self._plan_summary(p) for p in alternatives])}

        payload_context = {
            "machine": run_snapshot.get("request", {}).get("machine"),
            "part_number": run_snapshot.get("request", {}).get("part_number"),
            "quantity_needed": run_snapshot.get("request", {}).get("quantity"),
            "max_hours_allowed": run_snapshot.get("request", {}).get("max_hours"),
            "downtime_cost_per_hour": run_snapshot.get("request", {}).get("downtime_cost_per_hour"),
            "recommended_plan": self._plan_summary(recommended) if recommended else None,
            "alternative_plans": [self._plan_summary(p) for p in alternatives],
        }

        user_message = (
            "Explain and justify this already-selected recovery plan for the factory manager. "
            "Do not change the recommendation or the numbers.\n\n"
            f"{payload_context}"
        )

        if self.provider == "anthropic":
            text = await self._call_anthropic(user_message)
        elif self.provider == "gemini":
            text = await self._call_gemini(user_message)
        else:
            text = await self._call_grok(user_message)

        if not text:
            raise LlmError("AI recommendation returned no text.")
        return {"success": True, "source": self.provider, "text": text}

    def _run_context(self, run_snapshot: dict[str, Any]) -> dict[str, Any]:
        plans = run_snapshot.get("plans") or []
        request = run_snapshot.get("request", {})
        return {
            "stage": run_snapshot.get("stage"),
            "machine": request.get("machine"),
            "part_number": request.get("part_number"),
            "part_description": request.get("part_description"),
            "quantity_needed": request.get("quantity"),
            "max_hours_allowed": request.get("max_hours"),
            "downtime_cost_per_hour": request.get("downtime_cost_per_hour"),
            "compatibility_notes": request.get("compatibility_notes"),
            "supplier_offers": [
                {
                    "supplier": o.get("supplier"),
                    "status": o.get("status"),
                    "quantity_available": o.get("quantity_available"),
                    "unit_price": o.get("unit_price"),
                    "currency": o.get("currency"),
                    "availability_hours": o.get("availability_hours"),
                    "compatible": o.get("compatible"),
                    "confirmed": o.get("confirmed"),
                    "notes": o.get("notes"),
                }
                for o in run_snapshot.get("offers", [])
            ],
            "technician_intelligence": run_snapshot.get("technician_intelligence"),
            "recommended_plan": self._plan_summary(plans[0]) if plans else None,
            "alternative_plans": [self._plan_summary(p) for p in plans[1:4]],
            "approved": run_snapshot.get("approved"),
        }

    async def chat(self, run_snapshot: dict[str, Any], message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        history = history or []
        if not self.enabled:
            return {
                "success": True,
                "source": "fallback",
                "text": (
                    f"The AI chat assistant isn't configured yet — set {self._key_env_name()} to enable "
                    "natural-language Q&A about this run. In the meantime, the offers and plan tables "
                    "above have the full data for this run."
                ),
            }

        context = self._run_context(run_snapshot)
        convo = "\n".join(f"{h['role']}: {h['content']}" for h in history[-10:])
        user_message = (
            "Here is the current recovery run's data:\n"
            f"{context}\n\n"
            + (f"Conversation so far:\n{convo}\n\n" if convo else "")
            + f"Operator question: {message}"
        )

        if self.provider == "anthropic":
            text = await self._call_anthropic(user_message, system=CHAT_SYSTEM_PROMPT)
        elif self.provider == "gemini":
            text = await self._call_gemini(user_message, system=CHAT_SYSTEM_PROMPT)
        else:
            text = await self._call_grok(user_message, system=CHAT_SYSTEM_PROMPT)

        if not text:
            raise LlmError("Chat assistant returned no text.")
        return {"success": True, "source": self.provider, "text": text}

    async def _call_anthropic(self, user_message: str, system: str = SYSTEM_PROMPT) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": 500,
            "system": system,
            "messages": [{"role": "user", "content": user_message}],
        }
        data = await self._post(ANTHROPIC_MESSAGES_URL, headers, body)
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()

    async def _call_gemini(self, user_message: str, system: str = SYSTEM_PROMPT) -> str:
        url = GEMINI_URL_TEMPLATE.format(model=self.model)
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": 500},
        }
        data = await self._post(url, headers, body)
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()

    async def _call_grok(self, user_message: str, system: str = SYSTEM_PROMPT) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "max_tokens": 500,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        }
        data = await self._post(GROK_CHAT_URL, headers, body)
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()

    async def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LlmError(f"AI recommendation request to {self.provider} timed out.") from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"AI recommendation request to {self.provider} failed: {exc}") from exc

        if response.is_error:
            logger.warning("%s API error status=%s body=%s", self.provider, response.status_code, response.text[:300])
            raise LlmError(f"AI recommendation failed ({self.provider}, {response.status_code}).")
        return response.json()
