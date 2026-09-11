# CALL-E integration

RestartAI keeps CALL-E on the FastAPI backend. The browser never receives the API key.

## Modes

### DEMO

```env
CALL_E_MODE=demo
```

No API calls and no phone calls. Supplier responses are deterministic.

### LIVE

```env
CALL_E_MODE=live
CALLE_API_KEY=your_local_secret
CALLE_BASE_URL=https://api.heycall-e.com
```

The live path uses `httpx.AsyncClient` server-side. It sends `POST /v1/calls` with the task, recipients, result schemas, metadata, bearer authentication, and a stable `Idempotency-Key`, then polls `GET /v1/calls/{call_id}` every two seconds for up to five minutes.

## Safety

Live calls are side effects. RestartAI therefore:

- requires explicit LIVE confirmation;
- accepts supplier numbers from user/configured input rather than inventing them;
- validates live numbers as E.164;
- keeps credentials server-side;
- uses stable business idempotency keys;
- never authorizes purchases;
- reports provider failures as user-facing errors instead of exposing Python tracebacks.

## Adaptive calls

The first wave calls up to three configured suppliers.

When a committed quantity shortfall remains, each **REPLAN / CALL NEXT SUPPLIER** action contacts one additional configured supplier. The task sent to the later supplier contains the remaining quantity and the information already discovered, so the call is context-aware rather than a duplicate generic prompt.

## Structured extraction

CALL-E is responsible for extracting conversational facts such as quantity, price and timing. RestartAI's deterministic Python engine is responsible for:

- quantity allocation
- feasibility
- technician constraints
- recovery time
- downtime exposure
- total economic exposure
- plan ranking

Do not use an LLM for final arithmetic or operational constraints.

## Live testing

Use only authorized/consenting test numbers and a real API key stored in local `.env`. Never commit `.env`, API keys, or real phone numbers to GitHub.

The independent diagnostics endpoints are `POST /api/calle/test-call` and `GET /api/calle/call/{call_id}`. The browser talks only to FastAPI; the API key remains server-side.
