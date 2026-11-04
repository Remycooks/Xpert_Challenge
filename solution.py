#!/usr/bin/env python3
"""A starting point: reads the input and writes a valid, deliberately naive output.json.

It puts everyone in the "friend" circle, makes each group chat its own organisation, ties every
1:1 chat to the other person, and finds no goals or loops. It scores badly on purpose. Replace it.

    python3 solution.py <input_dir> <output.json>
"""
import json
import sys
from pathlib import Path


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(input_dir, output_path):
    folder = Path(input_dir)
    chats = read_jsonl(folder / "chats.jsonl")
    messages = read_jsonl(folder / "messages.jsonl")  # noqa: F841 - you'll want these

    people = sorted({p for c in chats for p in c["participants"]} - {"ME"})
    organisations, ties = [], []
    for c in chats:
        if c["is_group"]:
            org_id = "org_" + c["chat_id"]
            organisations.append({"id": org_id, "name": c["name"], "type": "other", "side": "work", "role": "member"})
            ties.append({"chat_id": c["chat_id"], "organisation": org_id})
        else:
            other = next(p for p in c["participants"] if p != "ME")
            ties.append({"chat_id": c["chat_id"], "person": other})

    out = {
        "people": [{"id": p, "circle": "friend"} for p in people],
        "organisations": organisations,
        "chats": ties,
        "goals": [],
        "open_loops": [],
    }
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
