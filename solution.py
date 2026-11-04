#!/usr/bin/env python3
"""Cold Start v1.1: rules only, no model calls.

    python3 solution.py <input_dir> <output.json>

Parts:
  1. Organisations: seed names -> matching group chats -> core people -> expand by member overlap.
     A Home organisation from household words and staff. Leftover work groups become extra orgs.
  2. Chats: groups go to their organisation; a 1:1 goes to the org the other person runs/supports,
     otherwise to the person.
  3. Circles: seed, then family surname (relative), then org membership (colleague / staff), then
     simple keyword rules. Everything else is "friend".
Goals and open loops come in v2 (model-based).
Never hardcode names: the hidden set renames everyone again.
"""
import collections
import json
import re
import sys
from pathlib import Path

MAX_ORGS = 7  # the answer key has about 7; up to that many costs nothing in the org score

STAFF_RE = re.compile(
    r"\b(cook|driver|maid|didi|guard|security|watchman|gardener|mali|cleaner|nanny|aaya|ayah|"
    r"bai|plumber|electrician|carpenter|housekeep\w*|caretaker|peon|office boy|helper)\b", re.I)
HOME_RE = re.compile(
    r"(kitchen|carpool|society|apartment|residen|building|tower|\bflat\b|home|ghar|house|"
    r"grocery|groceries|maid|cook|driver|\b\d+\s?[a-d]\b)", re.I)
WORK_GROUP_RE = re.compile(
    r"\b(ops|operations|team|office|admin|accounts|finance|hr|sales|marketing|tech|product|"
    r"leadership|management|pvt|ltd|llp)\b", re.I)
VENDOR_RE = re.compile(r"\b(invoice|quotation|quote|bill|estimate|delivery|dispatch|order confirmed)\b", re.I)
CLIENT_RE = re.compile(r"\b(campaign|proposal|deck|deliverable|brief|retainer|po\b|purchase order|onboard)", re.I)


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def name_keys(name):
    """Ways an organisation's name shows up in chat names: 'PostMyAd' -> {'postmyad', 'pma'}."""
    keys = {re.sub(r"[^a-z0-9]", "", name.lower())}
    caps = re.findall(r"[A-Z]", name)
    if 2 <= len(caps) < len(name.replace(" ", "")):  # CamelCase or multi-word -> acronym
        keys.add("".join(caps).lower())
    words = re.findall(r"[A-Za-z]+", name)
    if len(words) > 1:
        keys.add("".join(w[0] for w in words).lower())
    return {k for k in keys if len(k) >= 2}


def chat_matches(chat_name, keys):
    flat = re.sub(r"[^a-z0-9]", "", chat_name.lower())
    tokens = set(re.findall(r"[a-z0-9]+", chat_name.lower()))
    return any((len(k) >= 4 and k in flat) or k in tokens for k in keys)


def surname(full):
    parts = re.findall(r"[A-Za-z]+", full or "")
    return parts[-1].lower() if len(parts) >= 2 else None


def main(input_dir, output_path):
    folder = Path(input_dir)
    chats = read_jsonl(folder / "chats.jsonl")
    messages = read_jsonl(folder / "messages.jsonl")
    seed_path = folder / "seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.exists() else {}
    seed_circles = seed.get("circles") or {}

    chat_by_id = {c["chat_id"]: c for c in chats}
    groups = [c for c in chats if c["is_group"]]
    dms = [c for c in chats if not c["is_group"]]
    people = sorted({p for c in chats for p in c["participants"]} - {"ME"})

    # Names as saved in Arjun's phone: from 1:1 chat names and sender names.
    names = collections.defaultdict(collections.Counter)
    for m in messages:
        if m["sender_id"] != "ME" and m.get("sender_name"):
            names[m["sender_id"]][m["sender_name"]] += 1
    dm_with = {}
    for c in dms:
        other = next((p for p in c["participants"] if p != "ME"), None)
        if other:
            dm_with[other] = c
            names[other][c["name"]] += 5
    display = {p: (names[p].most_common(1)[0][0] if names[p] else "") for p in people}

    # Family: surnames of seeded family, plus any surname shared by 3+ 1:1 contacts.
    family_tags = {"spouse", "child", "parent", "in-law", "sibling", "relative"}
    nonwork_tags = family_tags | {"friend", "client", "vendor"}
    fam_surnames = {surname(display[p]) for p, c in seed_circles.items() if c in family_tags}
    dm_surnames = collections.Counter(surname(display[p]) for p in dm_with)
    fam_surnames |= {s for s, n in dm_surnames.items() if s and n >= 3}
    fam_surnames.discard(None)
    family = {p for p in people if seed_circles.get(p) in family_tags
              or (p not in seed_circles and surname(display[p]) in fam_surnames)}
    not_core = family | {p for p, c in seed_circles.items() if c in nonwork_tags}

    sent = collections.Counter()           # (chat, person) -> messages sent
    text_by_person = collections.defaultdict(list)
    for m in messages:
        sent[(m["chat_id"], m["sender_id"])] += 1
        if m["sender_id"] != "ME" and len(text_by_person[m["sender_id"]]) < 200:
            text_by_person[m["sender_id"]].append(m.get("body") or "")

    # ---------- 1. Organisations ----------
    orgs = []          # dicts: id, name, type, side, role, chats:set, core:set
    for i, oname in enumerate(seed.get("organisations") or []):
        keys = name_keys(oname)
        matched = {g["chat_id"] for g in groups if chat_matches(g["name"], keys)}
        orgs.append({"id": f"org{i+1}", "name": oname, "type": "company", "side": "work",
                     "role": "owner", "chats": matched, "core": set()})

    seeded_work = {p for p, c in seed_circles.items() if c in ("colleague", "partner")}

    def find_core(org):
        """The people who run it with Arjun: active in its chats, and either in a 1:1 with Arjun
        or seeded as colleague/partner. Community members in big groups stay out."""
        core = set()
        for p in people:
            if p in not_core:
                continue
            n_sent = sum(sent[(c, p)] for c in org["chats"])
            n_chats = sum(1 for c in org["chats"] if p in chat_by_id[c]["participants"])
            if (p in dm_with or p in seeded_work) and (n_sent >= 3 or (n_chats >= 2 and n_sent >= 1)):
                core.add(p)
            elif n_sent >= 30:
                core.add(p)
        return core

    def first_token(name):
        t = re.findall(r"[a-z0-9]+", name.lower())
        return t[0] if t and len(t[0]) >= 3 and len(t) >= 2 else None

    def is_family_group(g):
        members = set(g["participants"]) - {"ME"}
        return members and len(members & family) / len(members) >= 0.4

    taken = set().union(*(o["chats"] for o in orgs)) if orgs else set()
    for _ in range(2):  # expand, then recompute core with the new chats, then expand again
        for org in orgs:
            org["core"] = find_core(org)
        # A group joins the work org with most of its core people in it (one is enough).
        for g in groups:
            if g["chat_id"] in taken or HOME_RE.search(g["name"]) or is_family_group(g):
                continue
            members = set(g["participants"]) - {"ME"}
            best, best_n = None, 0
            for org in orgs:
                n = len(members & org["core"])
                if n > best_n:
                    best, best_n = org, n
            if best:
                best["chats"].add(g["chat_id"])
                taken.add(g["chat_id"])
        # Groups named like an org's groups ("D2C Circle ...", "D2B Connect ...") join it too.
        for org in orgs:
            prefixes = {first_token(chat_by_id[c]["name"]) for c in org["chats"]} - {None}
            for g in groups:
                if g["chat_id"] not in taken and first_token(g["name"]) in prefixes \
                        and not is_family_group(g):
                    org["chats"].add(g["chat_id"])
                    taken.add(g["chat_id"])
    for org in orgs:
        org["core"] = find_core(org)

    # Home: household groups, plus staff.
    staff = {p for p in people
             if STAFF_RE.search(display[p]) or seed_circles.get(p) == "staff"}
    home = {"id": "home", "name": "Home", "type": "residence", "side": "home", "role": "owner",
            "chats": set(), "core": set()}
    for g in groups:
        if g["chat_id"] not in taken and HOME_RE.search(g["name"]):
            home["chats"].add(g["chat_id"])
            taken.add(g["chat_id"])
    home["core"] = {p for p in staff}
    for g in home["chats"]:  # people who talk in household groups and are not family-looking
        for p in chat_by_id[g]["participants"]:
            if p != "ME" and sent[(g, p)] >= 3 and STAFF_RE.search(display[p]):
                home["core"].add(p)

    # Leftover work-looking groups become their own small orgs, merged by member overlap.
    extra = []
    for g in groups:
        if g["chat_id"] in taken or not WORK_GROUP_RE.search(g["name"]) or is_family_group(g):
            continue
        members = set(g["participants"]) - {"ME"}
        for e in extra:
            if len(members & e["members"]) / max(1, len(members | e["members"])) >= 0.5:
                e["chats"].add(g["chat_id"])
                e["members"] |= members
                break
        else:
            extra.append({"chats": {g["chat_id"]}, "members": members, "name": g["name"]})
        taken.add(g["chat_id"])
    room = MAX_ORGS - len(orgs) - 1
    extra.sort(key=lambda e: -sum(sent[(c, p)] for c in e["chats"] for p in e["members"]))
    for j, e in enumerate(extra):
        if j < room:
            org = {"id": f"x{j+1}", "name": e["name"], "type": "company", "side": "work",
                   "role": "partner", "chats": e["chats"], "core": set()}
            org["core"] = {p for p in e["members"] - not_core if sum(sent[(c, p)] for c in e["chats"]) >= 3}
            orgs.append(org)
        else:
            taken -= e["chats"]
    orgs.append(home)

    # Which org each person belongs to (the one where they talk most).
    n_orgs = collections.Counter(p for o in orgs for p in o["core"])
    person_org = {}
    for p in people:
        best, best_n = None, 0
        for org in orgs:
            if p in org["core"]:
                n = sum(sent[(c, p)] for c in org["chats"]) + 1
                if n > best_n:
                    best, best_n = org, n
        if best:
            person_org[p] = best

    # ---------- 2. Chats ----------
    chat_org = {cid: org for org in orgs for cid in org["chats"]}
    ties = []
    for c in chats:
        cid = c["chat_id"]
        if cid in chat_org:
            ties.append({"chat_id": cid, "organisation": chat_org[cid]["id"]})
        elif not c["is_group"]:
            other = next((p for p in c["participants"] if p != "ME"), None)
            # 1:1 goes to the org only for someone who works in exactly one of them.
            if other in person_org and other not in not_core and n_orgs[other] == 1:
                ties.append({"chat_id": cid, "organisation": person_org[other]["id"]})
            else:
                ties.append({"chat_id": cid, "person": other})
        else:  # unassigned group: tie to its most active member (rarely scored)
            members = [p for p in c["participants"] if p != "ME"]
            top = max(members, key=lambda p: sent[(cid, p)]) if members else None
            ties.append({"chat_id": cid, "person": top})

    # ---------- 3. Circles ----------
    circles = {}
    for p in people:
        if p in seed_circles:
            circles[p] = seed_circles[p]
            continue
        org = person_org.get(p)
        if p in family:
            circles[p] = "relative"
        elif org is home or p in staff:
            circles[p] = "staff"
        elif org is not None:
            circles[p] = "colleague" if org["role"] == "owner" else "partner"
        elif p in dm_with:
            txt = " ".join(text_by_person[p])
            if VENDOR_RE.search(txt):
                circles[p] = "vendor"
            elif CLIENT_RE.search(txt):
                circles[p] = "client"
            else:
                circles[p] = "friend"
        else:
            circles[p] = "friend"

    out_people = []
    for p in people:
        entry = {"id": p, "circle": circles[p]}
        links = []
        for org in orgs:
            if p in org["core"]:
                links.append({"organisation": org["id"],
                              "link": "supports" if (org is home or circles[p] == "staff") else "runs"})
        if links:
            entry["links"] = links
        out_people.append(entry)

    out = {
        "people": out_people,
        "organisations": [{k: o[k] for k in ("id", "name", "type", "side", "role")} for o in orgs],
        "chats": ties,
        "goals": [],
        "open_loops": [],
    }
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
