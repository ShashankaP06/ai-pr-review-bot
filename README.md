# ai-pr-review-bot

On pull requests (`opened`, `synchronize`), GitHub Actions checks out the repo, builds a three-dot unified diff (truncated at 100k characters for the model), calls **Google Gemini**, and posts the result as a **PR comment**.

## Setup

1. Repo **Settings → Secrets and variables → Actions**: add **`GEMINI_API_KEY`** (from [Google AI Studio](https://aistudio.google.com/apikey)). Do not commit the key.
2. Optional: in the workflow job, set env **`GEMINI_MODEL`** (default is `gemini-2.0-flash`). If that model is unavailable for your project, try `gemini-1.5-flash`.
3. Optional: set **`MAX_DIFF_CHARS`** in the workflow env to change truncation (default `100000`).

Same-repo PRs only are the supported starting point; forked PRs need extra care (tokens, approvals).
