# ReadSync Deployment

ReadSync now has two intentionally separate runtime modes:

- **Local development** uses **Ollama**
- **Deployed backend** uses **OpenRouter**

That separation is already supported in code. The only thing that changes between the two environments is the backend environment configuration.

## 1. Local mode stays on Ollama

Use the root [`.env.local.example`](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/.env.local.example) as your reference.

Your local `.env` should keep:

```env
LLM_PROVIDER=ollama
OLLAMA_ENDPOINT=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2
```

This keeps local ReadSync fully separate from the deployed OpenRouter setup.

## 2. Railway backend uses OpenRouter

Use [backend/.env.railway.example](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/backend/.env.railway.example) as the Railway template.

Required Railway environment variables:

```env
DATABASE_URL=sqlite:///./readsync.db
LLM_PROVIDER=openrouter
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free
OPENROUTER_API_KEY=...
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
FAISS_INDEX_PATH=./readsync.faiss
CORS_ORIGINS=https://your-readsync-frontend.vercel.app
```

Optional:

```env
OPENROUTER_HTTP_REFERER=
OPENROUTER_X_TITLE=ReadSync
READSYNC_ALLOWED_REPO_ROOTS=
```

Notes:

- Railway can now deploy directly from the **repo root**. Root-level [railway.json](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/railway.json), [Procfile](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/Procfile), and [requirements.txt](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/requirements.txt) forward the build into `backend/`.
- If you prefer, you can still set the backend root to `/backend`, and Railway will use [backend/railway.json](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/backend/railway.json) and [backend/Procfile](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/backend/Procfile).
- The OpenRouter key belongs only in Railway, never in the frontend
- Railway's filesystem is ephemeral by default, so SQLite and FAISS need a persistent volume if you want deployed data to survive redeploys

## 3. Vercel frontend points to Railway

Use [frontend/.env.vercel.example](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/frontend/.env.vercel.example) as the Vercel template.

Set:

```env
VITE_API_BASE=https://your-readsync-backend.up.railway.app
```

The frontend root should be `/frontend`.

SPA rewrites are already configured in [frontend/vercel.json](/Users/sofiacardenasgarcia/Documents/Codex/ReadSync/frontend/vercel.json).

## 4. Built-in connection check

After deployment:

1. Open ReadSync
2. Go to **Settings**
3. Choose `OpenRouter (deployed)` if needed
4. Click **Check LLM connection**

That check now calls the backend directly and confirms whether the deployed backend can actually reach OpenRouter with its configured model and API key.

## 5. What is already ready

Already done in code:

- provider switch between Ollama and OpenRouter
- OpenRouter extraction path
- OpenRouter match-judging path
- provider-aware health response
- provider-aware settings UI
- CORS driven by environment variables
- Railway backend start config
- Vercel frontend API base support

## 6. What still requires your login

I can prepare the project, but I cannot complete the actual Railway and Vercel deployment from here without access to your accounts.

The remaining account-side steps are:

1. create or open the Railway service
2. set the backend root to `/backend`
3. add the Railway environment variables
4. attach persistent storage if you want deployed SQLite/FAISS state retained
5. create or open the Vercel project
6. set the frontend root to `/frontend`
7. add `VITE_API_BASE`
8. deploy both services

Once that is done, the deployed ReadSync instance will use OpenRouter while your local machine continues using Ollama.
