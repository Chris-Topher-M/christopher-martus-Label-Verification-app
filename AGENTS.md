You are helping build the TTB Label Verification proof-of-concept. Standing rules for the whole 
project: 1. Stack: Python 3.12 + FastAPI backend, React (or plain HTML/JS) frontend, a vision 
model for extraction, no database (stateless / in-memory). Deploy target: a free-tier host (e.g. 
Railway, or Vercel + Render). 2. Hard requirements that override convenience: single-label result 
in UNDER 5 SECONDS; UI usable by a non-technical 70+ user with no instructions; BATCH UPLOAD is 
required, not optional; the government warning is an EXACT, case-sensitive match while all other 
fields are fuzzy/normalized; API keys live in ENVIRONMENT VARIABLES ONLY — never hardcoded, never 
committed. 3. Working cadence: when I say PLAN, propose an approach and list files/risks but 
write NO code. When I say REVIEW, critique that plan against the requirements and edge cases and 
finalize it. When I say EXECUTE, implement exactly the approved plan with tests, then tell me how 
to verify it. Keep scope to the current phase only. 4. Prefer correctness and clean structure 
over ambition. Confirm you understand these rules.