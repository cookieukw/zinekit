"""Every text the editor can show, collected from the code (used by test_i18n and to update locales/)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "zinekit"
CALL = re.compile(r"\b(?:tr|N_)\(\s*(\"(?:[^\"\\\n]|\\.)*\")\s*\)")
LOOSE = re.compile(r"\b(?:tr|N_)\(\s*[\"']")


def from_code():
    keys, problems = set(), []
    for f in sorted(ROOT.rglob("*.py")):
        text = f.read_text(encoding="utf-8")
        found = list(CALL.finditer(text))
        keys.update(ast.literal_eval(m.group(1)) for m in found)
        if len(LOOSE.findall(text)) != len(found):      # e.g. tr('x') or tr("a" "b")
            problems.append(str(f.relative_to(ROOT)))
    return keys, problems


def from_tables():
    from zinekit import params as P
    from zinekit import presets as PR
    keys = set(P.GROUPS) | set(PR.BUILTIN)
    for p in P.PARAMS:
        keys.add(p.label)
        keys.update(p.options)
        if p.tip:
            keys.add(p.tip)
    return keys


def all_keys():
    code, problems = from_code()
    return code | from_tables(), problems


if __name__ == "__main__":
    keys, problems = all_keys()
    print(len(keys), "keys;", "unparsed tr() calls in:", problems or "none")
