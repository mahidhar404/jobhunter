# WORKING.md — reliability contracts (short)

Live day-to-day rules for OmniDex / job-hunter. Details in `PLAYBOOK.md` + `TOOLS.md`.

1. **Port:** `http://127.0.0.1:8787` only (no silent hop to `:8788`).
2. **`GET /api/jobs`:** stamp-only + cache + ETag. No `jd_full` disk walks, no JD re-parse under the list path.
3. **JD work:** `/api/jobs/search` (bare tokens; UI strips `jd:` client-side), job detail, background backfill only.
4. **Prune:** explicit disqualifiers only; under-prune when unsure (`unable to sponsor` ≠ USC/GC prune). Goldens: `scripts/fixtures/prune_tag_goldens/`.
5. **Fill:** never-submit, never CAPTCHA, dummy PII for automation.
6. **Verify:** see “Reliability contracts — how to verify” in `TOOLS.md`.
