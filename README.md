# RestartAI — Emergency Production Recovery Agent

RestartAI is a hackathon MVP for CALL-E. When a production machine goes down, the app:
1. captures the failed-part requirement and downtime constraints,
2. calls suppliers through CALL-E,
3. converts live phone results into structured supplier offers,
4. detects partial stock,
5. generates multi-supplier recovery plans,
6. scores plans by recovery time + economic exposure,
7. asks for human approval before any purchase.

## Architecture

Browser → FastAPI → Recovery Engine → CALL-E → Supplier → structured result → Recovery Engine → Dashboard

CALL-E API keys stay on the backend. Do not put them in browser JavaScript.

## Stack

- Python 3.11+
- FastAPI
- Jinja2 + vanilla JS/CSS
- Pydantic
- official `calle-ai` Python SDK
- Optional: SQLite later; MVP uses in-memory state
- Demo mode included so the full UI can be tested without placing real calls

## Setup

```powershell
cd restartai
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Put your CALL-E API key in `.env`:

```env
CALLE_API_KEY=your_key_here
CALL_E_MODE=demo
```

Start:

```powershell
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

### Demo mode

`CALL_E_MODE=demo` never places phone calls. It simulates the exact supplier sequence needed for the pitch.

### Live CALL-E mode

Set:

```env
CALL_E_MODE=live
CALLE_API_KEY=your_real_key
```

Then use only consenting/authorized supplier numbers and follow CALL-E's calling policies. New CALL-E accounts currently receive 20 free calls.

## Important

The first version deliberately does NOT automate purchasing. The agent can recommend a recovery plan, but the final purchase remains human-approved.

## Suggested live demo

Part: SKF 6205-2RS bearing
Quantity: 20
Downtime cost: ₹5,000/hour
Maximum recovery time: 3 hours

Demo suppliers:
- Supplier A: 20 units, tomorrow, ₹150
- Supplier B: 20 units, 2 hours, ₹220
- Supplier C: 8 units, immediate pickup, ₹180
- Supplier D: 12 units, 90 minutes, ₹190

The engine should discover that C + D beats the apparently cheap tomorrow option when downtime is included, then show a final human approval card.
