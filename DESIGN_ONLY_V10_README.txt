DESIGN ONLY V10 — reference structure match

Changed:
- app/static/index.html: loads V10 decorator files only
- app/static/design-dashboard-v10-20260830.css: presentation only
- app/static/design-dashboard-v10-20260830.js: GET-only dashboard decorator + presentation classes

NOT INCLUDED / NOT MODIFIED:
- app.js
- Python backend
- API write logic
- DB/schema
- save/edit/register/delete/search/pagination handlers

Key V10 changes:
- Member card simplified to large total + 2 side stats (reference structure)
- Payment card simplified to one main amount + one comparison line
- Deadline card keeps 4 summary stats + up to 4 readable rows
- Recent work uses unified YYYY.MM.DD dates
- Form field layout classified by visible label text, not fragile input names
- vehicle type/fuel shortened; membership hint hidden; address full-width
- candidate list: resident number shown, affiliated company hidden, actions preserved
