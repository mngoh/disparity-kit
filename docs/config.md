# analysis.json

One file per project. Paths are relative to the folder holding it. Outputs go to `out/`.

| Key | Required | Meaning |
|---|---|---|
| `title` | yes | Page title. |
| `author` | no | Shown in the header and footer. |
| `place.name`, `place.short` | yes, no | Place name for prose; `short` (for example "LA") in the headline. |
| `place.census_geoid` | yes | Census Reporter place id, for example `16000US0644000`. Search: `https://api.censusreporter.org/1.0/geo/search?q=<name>&sumlevs=160`. |
| `acs_release`, `tiger` | no | Default `acs2024_5yr`, `tiger2024`. Pick the five-year release that best overlaps the incident window. |
| `window.start`, `window.end` | yes | ISO dates. Full calendar years where possible. Partial years are weighted. |
| `event.noun`, `.plural`, `.verb` | no | Wording, for example `assault`, `assaults`, `assaulted`. |
| `incidents` | yes | List of `{"path", "kind"}`. One file per kind, or one file with `kind` set to `all`. |
| `kind_labels` | no | Display names for kinds. |
| `columns` | yes | Map from standard names to the file's columns: `date` (required), `race`, `sex`, `age`, `lat`, `lon`, `district`, `premise`, `weapon`, `code`, `id`. Missing ones switch off the tests that need them. |
| `race_map` | yes | Raw race code to group name. Unmapped codes are excluded and reported. |
| `sex_values` | no | Raw sex code to `F` or `M`. Default `{"F": "F", "M": "M"}`. |
| `groups` | yes | Group names, each one of Black, Hispanic, White, Asian, AIAN, NHPI, Other, Multiracial (they pick the ACS tables). |
| `focus.group`, `focus.sex`, `focus.label` | yes | The group and sex at the center of the question, for example Black, F, "Black women". |
| `flags` | no | Subtypes: `{"partner": {"column": "code", "values": ["626", "236"], "label": "Intimate partner"}}`. `column` is a standard name from `columns` or any column in the incident files (for example a NIBRS relationship flag). |
| `districts` | no | `geojson_url` or `geojson_path`, `name_field`, optional `rename` (GeoJSON name to data name), `label` (for example "LAPD division"), `title_case` (default true). |
| `covariates` | no | List of `{"path", "label"}`: tract CSVs with `geoid` plus numeric columns. |
| `weapon_classes` | no | List of `{"label", "keywords"}` to override the default weapon classes. |
| `min_pop` | no | Smallest population to rate (default 2000). |
| `headline`, `question` | no | Set after seeing results. Generated if absent. |
| `extra_caveats` | no | List of `[heading, text]` pairs added to the caveats. |
| `sources` | no | Source names for the method line. |
| `links.code`, `links.home` | no | Header links. |
| `page_path` | no | Where to write the page (default `index.html`). |
