FINAL V13 - GitHub overwrite package

Scope: visual layout/style only for the existing receivables dashboard.
Changed:
- app/static/receivables.css
- app/static/receivables.html (CSS cache version string only)

Not changed:
- JavaScript
- Python/router/API
- DB/schema
- calculations
- receivables data
- payment/closure/contact/member lookup behavior

Design target:
- 4 KPI cards across the top
- comparison result banner below
- compact performance/member-change metrics
- 2x2 large, balanced chart grid
- clean monthly performance table
- existing member lookup below
- pale yellow Excel download button retained
