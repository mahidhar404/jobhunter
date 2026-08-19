/* Posted-date sorting shared by the ops UI (app.js). Classic UI is frozen (UI-033).
   Loaded as a plain script in the browser (globals) and required in node tests.

   This file is also the single source of truth for what a job's *displayed*
   posted date is. Sorting on one value while the row renders another is what
   made the Posted sort look broken when sort and label used different fields.
   Any posted-date label must come from jobPostedDisplay() so the two can't
   drift apart again. When the listing has no date_posted (or fallback), the
   discovery timestamp (created_at) is treated as the posted date. */
(function (root) {
  /** Resolved posted date: { time, iso, approx }; time is null when unknown.
   *  date_posted is the exact date from the source; date_posted_fallback is
   *  derived from a relative "Posted N Days Ago" string and renders with "~". */
  function jobPostedDisplay(job) {
    const exact = job && job.date_posted;
    if (exact != null && exact !== "") {
      const t = Date.parse(exact);
      if (!Number.isNaN(t)) return { time: t, iso: exact, approx: false };
    }
    const fb = job && job.date_posted_fallback;
    if (fb != null && fb !== "") {
      const t = Date.parse(fb);
      if (!Number.isNaN(t)) return { time: t, iso: fb, approx: true };
    }
    const discovered = job && job.created_at;
    if (discovered != null && discovered !== "") {
      const t = Date.parse(discovered);
      if (!Number.isNaN(t)) return { time: t, iso: discovered, approx: false };
    }
    return { time: null, iso: null, approx: false };
  }

  /** Epoch ms for a usable posted date, or null when absent/unparseable. */
  function datePostedTime(job) {
    return jobPostedDisplay(job).time;
  }

  /** Sort key for legacy callers: missing dates rank below every real date. */
  function datePostedSortKey(job) {
    const t = datePostedTime(job);
    return t == null ? -Infinity : t;
  }

  /** Fallback recency used only to break ties between equal posted dates. */
  function jobRecencyTime(job) {
    if (!job) return 0;
    const t = Date.parse(job.updated_at || job.created_at || "");
    return Number.isNaN(t) ? 0 : t;
  }

  /** Newest posted first; unknown posted dates always last; stable tie-breaks. */
  function compareByPosted(a, b) {
    const at = datePostedTime(a);
    const bt = datePostedTime(b);
    if (at == null || bt == null) {
      if (at !== bt) return at == null ? 1 : -1;
    } else if (at !== bt) {
      return bt - at;
    }
    const ar = jobRecencyTime(a);
    const br = jobRecencyTime(b);
    if (ar !== br) return br - ar;
    const title = String((a && a.title) || "").localeCompare(String((b && b.title) || ""));
    if (title !== 0) return title;
    return String((a && a.id) || "").localeCompare(String((b && b.id) || ""));
  }

  /** Whole days since the posted date, or null when it's unknown. */
  function postedAgeDays(job, now) {
    const { time } = jobPostedDisplay(job);
    if (time == null) return null;
    const ref = now == null ? Date.now() : now;
    return Math.max(0, Math.floor((ref - time) / 86400000));
  }

  /** Age column text: "3d", "~3d" for a derived date, "—" when unknown. */
  function postedAgeLabel(job, now) {
    const days = postedAgeDays(job, now);
    if (days == null) return "—";
    return (jobPostedDisplay(job).approx ? "~" : "") + days + "d";
  }

  const api = {
    jobPostedDisplay,
    datePostedTime,
    datePostedSortKey,
    jobRecencyTime,
    compareByPosted,
    postedAgeDays,
    postedAgeLabel,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== "undefined" ? globalThis : this);
