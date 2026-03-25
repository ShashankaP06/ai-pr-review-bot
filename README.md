# ai-pr-review-bot

On pull requests (`opened`, `synchronize`), GitHub Actions builds a three-dot unified diff (truncated at 100k characters), calls an **LLM**, and posts the result as a **PR comment**.

## Providers (pick one)

The script chooses in this order:

1. **OpenAI-compatible server** — if **`OPENAI_COMPAT_BASE_URL`** is non-empty (local Ollama `/v1`, LM Studio, vLLM, etc.).
2. **Ollama Cloud** — if **`OLLAMA_API_KEY`** is set (key from [ollama.com → Keys](https://ollama.com/settings/keys)); calls **`https://ollama.com/api/chat`**. See [Ollama Cloud API](https://docs.ollama.com/cloud).
3. **Google Gemini** — if **`GEMINI_API_KEY`** is set.

If **`OLLAMA_API_KEY`** is set, it is used **before** Gemini (clear the secret or use a fork workflow if you want Gemini instead).

### A) Gemini (GitHub-hosted runners)

1. Repo **Settings → Secrets and variables → Actions**: add **`GEMINI_API_KEY`** ([Google AI Studio](https://aistudio.google.com/apikey)).
2. Optional workflow env **`GEMINI_MODEL`** (default `gemini-1.5-flash`). See [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

### B) Ollama Cloud API key (works on GitHub-hosted runners)

Your **ollama.com** API key is for **Ollama’s cloud models**, not `localhost`.

1. Create a key at **ollama.com → Settings → Keys** (as in your account).
2. Repo **Secrets → Actions**: add **`OLLAMA_API_KEY`** (paste the full key; never commit it).
3. Repo **Variables → Actions**: add **`OLLAMA_MODEL`** with a cloud model id from the [cloud model list](https://ollama.com/search?c=cloud) (examples: `gpt-oss:120b`, or whatever the site lists for API use — match the name from `curl https://ollama.com/api/tags` with your key).
4. Optional variable **`OLLAMA_HOST`**: only if Ollama documents a different API host; default is **`https://ollama.com`**.

Billing / plan limits apply per Ollama’s account.

### C) Ollama **local** / OpenAI-compatible URL

Ollama exposes an OpenAI-compatible API, e.g. **`http://127.0.0.1:11434/v1`** (see [Ollama OpenAI compatibility](https://github.com/ollama/ollama/blob/main/docs/openai.md)).

**Important:** A job on **GitHub’s hosted** `ubuntu-latest` **cannot** call **`localhost` on your laptop** — there is no Ollama there. Use one of these:

- **Self-hosted GitHub Actions runner** on the same machine (or LAN) as Ollama, with `OPENAI_COMPAT_BASE_URL=http://127.0.0.1:11434/v1` (or your LAN IP).
- **Run the script locally** when testing: export env vars and run `python scripts/pr_llm_review.py pr.diff` after generating `pr.diff`.
- A **reachable HTTPS URL** to your server (VPN, tailnet, etc.) — treat keys and exposure carefully.

**Repository variables / secrets** (Settings → Secrets and variables → Actions):

| Name | Type | Example |
|------|------|--------|
| `OPENAI_COMPAT_BASE_URL` | Variable | `http://127.0.0.1:11434/v1` |
| `OPENAI_COMPAT_MODEL` | Variable | `llama3.2` |
| `OPENAI_COMPAT_API_KEY` | Secret (optional) | Often empty for Ollama |

Optional env **`LLM_HTTP_TIMEOUT_SEC`** (default `120` for Gemini, script uses `300` for compat if unset — override as needed).

When **`OPENAI_COMPAT_BASE_URL`** is set, the script does **not** use Ollama Cloud or Gemini for that job. When only **`OLLAMA_API_KEY`** is set, you do not need **`GEMINI_API_KEY`**.

## Other options

- Optional **`MAX_DIFF_CHARS`** in workflow env (default `100000`).

### Troubleshooting

- **429 on Gemini:** quota / rate limits; wait, enable billing, or use **OpenAI-compat** with a self-hosted model.
- **429 mentions `gemini-2.0-flash` but workflow uses `1.5-flash`:** the run is an **old commit**; push a new commit instead of only re-running an old job.

Same-repo PRs only for the simplest setup; forked PRs need extra care (tokens, approvals).
