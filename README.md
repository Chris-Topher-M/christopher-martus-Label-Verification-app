# TTB Label Verification Proof of Concept

Stateless proof-of-concept for checking alcohol label images against expected TTB application fields.

## Live Demo

- Public repository: https://github.com/Chris-Topher-M/christopher-martus-Label-Verification-app
- Current reviewed branch: `master`
- Current reviewed commit: `7fe8842`
- Deployed frontend/API base URL: https://ttb-label-verification-ozud.onrender.com
- Deployed health URL: https://ttb-label-verification-ozud.onrender.com/health
- Last verified live: July 8, 2026

The FastAPI backend serves the plain HTML/CSS/JavaScript frontend, so the frontend and API share one deployed base URL.

## What It Does

- Upload one or more label images.
- Extract required label fields from each image with a vision model.
- Compare extracted label text against expected application values.
- Return pass/fail results for each field and an overall verdict.
- Check the government warning as an exact, case-sensitive and punctuation-sensitive match.

## Core Requirements Covered

- Batch upload is supported through the UI and `POST /verify/batch`.
- Single-label verification has a 5-second target under normal deployed conditions.
- The app has no database and does not persist label history.
- API keys are read from environment variables only.
- The UI uses large, direct labels, clear errors, and simple pass/review results for non-technical users.

## Local Setup

Prerequisites:

- Python 3.12
- `uv` package manager, or an existing virtual environment with the project dependencies installed

Install dependencies with `uv`:

```powershell
uv sync
```

If `uv` is unavailable but dependencies are already installed in `.venv`, use the virtual environment directly:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Environment Variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Yes | None | API key used by `OpenAIVisionService.from_env()` for OpenAI Responses API calls. |
| `VISION_MODEL` | No | `gpt-5.4-mini` | Vision-capable model name used for label extraction. |
| `VISION_TIMEOUT_SECONDS` | No | `4.0` | Timeout applied to OpenAI client creation and per-request vision calls. |
| `VISION_MAX_LONG_EDGE_PIXELS` | No | `1280` | Maximum long edge used when resizing label images before upload to the vision model. |
| `VISION_JPEG_QUALITY` | No | `80` | JPEG quality used when re-encoding uploaded label images for the vision request. |
| `VISION_IMAGE_DETAIL` | No | `high` | OpenAI image detail hint for the vision request. Accepted values are `low`, `high`, and `auto`; invalid values fall back to `high`. |
| `VISION_MAX_OUTPUT_TOKENS` | No | `420` | Maximum response tokens allowed for structured extraction output. |
| `BATCH_CONCURRENCY` | No | `3` | Maximum number of label verifications processed concurrently in `POST /verify/batch`, clamped to the range `1` to `10`. |

Do not commit `.env`, `.env.*`, request logs, or any file containing real secret values.

## Run Locally

Start the backend and frontend:

```powershell
uv run uvicorn backend.app.main:app --reload
```

Fallback when using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

Local URLs:

- App: http://127.0.0.1:8000
- Health check: http://127.0.0.1:8000/health
- API docs: http://127.0.0.1:8000/docs

## Deployment

The repository includes `render.yaml` for a Render free-tier web service.

- Runtime: Python
- Build command: `pip install uv && uv sync --frozen --no-dev`
- Start command: `uv run uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`

Set required and optional environment variables in the hosting provider. Do not rely on local `.env` files in production.

## Approach / AI Workflow

This app was built with Codex CLI using the `PLAN` / `REVIEW` / `EXECUTE` cadence defined in `AGENTS.md`.

- `PLAN`: the agent read the current code and requirements, proposed an approach, and wrote no code.
- `REVIEW`: the plan was critiqued against the project requirements and edge cases, then narrowed or corrected before implementation.
- `EXECUTE`: the agent implemented only the approved plan, added or updated tests, and self-verified before the phase was considered complete.

Each phase used a human gate between `PLAN`, `REVIEW`, and `EXECUTE`. That gate was intentional: it prevented jumping straight from a draft plan to a large unreviewed code drop, which reduced scope creep and missed edge cases.

AI-generated work included implementation drafts, refactors, tests, and documentation updates proposed through Codex. Human-written or human-controlled work included the project requirements, phase approval, scope checks between steps, and the decision to stop or revise work before execution when a plan did not yet meet the requirements.

Technical approach:

- FastAPI handles uploads, validation, static frontend serving, and JSON API responses.
- The vision service preprocesses uploaded images, calls the OpenAI vision model, and asks for structured output.
- The comparison layer normalizes fuzzy fields while keeping the government warning exact.
- The frontend provides single-label and batch flows using plain HTML, CSS, and JavaScript.
- Processing is stateless and in memory for each request.

## Tools Used

- Python 3.12
- FastAPI
- Plain HTML/CSS/JavaScript frontend
- OpenAI Responses API for vision extraction with `gpt-5.4-mini`
- Verified against OpenAI's current model list on July 9, 2026
- Pillow for image preprocessing
- Pytest for automated tests
- Render deployment configuration

## API Endpoints

- `GET /health` returns service health.
- `POST /verify` verifies one label image against one set of application fields.
- `POST /verify/batch` verifies up to 10 label images with matching application field sets.

## API Examples

Single-label verification with `POST /verify`:

```bash
curl -X POST "https://ttb-label-verification-ozud.onrender.com/verify" \
  -F "image=@label.png;type=image/png" \
  -F "brand_name=Acme Reserve" \
  -F "class_type=Red Wine" \
  -F "producer_name=Acme Winery, LLC" \
  -F "country_of_origin=United States" \
  -F "alcohol_by_volume=13.5%" \
  -F "net_contents=750 mL" \
  -F "government_warning=GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."
```

Expected success response shape:

```json
{
  "verdict": "PASS",
  "fields": [
    {
      "field": "brand_name",
      "application_value": "Acme Reserve",
      "extracted_value": "Acme Reserve",
      "normalized_application_value": "acme reserve",
      "normalized_extracted_value": "acme reserve",
      "status": "PASS",
      "score": 100.0,
      "message": "Fuzzy score met threshold 90."
    },
    {
      "field": "government_warning",
      "application_value": "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS.",
      "extracted_value": "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS.",
      "normalized_application_value": "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS.",
      "normalized_extracted_value": "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS.",
      "status": "PASS",
      "score": null,
      "message": "Government warning matched exactly."
    }
  ],
  "latency_ms": 1800
}
```

Batch verification with `POST /verify/batch`:

```bash
curl -X POST "https://ttb-label-verification-ozud.onrender.com/verify/batch" \
  -F 'items=[{"client_id":"label-1","brand_name":"Acme Reserve","class_type":"Red Wine","producer_name":"Acme Winery, LLC","country_of_origin":"United States","alcohol_by_volume":"13.5%","net_contents":"750 mL","government_warning":"GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."},{"client_id":"label-2","brand_name":"Acme Reserve","class_type":"Red Wine","producer_name":"Acme Winery, LLC","country_of_origin":"United States","alcohol_by_volume":"13.5%","net_contents":"750 mL","government_warning":"GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."}]' \
  -F "images=@label-1.png;type=image/png" \
  -F "images=@label-2.png;type=image/png"
```

Expected batch success response shape:

```json
{
  "summary": {
    "passed": 2,
    "needs_review": 0,
    "total": 2,
    "latency_ms": 3200
  },
  "items": [
    {
      "client_id": "label-1",
      "filename": "label-1.png",
      "verdict": "PASS",
      "fields": [
        {
          "field": "brand_name",
          "application_value": "Acme Reserve",
          "extracted_value": "Acme Reserve",
          "normalized_application_value": "acme reserve",
          "normalized_extracted_value": "acme reserve",
          "status": "PASS",
          "score": 100.0,
          "message": "Fuzzy score met threshold 90."
        }
      ],
      "latency_ms": 1500,
      "error": null
    },
    {
      "client_id": "label-2",
      "filename": "label-2.png",
      "verdict": "PASS",
      "fields": [
        {
          "field": "brand_name",
          "application_value": "Acme Reserve",
          "extracted_value": "Acme Reserve",
          "normalized_application_value": "acme reserve",
          "normalized_extracted_value": "acme reserve",
          "status": "PASS",
          "score": 100.0,
          "message": "Fuzzy score met threshold 90."
        }
      ],
      "latency_ms": 1600,
      "error": null
    }
  ]
}
```

Example 4xx error response from `POST /verify` when the uploaded file type is not supported:

```json
{
  "error": {
    "message": "Please upload a JPG, PNG, or WebP image.",
    "details": []
  }
}
```

## Upload Limits

- Supported image formats: JPG, PNG, WebP
- Maximum image size: 10 MB per image
- Maximum batch size: 10 labels

## Field Matching

Fuzzy or normalized fields:

- Brand Name
- Class / Type
- Producer Name
- Country of Origin
- Alcohol by Volume
- Net Contents

Exact field:

- Government Warning

The government warning must match exactly after extraction, including case, spelling, punctuation, and numbering.

## Assumptions

- Uploaded images are readable product label images.
- Label text is visible enough for the vision model to extract.
- Network access to the vision API is available.
- The deployed host has `OPENAI_API_KEY` set.
- Performance depends on image size, preprocessing, hosting cold starts, and vision API latency.

## Limitations

- This is a proof of concept, not a legal compliance guarantee.
- Accuracy depends on image quality and model extraction quality.
- No review history is saved.
- Free-tier hosting cold starts may affect response time.
- A label may need human review when fields are absent, unreadable, or mismatched.

## Verification Checklist

Run automated tests:

```powershell
uv run pytest
```

Fallback:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run the deployed smoke checklist:

```powershell
uv run python scripts/run_live_checklist.py https://ttb-label-verification-ozud.onrender.com
```

Fallback:

```powershell
.\.venv\Scripts\python.exe scripts\run_live_checklist.py https://ttb-label-verification-ozud.onrender.com
```

Manual live verification:

- Open the deployed URL in a clean browser session.
- Upload one valid label and confirm results return under 5 seconds under normal conditions.
- Upload multiple labels and confirm batch results are clear.
- Submit a label with a government warning case or punctuation mismatch and confirm that field fails.
- Submit an imperfect but readable image and confirm the app returns a clear result or review state.

## Pre-Submission Audit

Check repository state:

```powershell
git status --short
git ls-files --error-unmatch .env
git log --all --full-history --name-status -- .env .env.*
```

Confirm `.env` files are ignored:

```powershell
git check-ignore -v .env .env.example
```

Search current tracked files for likely secrets:

```powershell
git grep -n -I -E "OPENAI_API_KEY|sk-|api[_-]?key|apikey|secret|token|bearer" HEAD
```

Search current tracked files for high-confidence key material:

```powershell
git grep -n -I -E "sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9_]*(SECRET|TOKEN|KEY)[A-Za-z0-9_]*\s*[:=]\s*['""]?[A-Za-z0-9_./+=-]{20,}" HEAD
```

Search full Git history for high-confidence key material:

```powershell
git rev-list --all | ForEach-Object { git grep -n -I -E "sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9_]*(SECRET|TOKEN|KEY)[A-Za-z0-9_]*\s*[:=]\s*['""]?[A-Za-z0-9_./+=-]{20,}" $_ }
```

Expected false positives include environment variable names such as `OPENAI_API_KEY`, local parameter names such as `api_key`, and placeholder test values such as `test-key`. Real secret values must not appear in source, tests, docs, configs, commit history, logs, or generated files.
