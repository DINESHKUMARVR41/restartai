# RestartAI LIVE CALL-E verification

## 1. Configure `.env`

Create `.env` in the project root:

```env
CALL_E_MODE=live
CALLE_API_KEY=YOUR_LOCAL_CALL_E_KEY
CALLE_BASE_URL=https://api.heycall-e.com
CALLE_POLL_INTERVAL_SECONDS=2
CALLE_POLL_TIMEOUT_SECONDS=300
```

Never commit `.env` or share the key.

## 2. Start the backend

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

## 3. Verify configuration

Open:

`http://127.0.0.1:8000/api/config`

Expected:

```json
{"mode":"live","live":true,"configured":true,"configuration_error":null}
```

If `configured` is false, do not attempt a call. Fix `.env` and restart the backend.

## 4. Test exactly one authorized E.164 number

Use the UI's **TEST LIVE CALL** button. The backend will create one CALL-E task and poll its status. It will never call a number automatically on server startup or health checks.

The UI reports the CALL-E call ID, status, failure code/message, recipient status, and attempt diagnostics.

## 5. Expected CALL-E lifecycle

`queued -> in_progress -> completed`

or a terminal failure such as:

`queued -> failed`

A queued request only means CALL-E accepted the task; it does not mean the phone call was answered.
