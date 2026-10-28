"""Find calls to file-reading functions in Terraform files.

  hclscan.py FILE...    prints one line per name found, exits 1 if any

The grader's hidden tests rely on the module not reading files: with file()
it could read a test file, or /proc, and learn values it should not know.
The module has no need to read files, so these functions are not allowed.

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
            for name in calls(fh.read()):
                found.append(f"{path.rsplit('/', 1)[-1]} has {name}()")
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
