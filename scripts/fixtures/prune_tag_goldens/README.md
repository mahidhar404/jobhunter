# Prune / tag golden fixtures (dummy JDs — no applicant PII).
#
# Each JSON file:
#   title, company, location, description
#   expect: {
#     "keep": true|false,
#     "reason": null|"clearance"|...   # auto_delete_reason when keep=false
#     "tags": { "clearance": bool, "us_person": bool, ... optional }
#     "work_mode": "remote"|"hybrid"|"onsite"|"unknown"  # optional
#     "min_yoe": int|null                                 # optional
#   }
#
# Policy: under-prune when unsure. "unable to sponsor" / "no visa sponsorship"
# must KEEP (not USC/GC prune). Clearance / USC-only / excessive YOE prune.
