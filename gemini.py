#!/usr/bin/env python
"""Gemini wrapper for BRUTUS_SETUP_CMD (the cheap repo-setup planner).

Reads a prompt on stdin, prints the completion on stdout. Gemini exposes an
OpenAI-compatible endpoint, so we reuse the OpenAI SDK like glm.py.

Configure via .env:
    GEMINI_API_KEY, GEMINI_MODEL
    BRUTUS_SETUP_CMD=.venv/bin/python gemini.py
"""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def main() -> None:
    load_dotenv()
    prompt = sys.stdin.read()
    client = OpenAI(
        api_key=os.environ["GEMINI_API_KEY"],
        base_url=os.environ.get("GEMINI_BASE_URL", GEMINI_BASE_URL),
    )
    response = client.chat.completions.create(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        messages=[{"role": "user", "content": prompt}],
        temperature=float(os.environ.get("GEMINI_TEMPERATURE", "0")),
    )
    sys.stdout.write(response.choices[0].message.content or "")


if __name__ == "__main__":
    main()
