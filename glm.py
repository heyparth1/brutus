#!/usr/bin/env python
"""GLM (via OpenCode Zen) wrapper for BRUTUS_LLM_CMD.

Reads a prompt on stdin, prints the completion on stdout — the contract brutus
expects. Zen serves GLM through an OpenAI-compatible endpoint, so we just point
the OpenAI SDK at it.

Configure via .env (see .env in the repo root):
    OPENCODE_API_KEY, LLM_BASE_URL, GLM_MODEL, GLM_TEMPERATURE
    BRUTUS_LLM_CMD=.venv/bin/python glm.py
"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI


def main() -> None:
    load_dotenv()
    prompt = sys.stdin.read()
    client = OpenAI(
        api_key=os.environ["OPENCODE_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://opencode.ai/zen/v1"),
    )
    response = client.chat.completions.create(
        model=os.environ.get("GLM_MODEL", "glm-5.2"),
        messages=[{"role": "user", "content": prompt}],
        temperature=float(os.environ.get("GLM_TEMPERATURE", "0")),  # deterministic scoring
    )
    sys.stdout.write(response.choices[0].message.content or "")


if __name__ == "__main__":
    main()
