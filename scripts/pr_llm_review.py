#!/usr/bin/env python3
"""Read PR unified diff, call Gemini, Ollama Cloud, or OpenAI-compatible API; post PR comment."""
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
    timeout = int(os.environ.get("LLM_HTTP_TIMEOUT_SEC", "120"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
        try:
            cand = data["candidates"][0]
            parts = cand["content"]["parts"]
            return parts[0]["text"].strip()
        except (KeyError, IndexError, TypeError) as e:
            snippet = json.dumps(data, indent=2)[:4000]
            raise RuntimeError(
                f"Unexpected Gemini response shape: {e}\n{snippet}"
            ) from e


def _ollama_com_chat_review(host: str, api_key: str, model: str, diff: str) -> str:
    """POST /api/chat on ollama.com (or compatible host) with Bearer API key."""
    base = host.rstrip("/")
    url = f"{base}/api/chat"
    user_block = (
        f"{REVIEW_INSTRUCTIONS}\n\n---\n\nUnified diff:\n```diff\n{diff}\n```\n"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_block}],
        "stream": False,
        "options": {"temperature": 0.35},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    timeout = int(os.environ.get("LLM_HTTP_TIMEOUT_SEC", "300"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    try:
        return data["message"]["content"].strip()
    except (KeyError, TypeError) as e:
        snippet = json.dumps(data, indent=2)[:4000]
        raise RuntimeError(
            f"Unexpected Ollama /api/chat response shape: {e}\n{snippet}"
        ) from e


def _openai_compat_review(base_url: str, api_key: str | None, model: str, diff: str) -> str:
    """POST /v1/chat/completions — works with Ollama, LM Studio, vLLM, etc."""
    base = base_url.rstrip("/")
    url = f"{base}/chat/completions"
    user_content = f"Unified diff:\n```diff\n{diff}\n```\n"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REVIEW_INSTRUCTIONS},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.35,
        "max_tokens": 8192,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    timeout = int(os.environ.get("LLM_HTTP_TIMEOUT_SEC", "300"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        snippet = json.dumps(data, indent=2)[:4000]
        raise RuntimeError(
            f"Unexpected chat completions response shape: {e}\n{snippet}"
        ) from e


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


def _gemini_retry_loop(api_key: str, model: str, diff: str) -> str:
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
                                wait = min(
                                    120,
                                    max(15, int(float(sec.rstrip("s")) + 5)),
                                )
                            break
                except (ValueError, TypeError, KeyError):
                    pass
                print(
                    f"Gemini rate limited (429) on {model}, waiting {wait}s "
                    "then retrying once...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            print(f"Gemini HTTP {e.code}: {body}", file=sys.stderr)
            raise
        except urllib.error.URLError as e:
            print(f"Gemini request failed: {e}", file=sys.stderr)
            raise
        except RuntimeError:
            raise
    if review is None:
        raise RuntimeError("Gemini returned no review text")
    return review


def _openai_compat_retry_loop(
    base_url: str, api_key: str | None, model: str, diff: str
) -> str:
    for attempt in range(2):
        try:
            return _openai_compat_review(base_url, api_key, model, diff)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429 and attempt == 0:
                print(
                    f"LLM rate limited (429), waiting 15s then retrying once...",
                    file=sys.stderr,
                )
                time.sleep(15)
                continue
            print(f"OpenAI-compat HTTP {e.code}: {body}", file=sys.stderr)
            raise
        except urllib.error.URLError as e:
            print(f"OpenAI-compat request failed: {e}", file=sys.stderr)
            raise
        except RuntimeError:
            raise
    raise RuntimeError("OpenAI-compat returned no review text")


def _ollama_com_retry_loop(host: str, api_key: str, model: str, diff: str) -> str:
    for attempt in range(2):
        try:
            return _ollama_com_chat_review(host, api_key, model, diff)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429 and attempt == 0:
                print(
                    "Ollama Cloud rate limited (429), waiting 20s then retrying once...",
                    file=sys.stderr,
                )
                time.sleep(20)
                continue
            print(f"Ollama Cloud HTTP {e.code}: {body}", file=sys.stderr)
            raise
        except urllib.error.URLError as e:
            print(f"Ollama Cloud request failed: {e}", file=sys.stderr)
            raise
        except RuntimeError:
            raise
    raise RuntimeError("Ollama Cloud returned no review text")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: pr_llm_review.py <diff-file>", file=sys.stderr)
        return 2

    diff_path = sys.argv[1]
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    if not token or not repo or not pr_number:
        print(
            "GITHUB_TOKEN, GITHUB_REPOSITORY, and PR_NUMBER must be set",
            file=sys.stderr,
        )
        return 1

    compat_base = os.environ.get("OPENAI_COMPAT_BASE_URL", "").strip()
    ollama_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if compat_base:
        model = os.environ.get("OPENAI_COMPAT_MODEL", "llama3.2").strip() or "llama3.2"
        api_key = os.environ.get("OPENAI_COMPAT_API_KEY", "").strip() or None
        print(
            f"pr_llm_review: provider=openai_compat url={compat_base} model={model}",
            file=sys.stderr,
        )
        review_label = f"OpenAI-compatible API (`{model}`)"
        try:
            review = _openai_compat_retry_loop(compat_base, api_key, model, _read_diff(diff_path))
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError):
            return 1
    elif ollama_key:
        host = os.environ.get("OLLAMA_HOST", "https://ollama.com").strip()
        if not host:
            host = "https://ollama.com"
        model = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b").strip() or "gpt-oss:120b"
        print(
            f"pr_llm_review: provider=ollama_cloud host={host} model={model}",
            file=sys.stderr,
        )
        review_label = f"Ollama Cloud (`{model}`)"
        diff, truncated = _load_diff(diff_path)
        try:
            review = _ollama_com_retry_loop(host, ollama_key, model, diff)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError):
            return 1
        footer = f"\n\n---\n_AI review via {review_label}; experimental._"
        body = _build_body(review, footer, truncated)
        return _post_or_fail(token, repo, pr_number, body)
    elif gemini_key:
        model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash").strip()
        if not model:
            model = "gemini-1.5-flash"
        print(f"pr_llm_review: provider=gemini GEMINI_MODEL={model}", file=sys.stderr)
        review_label = f"Gemini (`{model}`)"
        diff, truncated = _load_diff(diff_path)
        try:
            review = _gemini_retry_loop(gemini_key, model, diff)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError):
            return 1
        footer = f"\n\n---\n_AI review via {review_label}; experimental._"
        body = _build_body(review, footer, truncated)
        return _post_or_fail(token, repo, pr_number, body)

    else:
        print(
            "Set one of: GEMINI_API_KEY; OLLAMA_API_KEY (+ OLLAMA_MODEL) for ollama.com; "
            "or OPENAI_COMPAT_BASE_URL + OPENAI_COMPAT_MODEL for OpenAI-compatible servers.",
            file=sys.stderr,
        )
        return 1

    # openai_compat path: need truncated diff for footer
    diff, truncated = _load_diff(diff_path)
    footer = f"\n\n---\n_AI review via {review_label}; experimental._"
    body = _build_body(review, footer, truncated)
    return _post_or_fail(token, repo, pr_number, body)


def _read_diff(diff_path: str) -> str:
    diff, _ = _load_diff(diff_path)
    return diff


def _load_diff(diff_path: str) -> tuple[str, bool]:
    with open(diff_path, encoding="utf-8", errors="replace") as f:
        diff = f.read()
    truncated = False
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[... diff truncated by CI ...]\n"
        truncated = True
    return diff, truncated


def _build_body(review: str, footer: str, truncated: bool) -> str:
    body = review + footer
    if truncated:
        body = (
            f"_Diff truncated to {MAX_DIFF_CHARS} characters for the model._\n\n" + body
        )
    return body


def _post_or_fail(token: str, repo: str, pr_number: str, body: str) -> int:
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
