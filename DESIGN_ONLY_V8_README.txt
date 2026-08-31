DESIGN ONLY V8 — 2026-08-30

This patch changes presentation only.
- app.js: NOT included / NOT modified
- Python/API mutation/DB logic: NOT included / NOT modified
- Existing save/edit/register/delete/search handlers remain untouched

V8 fixes:
1) Top dashboard matches the approved reference more closely and no longer squeezes/clips its tables.
2) Member stats: full 2x2 detail table visible.
3) Payment summary: prepaid + pending cells both visible.
4) Deadline: title/target/date/D-day rows readable.
5) Recent work: YYYY.MM.DD format, readable rows.
6) Candidate form: vehicle type + fuel type made shorter.
7) Removed the redundant visible helper text '없으면 미가입'. Empty membership date still means non-member; data behavior is unchanged.
8) Candidate list: resident registration number shown; affiliated company hidden.
9) Existing edit/register/delete buttons remain visible.
