#!/usr/bin/env python3
"""Non-interactive Git HTTPS credential helper for the coder subprocess."""

import os
import sys

prompt = " ".join(sys.argv[1:]).casefold()
if "username" in prompt:
    print("x-access-token")
elif "password" in prompt:
    print(os.getenv("GITHUB_TOKEN", ""))
