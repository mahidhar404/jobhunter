#!/usr/bin/env python3
"""A lane switch chooses what to SCRAPE, never what to KEEP.

Regression: the auto-delete sweep evaluated every job against the lanes
currently switched on. Turning India off therefore deleted all 742 India jobs
already discovered, and block_deleted_job tombstoned their URLs so
re-discovery would skip them forever — the user toggled a filter and their
jobs disappeared permanently. Retention now evaluates against every valid
lane, so a toggle only hides rows.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_filters import auto_delete_reason  # noqa: E402

INDIA_JOB = dict(
    title="Machine Learning Engineer",
    company="Acme India",
    location="Bengaluru, India",
    description="Python, PyTorch",
    url="https://example.com/job/1",
)
WW_JOB = dict(
    title="Data Engineer",
    company="Acme GmbH",
    location="Berlin",
    description="Python, dbt",
    url="https://example.com/job/2",
)


class LaneToggleRetentionTests(unittest.TestCase):
    def test_india_job_is_disqualified_only_by_a_narrowed_lane_set(self):
        """Shows the mechanism the sweep used to run into."""
        self.assertIsNone(auto_delete_reason(**INDIA_JOB,
                                             regions=("india", "worldwide")))
        self.assertEqual(
            auto_delete_reason(**INDIA_JOB, regions=("worldwide",)),
            "non_us_location",
            "narrowing lanes marks in-scope India jobs for deletion")

    def test_sweep_uses_all_valid_lanes_not_the_enabled_ones(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        self.assertIn('VALID_DISCOVERY_LANES = ("india", "worldwide")', src)
        # The sweep must not scope retention to the toggled-on lanes.
        sweep = src[src.index("def _auto_delete_sweep") if "def _auto_delete_sweep" in src
                    else src.index("auto-delete sweep") - 6000:]
        sweep = sweep[:sweep.index("auto-delete sweep")] if "auto-delete sweep" in sweep else sweep
        self.assertNotIn("regions = enabled_discovery_regions()", sweep,
                         "retention must not depend on the lane switches")

    def test_worldwide_job_survives_either_lane_set(self):
        self.assertIsNone(auto_delete_reason(**WW_JOB, regions=("worldwide",)))
        self.assertIsNone(auto_delete_reason(**WW_JOB,
                                             regions=("india", "worldwide")))

    def test_restore_script_keeps_genuinely_bad_jobs_deleted(self):
        """Restoring must re-check, not blanket-undelete."""
        src = (ROOT / "scripts" / "restore_lane_swept_jobs.py").read_text()
        self.assertIn("auto_delete_reason(", src)
        self.assertIn("regions=VALID_LANES", src)
        self.assertIn("kept += 1", src)
        # A management-track India job must stay deleted after restore.
        self.assertEqual(
            auto_delete_reason(
                title="Engineering Manager", company="Acme India",
                location="Bengaluru, India", description="",
                url="https://example.com/job/3",
                regions=("india", "worldwide")),
            "management_track")

    def test_restore_lifts_url_tombstones(self):
        src = (ROOT / "scripts" / "restore_lane_swept_jobs.py").read_text()
        self.assertIn("unblock_job", src,
                      "a restored job whose URL stays blocked is still invisible "
                      "to re-discovery")


if __name__ == "__main__":
    ok = unittest.main(exit=False).result.wasSuccessful()
    sys.exit(0 if ok else 1)


class LaneSettingsReachTheUiTests(unittest.TestCase):
    """/api/status must carry the lane switches, or the UI overwrites them.

    app.js does `india = disc.discover_india !== false`, so an absent key reads
    as ON. runtime_status() built its discovery blob from
    _discovery_status_in_memory(), which has no settings — the UI therefore
    showed both lanes enabled whatever was saved, and wrote that back on the
    next save, silently undoing a worldwide-only choice on every restart.
    """

    def test_runtime_status_includes_the_lane_switches(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        block = src[src.index("def runtime_status"):]
        block = block[:block.index("def _parse_jobs_payload")]
        self.assertIn('disc["discover_india"]', block)
        self.assertIn('disc["discover_worldwide"]', block)
        self.assertIn("load_discovery_settings()", block)

    def test_ui_treats_a_missing_key_as_enabled(self):
        """Documents why the key must be present, not merely correct."""
        app_js = (ROOT / "dashboard" / "static" / "app.js").read_text()
        self.assertIn("disc.discover_india !== false", app_js)

    def test_start_script_does_not_clobber_a_saved_lane(self):
        sh = (ROOT / "start_dashboard.sh").read_text()
        self.assertIn('cur.setdefault("discover_india", True)', sh)
        self.assertNotIn('cur["discover_india"] = True', sh)


class ChildStepLaneScopeTests(unittest.TestCase):
    """The lane switches must not reach the child steps as a *retention* scope.

    server.py exports JOBHUNTER_DISCOVERY_REGIONS before spawning dedup /
    write. It used to export the *enabled* lanes, and write_discovered_jobs
    calls auto_delete_reason() with no explicit regions — so it read that env
    var. A worldwide-only run therefore wrote every India role a worldwide
    board surfaced (Wellfound alone: 56 in one run) and pruned it in the same
    pass as `non_us_location`. The user saw "813 scraped, nothing added".
    """

    def test_children_inherit_every_valid_lane_not_the_enabled_ones(self):
        src = (ROOT / "dashboard" / "server.py").read_text()
        line = [ln for ln in src.splitlines()
                if 'os.environ["JOBHUNTER_DISCOVERY_REGIONS"]' in ln]
        self.assertEqual(len(line), 1, "expected exactly one export site")
        self.assertIn("VALID_DISCOVERY_LANES", line[0],
                      "child steps must keep every lane, not the enabled ones")
        self.assertNotIn("join(regions)", line[0])

    def test_write_step_has_no_explicit_region_scope(self):
        """It relies on the env var, which is why the export must be right."""
        src = (ROOT / "scripts" / "write_discovered_jobs.py").read_text()
        block = src[src.index("prune_reason = auto_delete_reason("):]
        block = block[:block.index(")")]
        self.assertNotIn("regions=", block)

    def test_an_india_role_from_a_worldwide_board_survives(self):
        """Wellfound is a worldwide board that lists Bengaluru roles."""
        self.assertIsNone(auto_delete_reason(
            title="Backend Engineer", company="Acme",
            location="Bengaluru", description="Python",
            url="https://wellfound.com/jobs/1",
            regions=("india", "worldwide")))
        # …and this is what the narrowed scope used to do to it.
        self.assertEqual(auto_delete_reason(
            title="Backend Engineer", company="Acme",
            location="Bengaluru", description="Python",
            url="https://wellfound.com/jobs/1",
            regions=("worldwide",)), "non_us_location")
