"""Unified diff parsing. The reviewer may only comment on lines this PR added or
changed, so the parser records, per file, which new-side line numbers exist."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Line:
    number: int  # line number on the new side
    text: str
    added: bool


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"  # added | modified | deleted | renamed | binary
    lines: list[Line] = field(default_factory=list)

    @property
    def added_lines(self) -> list[Line]:
        return [ln for ln in self.lines if ln.added]

    @property
    def commentable(self) -> set[int]:
        return {ln.number for ln in self.lines if ln.added}

    def numbered(self, context: bool = True) -> str:
        """The new-side lines with numbers, '+' on added lines, for the model prompt."""
        out = []
        for ln in self.lines:
            if ln.added or context:
                out.append(f"{ln.number:>5} {'+' if ln.added else ' '} {ln.text}")
        return "\n".join(out)


def parse_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_no = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+) b/(.+)", raw)
            current = FileDiff(path=m.group(2) if m else raw.split()[-1])
            files.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.status = "added"
        elif raw.startswith("deleted file mode"):
            current.status = "deleted"
        elif raw.startswith("rename from "):
            current.status, current.old_path = "renamed", raw[len("rename from ") :]
        elif raw.startswith("Binary files "):
            current.status = "binary"
        elif raw.startswith("+++ "):
            if raw[4:] != "/dev/null":
                current.path = raw[4:].removeprefix("b/")
        elif raw.startswith("--- "):
            continue
        elif m := _HUNK.match(raw):
            new_no = int(m.group(3))
        elif raw.startswith("+"):
            current.lines.append(Line(new_no, raw[1:], True))
            new_no += 1
        elif raw.startswith(" "):
            current.lines.append(Line(new_no, raw[1:], False))
            new_no += 1
        # '-' lines exist only on the old side; '\ No newline' markers are ignored
    return files
