"""The READMEs render on GitHub as written: bold labels in Chinese text close properly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# CommonMark (GitHub) closes ** after a punctuation mark only before a space or more punctuation, so
# "**网格：**按" shows its asterisks; "**网格**：按" is bold.
BROKEN_BOLD = re.compile(r"[：；，。、！？）」』”]\*\*(?=[^\s*\[\]（）(),.:;!?、，。：；！？「」『』“”`<>/|-])")


@pytest.mark.parametrize("name", ["README.md", "README.zh-CN.md"])
def test_bold_labels_close_where_github_expects(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    bad = [f"{n}: {line[:60]}" for n, line in enumerate(text.splitlines(), 1) if BROKEN_BOLD.search(line)]
    assert not bad, "move the punctuation outside the bold (**标签**：):\n" + "\n".join(bad)
