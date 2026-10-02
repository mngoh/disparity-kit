"""Recognize what a dataset's columns hold, from their names, descriptions and values.

Shared by sources.py (remote datasets) and schema.py (downloaded files). Every guess comes back
with its evidence so a person can check it; nothing here decides what belongs in an analysis.
"""
import re

# column roles, each a list of token rules in priority order; a rule matches when all its tokens appear
ROLES = {
    "date": [["date", "occ"], ["occ", "date"], ["occurred"], ["occurrence"], ["incident", "date"], ["offense", "date"], ["crime", "date"],
             ["from", "date"], ["start", "date"], ["date"], ["datetime"], ["report", "date"], ["date", "rptd"], ["reported"]],
    "race": [["race"], ["descent"], ["descnt"]],
    "ethnicity": [["ethnicity"], ["ethnic"], ["hispanic"], ["latino"], ["eth"]],
    "sex": [["sex"], ["gender"]],
    "age": [["age"], ["agenum"]],
    "lat": [["latitude"], ["lat"], ["y"]],
    "lon": [["longitude"], ["lon"], ["long"], ["lng"], ["x"]],
    "code": [["crm", "cd"], ["crime", "code"], ["offense", "code"], ["ofns", "cd"], ["nibr", "code"], ["nibrs", "code"], ["nibrs"],
             ["ucr", "code"], ["ucr"], ["offense", "cd"], ["fbi", "cd"], ["fbi", "code"], ["iucr", "cd"], ["iucr"], ["statute"], ["charge", "code"], ["offense"]],
    "desc": [["crm", "cd", "desc"], ["offense", "desc"], ["ofns", "desc"], ["crime", "desc"], ["nibr", "description"], ["nibrs", "description"],
             ["offense", "description"], ["offense", "name"], ["fbi", "descr"], ["offense", "descr"], ["crime", "type"], ["offense", "type"],
             ["primary", "type"], ["description"], ["desc"], ["descr"], ["primary"]],
    "district": [["area", "name"], ["division"], ["district", "name"], ["precinct"], ["district"], ["area"], ["patrol"], ["sector"], ["beat"], ["zone"], ["ward"]],
    "premise": [["premis", "desc"], ["premise", "desc"], ["premises"], ["premise"], ["premis"], ["location", "type"], ["loc", "type"], ["location", "desc"], ["location", "description"], ["place", "type"]],
    "weapon": [["weapon", "desc"], ["weapon", "description"], ["weapon"], ["weapn"]],
    "id": [["dr", "no"], ["uniquevictimno"], ["victim", "id"], ["caseno"], ["case", "number"], ["incident", "id"], ["incident", "number"],
           ["report", "number"], ["case"], ["objectid"], ["id"]],
    "victim_type": [["victim", "type"], ["vic", "type"]],
}
# tokens that disqualify a column for a role even when its rule matches
NOT = {"date": {"hour", "updated", "modified", "edited", "created", "load"}, "age": {"page", "usage", "agency"},
       "code": {"zip", "district", "area", "premis", "premise", "weapon", "status", "location", "mo", "mocodes", "desc", "description", "name"},
       "desc": {"premis", "premise", "weapon", "status", "location", "mo", "victim", "iucr", "secondary"},
       "district": {"rpt", "reporting", "council", "school", "type", "code", "cd", "num", "no", "id", "state", "house", "senate",
                    "congressional", "community", "fire", "water"} | {"x", "y"},
       "id": {"area", "district", "premis", "weapon", "crm", "status", "zip"}, "lat": {"lon", "long"}, "lon": {"lat"},
       "race": {"officer"}, "sex": {"officer"}, "age": {"officer"}}
# whose attribute a race, sex or age column records
SUBJECTS = [("victim", {"vict", "victim", "vic", "victims", "victimization"}), ("suspect", {"susp", "suspect", "offender", "offenders", "off", "perp"}),
            ("arrestee", {"arrest", "arrestee", "arrested", "defendant", "booking"}), ("officer", {"officer", "ofc", "member"}),
            ("person stopped", {"subject", "driver", "stopped", "person", "civilian"})]


def tokens(name):
    """vict_age -> {vict, age}; VictimAge -> {victim, age}; NIBR Code -> {nibr, code}."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def subject(name, description=""):
    toks = set(tokens(name))
    for label, keys in SUBJECTS:
        if toks & keys:
            return label
    d = description.lower()
    for label, keys in SUBJECTS:
        if any(re.search(rf"\b{k}", d) for k in keys if len(k) > 3):
            return label
    return None


def role_rank(name, role):
    """Priority of the first rule that matches this column name (lower is better), or None."""
    toks = tokens(name)
    ts = set(toks)
    if ts & NOT.get(role, set()):
        return None
    if role == "date" and "time" in ts and "date" not in ts:
        return None  # time_occ is a time of day; CrimeDateTime is a date
    for i, rule in enumerate(ROLES[role]):
        if all(t in ts for t in rule):
            if role in ("lat", "lon") and rule in (["y"], ["x"]) and len(toks) > 2:
                return None
            return i
    return None


def guess_roles(columns):
    """columns: list of {"field", "name", "type", "description"}. Returns role -> best field, and every candidate per role."""
    cands = {}
    for c in columns:
        for role in ROLES:
            r = min([x for x in (role_rank(c["field"], role), role_rank(c.get("name") or "", role)) if x is not None], default=None)
            if r is None:
                continue
            t = (c.get("type") or "").lower()
            if role == "date" and t and not any(k in t for k in ("date", "time", "text", "string")):
                continue
            if role in ("lat", "lon") and t and not any(k in t for k in ("number", "double", "float", "int", "text", "string")):
                continue
            # among equal matches, prefer the victim's column ("victimization_fbi_cd" over "incident_fbi_cd")
            cands.setdefault(role, []).append((r, 0 if subject(c["field"]) == "victim" else 1, c["field"]))
    best = {}
    for role, lst in cands.items():
        lst.sort()
        best[role] = lst[0][-1]
    # people columns: prefer the victim's when there are several
    for role in ("race", "sex", "age", "ethnicity"):
        lst = [x[-1] for x in sorted(cands.get(role, []))]
        vict = [f for f in lst if subject(f) == "victim"]
        if vict:
            best[role] = vict[0]
    if best.get("desc") and best.get("desc") == best.get("code"):
        best.pop("desc")
    return best, {role: [x[-1] for x in sorted(lst)] for role, lst in cands.items()}


# "A - Other Asian B - Black C - Chinese", "1 = White, 2 = Black", "F: Female; M: Male"
LEGEND = re.compile(r"(?:(?<=[\s,;(])|^)([A-Z0-9]{1,3})\s*(?:-|=|:|–)\s+")


def parse_legend(text):
    """Code legends written into a column description. Returns {code: label} or {} when fewer than two codes are found."""
    if not text:
        return {}
    text = " ".join(str(text).split())
    hits = list(LEGEND.finditer(text))
    out = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        label = text[m.end():end].strip(" ,;.")
        if label and len(label) < 60:
            out[m.group(1)] = label
    return out if len(out) >= 2 else {}


# label -> ACS group. Order matters: Hispanic before Black and White ("WHITE HISPANIC"),
# American Indian before Asian ("ASIAN INDIAN" is Asian), combined Asian/Pacific Islander flagged.
RACE_RULES = [
    ("unknown", r"\bUNK|NOT (RECORDED|STATED|REPORTED|SPECIFIED|AVAILABLE)|REFUSED|^NONE$|^NULL$|^N/?A$|^-$|^\?$|^X$|^U$"),
    ("ambiguous: Asian or Pacific Islander", r"ASIAN.*PACIFIC|PACIFIC.*ASIAN|\bAPI\b|ASIAN/PI|A/PI"),
    ("Hispanic", r"HISPANIC|LATIN|MEXICAN|CHICAN|PUERTO RIC|CUBAN|SALVADOR|GUATEMAL|SPANISH"),
    ("AIAN", r"AMERICAN INDIAN|ALASKA|NATIVE AMERICAN|INDIGENOUS|INDIAN/ALASKAN"),
    ("NHPI", r"PACIFIC|HAWAIIAN|SAMOAN|GUAMANIAN|CHAMORRO|TONGAN|FIJIAN|MARSHALLESE"),
    ("Asian", r"ASIAN|CHINESE|JAPANESE|KOREAN|FILIPINO|VIETNAMESE|CAMBODIAN|LAOTIAN|HMONG|THAI|INDIAN|PAKISTANI|BANGLADESHI|TAIWANESE|INDONESIAN|MALAYSIAN|BURMESE|NEPALESE"),
    ("Black", r"BLACK|AFRICAN"),
    ("White", r"WHITE|CAUCASIAN"),
    ("Multiracial", r"MULTI|TWO OR MORE|MIXED|BIRACIAL|MORE THAN ONE"),
    ("no ACS group: Middle Eastern or North African", r"MIDDLE EAST|ARAB|NORTH AFRICA|MENA"),
    ("no ACS group: other", r"^OTHER$|^O$|OTHER RACE|SOME OTHER"),
]
# common single-letter codes when no legend says otherwise (NIBRS uses W B I A P U, plus H for ethnicity)
# and common abbreviations (Chicago: BLK, WHI, WWH white Hispanic, WBH black Hispanic, API)
CONVENTION = {"W": "White", "B": "Black", "H": "Hispanic", "A": "Asian", "I": "AIAN", "P": "NHPI", "U": "unknown", "X": "unknown",
              "BLK": "Black", "BLA": "Black", "WHI": "White", "WHT": "White", "WWH": "Hispanic", "WBH": "Hispanic", "HIS": "Hispanic",
              "HSP": "Hispanic", "HISP": "Hispanic", "ASN": "Asian", "ASI": "Asian", "API": "ambiguous: Asian or Pacific Islander", "UNK": "unknown"}


def race_group(label):
    s = " ".join(str(label).upper().split())
    for group, pat in RACE_RULES:
        if re.search(pat, s):
            return group
    return None


def propose_race_map(values, legend):
    """values: {raw value: count}. Returns rows of {value, n, label, group, basis}; basis is legend, label or convention."""
    rows = []
    for v, n in values.items():
        v = str(v).strip()
        if v in legend:
            label, group, basis = legend[v], race_group(legend[v]), "legend"
        elif race_group(v) and len(v) > 2:
            label, group, basis = v, race_group(v), "label"
        elif v.upper() in CONVENTION:
            label, group, basis = v, CONVENTION[v.upper()], "convention (no legend: confirm)"
        else:
            label, group, basis = v, race_group(v), "label" if race_group(v) else "not recognized"
        if v == "" or v.lower() in ("nan", "none", "null"):
            label, group, basis = "(blank)", "unknown", "blank"
        rows.append({"value": v, "n": int(n), "label": label, "group": group, "basis": basis})
    return sorted(rows, key=lambda r: -r["n"])


def sex_code(label):
    s = str(label).strip().upper()
    if s in ("F", "FEMALE", "WOMAN", "GIRL") or s.startswith("FEMALE"):
        return "F"
    if s in ("M", "MALE", "MAN", "BOY") or s.startswith("MALE"):
        return "M"
    return None


def propose_sex_map(values, legend):
    rows = []
    for v, n in values.items():
        v = str(v).strip()
        label = legend.get(v, v)
        rows.append({"value": v, "n": int(n), "label": label, "code": sex_code(label) or sex_code(v)})
    return sorted(rows, key=lambda r: -r["n"])


def ethnicity_code(label):
    s = str(label).upper()
    if re.search(r"NOT|NON|^N$", s):
        return "not Hispanic"
    if re.search(r"HISPANIC|LATIN|^H$|^Y$|^YES$", s):
        return "Hispanic"
    if re.search(r"UNK|^U$|^X$", s):
        return "unknown"
    return None
