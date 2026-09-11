# RestartAI — Emergency Production Recovery Agent

RestartAI is an AI-powered emergency production recovery agent for factories. Instead of choosing a supplier only by purchase price, it dynamically builds the fastest feasible recovery plan from live supplier conversations, technician constraints, recovery time, and downtime economics.

## What is implemented

1. Factory enters the failed machine, part, quantity, recovery deadline and downtime cost.
2. Supplier calls run in **DEMO** or **LIVE CALL-E** mode.
3. Supplier conversations are converted into structured offers.
4. Partial stock creates a real shortfall and triggers adaptive supplier discovery.
5. Multiple supplier legs can be combined.
6. A local maintenance knowledge base determines demo technician requirements and installation time.
7. Python deterministically calculates material arrival, installation, total recovery time and economic exposure.
8. Recovery plans are compared and the best feasible plan is recommended.
9. Human approval is required before the workflow can mark a plan approved. No purchasing is automated.
10. Demo mode is deterministic and requires no API key, internet, Gemini/Groq key, or real phone calls.

## Why CALL-E matters

Many real suppliers may not expose inventory through APIs or structured digital systems. CALL-E lets RestartAI interact with suppliers through natural phone conversations and turn those conversations into structured operational data.

The key differentiator is the **adaptive recovery workflow**: if Supplier C has only 8 of 20 required parts, RestartAI does not discard it. It calls the next supplier for the remaining quantity and constructs a split plan when that is faster/economically feasible.

## Architecture

```text
FACTORY USER
     |
     v
+---------------------+
|    RestartAI UI     |
+---------------------+
     |
     v
+---------------------+
|  Recovery Engine    |
+---------------------+
   /        |         \
  v         v          v
CALL-E   Maintenance   Optional LLM
Agent    Knowledge     (language only)
         Base
  |
  v
Supplier phone calls
  |
  v
Dynamic replanning
  |
  v
Recovery Plan Engine
  |
  v
Human Approval
```

Technical principle:

- **CALL-E** = real-world supplier communication
- **Optional LLM** = language understanding/extraction only
- **Maintenance KB** = technician/task knowledge
- **Python** = deterministic planning, arithmetic and optimization
- **Human** = final approval

The LLM is deliberately not allowed to decide technician count, feasibility, final recovery time, arithmetic, or supplier ranking.

## Maintenance knowledge base

`data/maintenance_tasks.json` contains 10 illustrative demo maintenance tasks. These are **demo knowledge-base values, not universal industrial standards**.

For example, `bearing_replacement` uses 2 technicians and 30 minutes. The UI also supports factory/user overrides.

## Setup — Windows PowerShell

```powershell
cd D:\restartai
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.venv\Scripts\Activate.ps1
```

## DEMO mode

`.env`:

```env
CALLE_API_KEY=
CALL_E_MODE=demo
CALLE_BASE_URL=https://api.heycall-e.com
```

Demo mode makes no network requests and no phone calls.

Use:

- Machine: CNC Production Line 4
- Part: SKF 6205-2RS
- Quantity: 20
- Max recovery: 3 hours
- Downtime cost: ₹5,000/hour
- Available technicians: 3

The deterministic demo sequence is:

- A: 20 units, ₹150, 24h
- B: 20 units, ₹220, 2h
- C: 8 units, ₹180, 0.5h pickup
- D: 12 units, ₹190, 1.5h delivery

RestartAI discovers the C shortfall, calls D on the next replan action, and compares C+D against the alternatives.

Expected recommended plan:

```text
C: 8 × ₹180 = ₹1,440
D: 12 × ₹190 = ₹2,280
Purchase = ₹3,720

Material arrival = 1.5h
Installation = 0.5h
Total recovery = 2h

Downtime exposure = ₹5,000 × 2 = ₹10,000
Total economic exposure = ₹13,720
```

Supplier B has a 2-hour arrival but costs ₹4,400, giving ₹14,400 total exposure. Therefore C+D is recommended.

## LIVE CALL-E mode

Never paste your key into chat or frontend code. Put it only in your local `.env`:

```env
CALLE_API_KEY=YOUR_LOCAL_SECRET
CALL_E_MODE=live
CALLE_BASE_URL=https://api.heycall-e.com
```

Use authorized, consenting supplier/test numbers in valid E.164 form, for example `+14155550100`. The UI requires an explicit LIVE confirmation before dispatch. The backend keeps the API key server-side and uses stable idempotency keys.

CALL-E's current API requires a stable `Idempotency-Key` for safe call creation and supports structured `recipient_result_schema` output. citeturn0search4

Do not use the placeholder demo numbers in LIVE mode.

## Tests

```powershell
pytest -q
```

Tests cover:

- full-stock supplier
- partial + second supplier
- no over-purchasing
- no feasible supplier
- technician shortage
- recovery time including installation
- economic exposure
- best-plan selection
- maximum recovery-time constraint
- demo mode without credentials
- human approval
- idempotency

Tests never place real calls.

## Security

- `.env` is git-ignored.
- CALL-E credentials never reach JavaScript.
- Live phone numbers are validated as E.164.
- Live dispatch requires explicit confirmation.
- Stable idempotency keys prevent duplicate call creation on retries.
- No secrets are stored in README or source.
- Purchasing is never automated.

## Known MVP limitation

Run state is intentionally in memory to keep the hackathon MVP simple. A server restart clears active recovery runs. Persistent storage is not required for the demo and was intentionally not added as unnecessary complexity.

## Final demo story

> “We did not find the cheapest supplier. We found the fastest feasible way to restart production.”

The important moment is the transition from **8 units available** to **12-unit shortfall**, followed by an adaptive call to the next supplier and a deterministic C+D recovery plan.
