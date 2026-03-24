# ai-pr-review-bot

On pull requests (`opened`, `synchronize`), GitHub Actions checks out the repo, builds a three-dot unified diff (truncated at 100k characters for the model), calls **Google Gemini**, and posts the result as a **PR comment**.

## Setup

1. Repo **Settings → Secrets and variables → Actions**: add **`GEMINI_API_KEY`** (from [Google AI Studio](https://aistudio.google.com/apikey)). Do not commit the key.
2. Optional: in the workflow job, set env **`GEMINI_MODEL`**. Default in CI is **`gemini-1.5-flash`** (often avoids `429` when `gemini-2.0-flash` free-tier quotas are saturated). See [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

If you still see **HTTP 429**, wait a minute (or until the next day for daily caps), enable **billing** on the Google Cloud project tied to the key, or try another model id your project supports.

### Troubleshooting

- **429 mentions `gemini-2.0-flash` but your workflow sets `gemini-1.5-flash`:** the run is using an **old commit** (before that change). **Re-run** does not update the commit—**push a new commit** or merge the branch that contains the updated workflow, then open a **new** workflow run. In the log you should see `GEMINI_MODEL=gemini-1.5-flash` and `pr_llm_review: GEMINI_MODEL=gemini-1.5-flash`.
3. Optional: set **`MAX_DIFF_CHARS`** in the workflow env to change truncation (default `100000`).

Same-repo PRs only are the supported starting point; forked PRs need extra care (tokens, approvals).
