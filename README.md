# TTB Label Verification Proof of Concept

Stateless proof-of-concept for checking alcohol label images against expected TTB application fields.

## Live Demo

- Public repository: https://github.com/Chris-Topher-M/christopher-martus-Label-Verification-app
- Current reviewed branch: `master`
- Current reviewed commit: `7fe8842`
- Deployed frontend/API URL: https://ttb-label-verification-ozud.onrender.com/

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

## Approach

- FastAPI handles uploads, validation, static frontend serving, and JSON API responses.
- The vision service preprocesses uploaded images, calls the OpenAI vision model, and asks for structured output.
- The comparison layer normalizes fuzzy fields while keeping the government warning exact.
- The frontend provides single-label and batch flows using plain HTML, CSS, and JavaScript.
- Processing is stateless and in memory for each request.

## Tools Used

- Python 3.12
- FastAPI
- Plain HTML/CSS/JavaScript frontend
- OpenAI Responses API for vision extraction
- Pillow for image preprocessing
- Pytest for automated tests
- Render deployment configuration

## API Endpoints

- `GET /health` returns service health.
- `POST /verify` verifies one label image against one set of application fields.
- `POST /verify/batch` verifies up to 10 label images with matching application field sets.

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

Run the deployed smoke checklist after replacing the URL:

```powershell
uv run python scripts/run_live_checklist.py https://your-deployed-url.example
```

Fallback:

```powershell
.\.venv\Scripts\python.exe scripts\run_live_checklist.py https://your-deployed-url.example
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
