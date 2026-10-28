"""Find what a submitted module may not contain: file functions and provider blocks.

  hclscan.py FILE...    prints one line per finding, exits 1 if any

The grader's hidden tests rely on the module not reading files: with file()
it could read a test file, or /proc, and learn values it should not know.
And a provider block of its own (even an aliased one) could point the AWS
provider at a shared config file with a credential_process, which runs a
program during plan. The module needs neither: it reads no files, and the
caller configures the provider.

In HCL a function call is the function's name, then "(", with only spaces,
newlines and comments in between. This looks for exactly that in the raw
text, without trying to tell code from comments and strings: a call can
never hide, and a comment or string that happens to look like a call is
reported too, so the author can reword it.
"""

from __future__ import annotations

import re
import sys

FUNCTIONS = ("file", "fileexists", "fileset", "filebase64", "filebase64sha256", "filebase64sha512", "filemd5",
             "filesha1", "filesha256", "filesha512", "templatefile")
# A whole name: not part of a longer word. A "-" before the name does not
# count: "1-file(" and "-file(" are real calls, and an identifier such as
# "my-file(" is reported too, which only errs on the safe side.
NAME_RE = re.compile(r"(?<!\w)(" + "|".join(FUNCTIONS) + r")(?!\w)")


def next_code(src: str) -> list[int]:
    """For each position, where the next character after spaces and comments is.

    One pass from the end, so the scan stays linear however the comments
    are arranged.
    """
    n = len(src)
    newline = [n] * (n + 1)
    close = [n] * (n + 2)
    for i in range(n - 1, -1, -1):
        newline[i] = i if src[i] == "\n" else newline[i + 1]
        close[i] = i if src.startswith("*/", i) else close[i + 1]
    after = [n] * (n + 1)
    for i in range(n - 1, -1, -1):
        ch = src[i]
        if ch.isspace():
            after[i] = after[i + 1]
        elif ch == "#" or src.startswith("//", i):
            after[i] = after[newline[i]]
        elif src.startswith("/*", i):
            end = close[i + 2]
            after[i] = after[end + 2] if end < n else n
        else:
            after[i] = i
    return after


PROVIDER_RE = re.compile(r"(?<!\w)provider(?!\w|-|::)")
LABEL_RE = re.compile(r'"[^"\n]*"|[^\W\d][\w-]*')


def provider_blocks(src: str) -> int:
    """Block headers such as provider "aws" { or provider aws {, in the raw text.

    A label with an escaped quote is not matched, but no provider can be
    named that way, so Terraform would refuse the block anyway.
    """
    after = next_code(src)
    found = 0
    for match in PROVIDER_RE.finditer(src):
        label = LABEL_RE.match(src, after[match.end()])
        if label and after[label.end()] < len(src) and src[after[label.end()]] == "{":
            found += 1
    return found


def calls(src: str) -> list[str]:
    after = next_code(src)
    found = []
    for match in NAME_RE.finditer(src):
        nxt = after[match.end()]
        if nxt < len(src) and src[nxt] == "(":
            found.append(match.group(1))
    return found


def main() -> int:
    found = []
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        name_of_file = path.rsplit("/", 1)[-1]
        for name in calls(src):
            found.append(f"{name_of_file} has {name}()")
        if provider_blocks(src):
            found.append(f"{name_of_file} has a provider block")
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
