# Lessons from past runs

Each of these happened on the Los Angeles assault analysis. Check for all of them every time.

1. **Related codes were left out.** LAPD coded intimate partner assault separately (626, 236). The first analysis used only 624 and 230 and missed 55,578 assaults, about half of all assaults on women. Read the full code list in the audit.
2. **An old download was stale.** The portal had been revised (late reports) and extended past the file's end. Pull fresh from the API.
3. **A records-system change thinned the data.** LAPD moved to NIBRS in March 2024; the old file emptied out over three months. Stop at the last full year before the break, and use the new system as a replication.
4. **Missing values were imputed.** An early notebook back-filled and forward-filled the raw file, copying fields between unrelated records. Never impute.
5. **A half year was weighted as a full one.** Weight partial years by their share of the year, or use full years.
6. **Report rates were turned into people.** "About 1 in 28 Black women" was wrong: a woman assaulted twice counts twice. Say "per 100,000", never "1 in X".
7. **Race coding was unbounded.** Officers record race by sight; the Census counts race alone. LA has 24% more people who are Black alone or in combination. State the worst case.
8. **Missing race was not random.** Unknown race clustered where Black residents are scarce, which understates other groups' rates and widens the gap. Check where it clusters.
9. **One comparison was unstable.** The Asian ratio moved 35% between records systems while the others moved under 6%. Do not headline unstable comparisons.
10. **Prose went stale.** After a definition change, "the only group above 50%" became false and a caption read the wrong row of a reordered list. Generate every number in prose from the outputs, and look rows up by label, not position.
11. **Pooling hid a spread.** "Other women" pooled Hispanic, White and Asian women; the adjusted gap was about 2x against Hispanic and White women and 10x against Asian women. Report pairwise.
12. **A covariate looked meaningful because of how it was built.** Homelessness entered as one person per tent. Test alternative definitions; here they changed nothing.
13. **A claim was nearly published without a source.** Verify every external figure by opening the source.
14. **Interpretation drifted toward causes.** "Consistent with domestic violence" was written before the data was checked, and the check contradicted it. Test a reading before writing it.
