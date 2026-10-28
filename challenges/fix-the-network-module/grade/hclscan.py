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
# Not preceded by another identifier character (HCL identifiers may hold
# letters of any script, digits, _ and -), so "profile(" is not "file(".
CALL_RE = re.compile(
    r"(?<![\w-])(" + "|".join(FUNCTIONS) + r")(?:\s|/\*.*?\*/|#[^\n]*|//[^\n]*)*\(",
    re.DOTALL,
)


def calls(src: str) -> list[str]:
    return [m.group(1) for m in CALL_RE.finditer(src)]


def main() -> int:
    found = []
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for name in calls(fh.read()):
                found.append(f"{path.rsplit('/', 1)[-1]} has {name}(")
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
