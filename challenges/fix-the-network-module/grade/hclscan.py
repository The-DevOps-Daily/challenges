"""Find calls to file-reading functions in Terraform files.

  hclscan.py FILE...    prints one line per call found, exits 1 if any

The grader's hidden tests rely on the module not reading files: with file()
it could read a test file, or /proc, and learn values it should not know.
The module has no need to read files, so these functions are not allowed.

The scan blanks out comments and the literal text of strings and heredocs,
but keeps ${...} and %{...} template code, where calls can also be. It is
deliberately simple; anything it cannot follow is treated as code, which can
only find more calls, never fewer.
"""

from __future__ import annotations

import re
import sys

FUNCTIONS = ("file", "fileexists", "fileset", "filebase64", "filebase64sha256", "filebase64sha512", "filemd5",
             "filesha1", "filesha256", "filesha512", "templatefile")
CALL_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(FUNCTIONS) + r")\s*\(")
HEREDOC_RE = re.compile(r"<<-?([A-Za-z_][A-Za-z0-9_-]*)[ \t]*\n")


def code_only(src: str) -> str:
    """The text with comments and literal string text replaced by spaces."""
    out: list[str] = []
    stack: list[tuple[str, str | None]] = []  # templates we are inside: ("string", None) or ("heredoc", marker)
    depth: list[int] = []  # open braces in each ${...} we are inside
    i, n = 0, len(src)

    def in_template_text() -> bool:
        return bool(stack) and len(stack) > len(depth)

    while i < n:
        if in_template_text():
            kind, marker = stack[-1]
            ch = src[i]
            if kind == "string" and ch == "\\":
                out.append("  ")
                i += 2
                continue
            if src.startswith("$${", i) or src.startswith("%%{", i):
                out.append("   ")
                i += 3
                continue
            if src.startswith("${", i) or src.startswith("%{", i):
                out.append("  ")
                depth.append(0)
                i += 2
                continue
            if kind == "string" and ch == '"':
                stack.pop()
                out.append(" ")
                i += 1
                continue
            if kind == "heredoc" and (i == 0 or src[i - 1] == "\n"):
                line_end = src.find("\n", i)
                line = src[i:] if line_end < 0 else src[i:line_end]
                if line.strip() == marker:
                    stack.pop()
                    out.append(" " * len(line))
                    i += len(line)
                    continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        ch = src[i]
        if ch == "#" or src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(" " * (end - i))
            i = end
            continue
        if ch == '"':
            stack.append(("string", None))
            out.append(" ")
            i += 1
            continue
        heredoc = HEREDOC_RE.match(src, i)
        if heredoc:
            stack.append(("heredoc", heredoc.group(1)))
            out.append(" " * (heredoc.end() - i))
            i = heredoc.end()
            continue
        if depth and ch == "{":
            depth[-1] += 1
        elif depth and ch == "}":
            if depth[-1] == 0:
                depth.pop()
                out.append(" ")
                i += 1
                continue
            depth[-1] -= 1
        out.append(ch)
        i += 1
    return "".join(out)


def calls(src: str) -> list[str]:
    return [m.group(1) for m in CALL_RE.finditer(code_only(src))]


def main() -> int:
    found = []
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for name in calls(fh.read()):
                found.append(f"{path.rsplit('/', 1)[-1]} calls {name}()")
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
