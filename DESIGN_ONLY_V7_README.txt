DESIGN ONLY V7 (2026-08-30)

Purpose
- Restore the approved dashboard proportions and readable typography.
- Fix the over-compressed V5/V6 dashboard appearance.

Changed
- app/static/index.html: only points to V7 CSS/JS assets.
- app/static/design-dashboard-v7-20260830.css: presentation rules only.
- app/static/design-dashboard-v7-20260830.js: same read-only dashboard decorator as V6; only adds a deadline column-label row for readability.

NOT CHANGED
- app/static/app.js
- Python server/router/model files
- POST/PUT/DELETE handlers
- database schema/data
- save/edit/register/delete/search business logic

Dashboard target
- Member statistics: full 2x2 detail grid visible.
- Payment summary: prepaid + pending both visible.
- Deadline management: summary + 업무/기한/남은일 + real task titles.
- Recent work: YYYY.MM.DD format.
- No micro-font/164px card squeeze at normal desktop viewport.
