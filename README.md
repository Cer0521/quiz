# StudyTime Python Backend

A **FastAPI** port of the original Node.js/Express StudyTime backend.  
Keeps the same REST API contract so the existing React frontend works without changes.

## Stack

| Layer | Original (JS) | Python port |
|---|---|---|
| Framework | Express.js | **FastAPI** |
| ASGI server | — | **Uvicorn** |
| Auth / DB | Supabase | **Supabase** (same) |
| AI service | Gemini 2.5 Flash | **Gemini 2.5 Flash** (same) |
| Rate limiting | express-rate-limit | **slowapi** |
| File uploads | multer | **python-multipart** |
| HTTP client | node-fetch | **httpx** (async) |

---

## Quick start

```bash
# 1. Clone / unzip and enter the directory
cd studytime-python

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# → fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY

# 5. Run
python run.py
# → API available at http://localhost:8000
# → Interactive docs at http://localhost:8000/docs
```

---

## Project structure

```
app/
  main.py               # FastAPI app, CORS, rate limiter, router registration
  supabase_client.py    # Supabase admin client
  middleware/
    auth.py             # JWT bearer dependency (get_current_user, require_teacher, require_verified)
  routers/
    auth.py             # /api/auth  — register, login, logout, me, forgot-password
    quizzes.py          # /api/quizzes — CRUD, AI generation, analytics
    attempts.py         # /api/attempts — guest & authenticated quiz taking
    subscriptions.py    # /api/subscription — tiers, referrals, limits
    grading.py          # /api/grading — AI essay & enumeration grading
    dashboard.py        # /api/dashboard/stats
  services/
    gemini.py           # Gemini API calls: generate_from_document, grade_essay, grade_enumeration
```

---

## Best pairings for this backend

### Frontend (keep as-is or modernise)
- **React + Vite** (existing) — zero changes needed; just point `VITE_API_URL` at port 8000
- **Next.js App Router** — if you want SSR; slightly more refactoring on the frontend

### Deployment
| Service | Notes |
|---|---|
| **Railway** | Best for FastAPI; one-click Dockerfile or Nixpacks auto-detect |
| **Render** | Free tier available; set start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| **Fly.io** | Great for persistent WebSocket/background tasks |
| **Vercel** (serverless) | Possible via ASGI adapter but cold starts hurt; not recommended |

### Database / Auth (keep Supabase or swap)
- **Supabase** (existing) — zero changes; already async-compatible via `supabase-py`
- **PostgreSQL + SQLAlchemy** — if you want full ORM control; use `asyncpg` driver

### Background tasks / Queues (optional upgrade)
- **Celery + Redis** — for long AI generation jobs (prevents request timeouts on large PDFs)
- **ARQ** — lighter async task queue that pairs naturally with FastAPI

### Testing
- **pytest + httpx** — `TestClient` from FastAPI makes route testing trivial
- **pytest-asyncio** — for async service tests (Gemini mocking)

---

## API endpoints (same as original)

```
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/auth/forgot-password

GET    /api/quizzes
POST   /api/quizzes/generate          (multipart/form-data)
POST   /api/quizzes/manual
GET    /api/quizzes/:id
PATCH  /api/quizzes/:id
POST   /api/quizzes/:id/publish
DELETE /api/quizzes/:id
POST   /api/quizzes/:id/questions
PUT    /api/quizzes/:id/questions/:qid
DELETE /api/quizzes/:id/questions/:qid
GET    /api/quizzes/:id/analytics
GET    /api/quiz/share/:code

GET    /api/quizzes/:id/attempt
PUT    /api/attempts/:id/answers
POST   /api/attempts/:id/submit
GET    /api/attempts/history
GET    /api/attempts/:id/result

POST   /api/quiz/guest/start
PUT    /api/quiz/guest/attempts/:id/answers
POST   /api/quiz/guest/attempts/:id/submit
GET    /api/quiz/guest/attempts/:id/result

GET    /api/subscription
POST   /api/subscription/upgrade
POST   /api/subscription/referral
GET    /api/subscription/referral-stats
GET    /api/subscription/limits

POST   /api/grading/grade-essay
POST   /api/grading/grade-enumeration

GET    /api/dashboard/stats
GET    /api/health
```
