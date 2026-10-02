"""FBI NIBRS state files: list agencies, flatten to one row per victim per offense, build victim files by kind.

For agencies whose own portal has no victim race. Download the state's yearly files from the FBI Crime Data
Explorer (Documents & Downloads, NIBRS data by state: https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads)
into data/raw/ as received, one zip per year. Each zip holds linked tables and a README describing them.

  python nibrs.py agencies data/raw/*.zip
  python nibrs.py flatten data/raw/*.zip [--ori DCMPD0000] --out data/interim/victim_offenses.csv
  python nibrs.py victims data/interim/victim_offenses.csv --ori DCMPD0000 --kind aggravated=13A --kind simple=13B
                  --race-rule hispanic-first --out-dir data --prefix dc_

agencies  every agency in the files: ORI, name, type, population, years and incidents, so the city's own agency can be
          picked (and transit, campus or housing police left out, or not, on purpose).
flatten   joins incident, offense, victim, victim-offense, victim-offender relationship, weapon and injury tables with
          their code lookups. Nothing is imputed. A victim of two offenses in one incident has two rows.
victims   one row per victim per kind: individuals only (officers, businesses and society are other victim types), age
          from the NIBRS age codes (under one year is 0, unknown stays missing), a partner flag from the relationship
          codes, and race_group by --race-rule. A victim listed under two kinds is kept in the first kind given.
  --race-rule hispanic-first   "H" when ethnicity is Hispanic, otherwise the recorded race (the ACS convention the
                               denominators use; unknown ethnicity keeps the recorded race). Say so in the caveats.
  --race-rule race             the recorded race only; Hispanic victims stay in their race group.
"""
import argparse
import io
import pathlib
import zipfile

import numpy as np
import pandas as pd

PARTNER = {"SE", "CS", "BG", "HR", "XS", "XR"}  # spouse, common-law, boyfriend/girlfriend, same-sex, ex-spouse, ex-relationship
INFANT = {"NN", "NB", "BB"}  # under 24 hours, 1 to 6 days, 7 to 364 days
UNKNOWN_AGE = {"00", "NS"}
# short labels for NIBRS location names, which are too long for chart axes on a phone; the raw name stays in `location`
PREMISE = {
    "Residence/Home": "Home", "Highway/Road/Alley/Street/Sidewalk": "Street & Sidewalk",
    "School-Elementary/Secondary": "K-12 School", "School-College/University": "College", "School/College": "School & College",
    "Government/Public Building": "Government Building", "Commercial/Office Building": "Office Building",
    "Other/Unknown": "Other & Unknown", "Air/Bus/Train Terminal": "Transit Station", "Parking/Drop Lot/Garage": "Parking Lot",
    "Hotel/Motel/Etc.": "Hotel & Motel", "Bar/Nightclub": "Bar & Nightclub", "Drug Store/Doctor's Office/Hospital": "Hospital & Clinic",
    "Department/Discount Store": "Department Store", "Grocery/Supermarket": "Grocery Store", "Park/Playground": "Park & Playground",
    "Shelter-Mission/Homeless": "Shelter", "Jail/Prison/Penitentiary/Corrections Facility": "Jail & Prison",
    "Church/Synagogue/Temple/Mosque": "Religious Building", "Service/Gas Station": "Gas Station", "Field/Woods": "Field & Woods",
    "Lake/Waterway/Beach": "Waterway & Beach", "Arena/Stadium/Fairgrounds/Coliseum": "Arena & Stadium",
    "Bank/Savings and Loan": "Bank", "ATM Separate from Bank": "Cash Machine", "Abandoned/Condemned Structure": "Abandoned Building",
    "Rental Storage Facility": "Storage Facility", "Auto Dealership New/Used": "Auto Dealership", "Camp/Campground": "Campground",
    "Dock/Wharf/Freight/Modal Terminal": "Dock & Wharf", "Gambling Facility/Casino/Race Track": "Casino & Race Track",
    "Military Installation": "Military Base", "Farm Facility": "Farm",
}


def reader(path):
    """Read tables from one state-year zip by name, wherever they sit inside it (some years nest a folder or two)."""
    z = zipfile.ZipFile(path)
    names = {pathlib.PurePosixPath(n).name.lower(): n for n in z.namelist() if n.lower().endswith(".csv")}

    def read(table, usecols=None, **kw):
        d = pd.read_csv(io.BytesIO(z.read(names[f"{table.lower()}.csv"])), low_memory=False, **kw)
        d.columns = [c.lower() for c in d.columns]  # older years use upper case
        return d[usecols] if usecols else d
    return read


def joined(values):
    vals = sorted({str(v) for v in values if pd.notna(v)})
    return ";".join(vals) if vals else None


def cmd_agencies(a):
    rows = []
    for path in a.zips:
        read = reader(path)
        ag = read("agencies")
        n = read("NIBRS_incident", usecols=["agency_id"])["agency_id"].value_counts()
        ag["incidents"] = ag["agency_id"].map(n).fillna(0).astype(int)
        rows.append(ag[["ori", "pub_agency_name", "agency_type_name", "population", "data_year", "nibrs_start_date", "incidents"]])
    d = pd.concat(rows)
    g = d.groupby("ori").agg(name=("pub_agency_name", "last"), type=("agency_type_name", "last"), population=("population", "max"),
                            years=("data_year", lambda s: f"{s.min()}-{s.max()}"), nibrs_start=("nibrs_start_date", "last"),
                            incidents=("incidents", "sum")).sort_values("incidents", ascending=False)
    print(g.to_string())


def flatten_one(path, oris):
    read = reader(path)
    look = lambda t, k, v: read(t).set_index(k)[v].to_dict()
    ag = read("agencies")
    agency, ori = ag.set_index("agency_id")["pub_agency_name"].to_dict(), ag.set_index("agency_id")["ori"].to_dict()
    off_name = look("NIBRS_OFFENSE_TYPE", "offense_code", "offense_name")
    loc_name = look("NIBRS_LOCATION_TYPE", "location_id", "location_name")
    vtype = look("NIBRS_VICTIM_TYPE", "victim_type_id", "victim_type_name")
    race = look("REF_RACE", "race_id", "race_code")
    eth = look("NIBRS_ETHNICITY", "ethnicity_id", "ethnicity_code")
    age_code = look("NIBRS_AGE", "age_id", "age_code")
    rel_code = look("NIBRS_RELATIONSHIP", "relationship_id", "relationship_code")
    weapon_name = look("NIBRS_WEAPON_TYPE", "weapon_id", "weapon_name")
    injury_name = look("NIBRS_INJURY", "injury_id", "injury_name")

    inc = read("NIBRS_incident", usecols=["incident_id", "agency_id", "incident_date", "report_date_flag", "incident_hour"])
    if oris:
        inc = inc[inc["agency_id"].map(ori).isin(oris)]
    off = read("NIBRS_OFFENSE", usecols=["offense_id", "incident_id", "offense_code", "attempt_complete_flag", "location_id"])
    vo = read("NIBRS_VICTIM_OFFENSE", usecols=["victim_id", "offense_id"])
    vic = read("NIBRS_VICTIM", dtype={"age_num": str, "AGE_NUM": str})
    rel = read("NIBRS_VICTIM_OFFENDER_REL", usecols=["victim_id", "relationship_id"])
    wea = read("NIBRS_WEAPON", usecols=["offense_id", "weapon_id"])
    inj = read("NIBRS_VICTIM_INJURY", usecols=["victim_id", "injury_id"])

    rel["code"] = rel["relationship_id"].map(rel_code)
    rel_by_victim = rel.groupby("victim_id")["code"].agg(joined)
    wea["name"] = wea["weapon_id"].map(weapon_name)
    weapon_by_offense = wea.groupby("offense_id")["name"].agg(joined)
    inj["name"] = inj["injury_id"].map(injury_name)
    injury_by_victim = inj.groupby("victim_id")["name"].agg(joined)

    d = (vo.merge(off, on="offense_id", how="left")
           .merge(vic, on=["victim_id"], how="left", suffixes=("", "_v"))
           .merge(inc, on="incident_id", how="inner" if oris else "left"))
    year = int(vic["data_year"].iloc[0]) if "data_year" in vic and len(vic) else None
    out = pd.DataFrame({
        "file_year": year,
        "agency": d["agency_id"].map(agency),
        "ori": d["agency_id"].map(ori),
        "incident_id": d["incident_id"],
        "incident_date": d["incident_date"],
        "report_date_flag": d["report_date_flag"],
        "incident_hour": d["incident_hour"],
        "offense_id": d["offense_id"],
        "offense_code": d["offense_code"],
        "offense_name": d["offense_code"].map(off_name),
        "attempt_complete": d["attempt_complete_flag"],
        "location": d["location_id"].map(loc_name),
        "victim_id": d["victim_id"],
        "victim_seq_num": d["victim_seq_num"],
        "victim_type": d["victim_type_id"].map(vtype),
        "age_code": d["age_id"].map(age_code),
        "age_num": d["age_num"],
        "sex": d["sex_code"],
        "race": d["race_id"].map(race),
        "ethnicity": d["ethnicity_id"].map(eth),
        "resident_status": d["resident_status_code"],
        "relationship": d["victim_id"].map(rel_by_victim),
        "weapon": d["offense_id"].map(weapon_by_offense),
        "injury": d["victim_id"].map(injury_by_victim),
    })
    print(f"{pathlib.Path(path).name}: {len(inc):,} incidents, {len(vic):,} victims in the file, {len(out):,} victim-offense rows")
    return out


def cmd_flatten(a):
    df = pd.concat([flatten_one(p, set(a.ori or [])) for p in sorted(a.zips)], ignore_index=True)
    dest = pathlib.Path(a.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    print("wrote", dest, f"({len(df):,} rows)")


def cmd_victims(a):
    kinds = {}
    for k in a.kind:
        name, _, codes = k.partition("=")
        kinds[name] = [c.strip() for c in codes.split(",")]
    d = pd.read_csv(a.csv, low_memory=False, dtype={"age_num": str, "age_code": str})
    codes = {c: name for name, cs in kinds.items() for c in cs}
    keep = d["offense_code"].isin(codes) & (d["victim_type"] == "Individual")
    if a.ori:
        keep &= d["ori"].isin(a.ori)
    left = d[d["offense_code"].isin(codes) & ~keep]
    d = d[keep].copy()
    age = pd.to_numeric(d["age_num"], errors="coerce")
    age[d["age_code"].isin(INFANT)] = 0
    age[d["age_code"].isin(UNKNOWN_AGE)] = np.nan
    rel = d["relationship"].fillna("").str.split(";")
    race_group = np.where(d["ethnicity"] == "H", "H", d["race"]) if a.race_rule == "hispanic-first" else d["race"]
    out = pd.DataFrame({
        "victim_id": d["victim_id"], "incident_id": d["incident_id"], "incident_date": d["incident_date"], "agency": d["agency"], "ori": d["ori"],
        "offense_code": d["offense_code"], "kind": d["offense_code"].map(codes), "race_group": race_group, "race": d["race"], "ethnicity": d["ethnicity"],
        "sex": d["sex"], "age": age.astype("Int64"), "resident_status": d["resident_status"],
        "partner": np.where(rel.apply(lambda xs: bool(PARTNER & set(xs))), "Y", "N"), "relationship": d["relationship"],
        "location": d["location"], "premise": d["location"].map(lambda v: PREMISE.get(v, v)), "weapon": d["weapon"], "injury": d["injury"],
        "attempt_complete": d["attempt_complete"],
    })
    order = {name: i for i, name in enumerate(kinds)}
    out["_o"] = out["kind"].map(order)
    before = len(out)
    out = out.sort_values(["_o", "incident_date", "victim_id"]).drop_duplicates("victim_id").drop(columns="_o")
    if before != len(out):
        print(f"{before - len(out):,} rows dropped: a victim listed under two kinds (or twice in one) is kept once, in the first kind given")
    other = left["ori"].isin(a.ori) if a.ori else pd.Series(True, index=left.index)
    if a.ori:
        print(f"left out, other agencies: {int((~other).sum()):,} rows ({', '.join(f'{k} {v:,}' for k, v in left.loc[~other, 'agency'].value_counts().items())})")
    print("left out, not individuals:", ", ".join(f"{k} {v:,}" for k, v in left.loc[other, "victim_type"].value_counts().items()) or "none")
    dest = pathlib.Path(a.out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for name in kinds:
        part = out[out["kind"] == name].drop(columns="kind")
        path = dest / f"{a.prefix}{name}.csv"
        part.to_csv(path, index=False)
        print(f"wrote {path}: {len(part):,} victims")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("agencies"); s.add_argument("zips", nargs="+")
    s = sub.add_parser("flatten"); s.add_argument("zips", nargs="+"); s.add_argument("--ori", action="append"); s.add_argument("--out", required=True)
    s = sub.add_parser("victims"); s.add_argument("csv"); s.add_argument("--kind", action="append", required=True); s.add_argument("--ori", action="append")
    s.add_argument("--race-rule", choices=["hispanic-first", "race"], required=True); s.add_argument("--out-dir", required=True); s.add_argument("--prefix", default="")
    a = ap.parse_args()
    {"agencies": cmd_agencies, "flatten": cmd_flatten, "victims": cmd_victims}[a.cmd](a)


if __name__ == "__main__":
    main()
