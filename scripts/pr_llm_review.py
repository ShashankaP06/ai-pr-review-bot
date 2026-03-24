#!/usr/bin/env python3
"""Read PR unified diff from a file, call Gemini, post one GitHub PR (issue) comment."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "100000"))
GEMINI_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

REVIEW_INSTRUCTIONS = """You are a senior engineer reviewing a pull request. Given the unified diff below:
- Start with a brief summary (2–4 bullets max).
- Call out notable risks, bugs, or missing tests if you see them.
- Optional: small, concrete suggestions.
Use GitHub-flavored markdown. Be concise. Do not repeat the entire diff. If the diff is empty or only whitespace, say there is nothing to review. If the diff looks truncated, say you only saw part of the changes."""


def _gemini_review(api_key: str, model: str, diff: str) -> str:
    url = f"{GEMINI_TEMPLATE.format(model=model)}?key={api_key}"
    user_block = (
        f"{REVIEW_INSTRUCTIONS}\n\n---\n\nUnified diff:\n```diff\n{diff}\n```\n"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_block}]}],
        "generationConfig": {"temperature": 0.35, "maxOutputTokens": 8192},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    try:
        cand = data["candidates"][0]
        parts = cand["content"]["parts"]
        return parts[0]["text"].strip()
    except (KeyError, IndexError, TypeError) as e:
        snippet = json.dumps(data, indent=2)[:4000]
        raise RuntimeError(f"Unexpected Gemini response shape: {e}\n{snippet}") from e


def _post_comment(token: str, repo: str, pr_number: str, body: str) -> None:
    comment_url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    payload = {"body": body}
    req = urllib.request.Request(
        comment_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"GitHub API returned HTTP {resp.status}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: pr_llm_review.py <diff-file>", file=sys.stderr)
        return 2

    diff_path = sys.argv[1]
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 1

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    if not token or not repo or not pr_number:
        print(
            "GITHUB_TOKEN, GITHUB_REPOSITORY, and PR_NUMBER must be set",
            file=sys.stderr,
        )
        return 1

    model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash").strip()
    if not model:
        model = "gemini-1.5-flash"

    with open(diff_path, encoding="utf-8", errors="replace") as f:
        diff = f.read()

    truncated = False
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[... diff truncated by CI ...]\n"
        truncated = True

    review = None
    for attempt in range(2):
        try:
            review = _gemini_review(api_key, model, diff)
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429 and attempt == 0:
                wait = 50
                try:
                    err_json = json.loads(body)
                    for d in err_json.get("error", {}).get("details", []):
                        if d.get("@type", "").endswith("RetryInfo"):
                            sec = d.get("retryDelay", "")
                            if isinstance(sec, str) and sec.endswith("s"):
                                wait = min(120, max(15, int(float(sec.rstrip("s")) + 5)))
                            break
                except (ValueError, TypeError, KeyError):
                    pass
                print(
                    f"Gemini rate limited (429), waiting {wait}s then retrying once...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            print(f"Gemini HTTP {e.code}: {body}", file=sys.stderr)
            return 1
        except urllib.error.URLError as e:
            print(f"Gemini request failed: {e}", file=sys.stderr)
            return 1
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
    if review is None:
        return 1

    footer = f"\n\n---\n_AI review via Gemini (`{model}`); experimental._"
    body = review + footer
    if truncated:
        body = (
            f"_Diff truncated to {MAX_DIFF_CHARS} characters for the model._\n\n" + body
        )

    try:
        _post_comment(token, repo, pr_number, body)
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        print(f"GitHub HTTP {e.code}: {err}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"GitHub request failed: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
