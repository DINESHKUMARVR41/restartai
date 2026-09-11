# CALL-E integration

The project uses the official Python server SDK:

`pip install calle-ai`

The SDK is used only on the FastAPI backend.

## Why

CALL-E is not something that becomes active merely because its name appears in the code. The application must actually call the CALL-E Developer API/SDK. You also need a CALL-E account and API key for live calls.

The frontend never receives the API key.

## Live flow

1. User starts recovery.
2. FastAPI creates a supplier call task.
3. `CalleClient.calls.create_and_wait(...)` executes the phone task.
4. CALL-E returns structured supplier information.
5. Recovery engine stores the result.
6. A partial-stock result triggers replanning.
7. The app calls another supplier.
8. The recovery engine compares plans.
9. Human approves the recommendation.

## Credentials

Set:

`CALLE_API_KEY=...`

and:

`CALL_E_MODE=live`

Use real authorized numbers only. Keep an idempotency key for each business call so retries do not create duplicate calls.

## Hackathon requirement

The CALL-E hackathon requires a functional project using CALL-E's API/SDKs or its Skill/MCP integrations, and requires a pull request to the `awesome-phone-call-agents` repository as part of submission.
