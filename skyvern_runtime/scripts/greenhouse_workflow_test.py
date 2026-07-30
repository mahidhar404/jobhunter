"""
Pilot: persisted, parameterized Skyvern Workflow for Greenhouse applications,
instead of the ad-hoc run_task() calls real_job_test.py uses.

WHY THIS EXISTS: a capability survey of Skyvern's source found that action/script
caching (real Playwright code generated from a successful run, replayed on later
runs with per-element AI-locator fallback) is keyed to workflow_permanent_id - and
every ad-hoc run_task()/task_v2 call mints a BRAND NEW workflow_permanent_id every
single invocation (task_v2_service.py: create_empty_workflow(...) per call). So
caching can never accumulate no matter what flags are passed to run_task(). The
only way to actually get the caching benefit is a real, persisted Workflow, called
repeatedly via run_workflow() against the SAME workflow_permanent_id. Greenhouse is
the highest-volume ATS in the discovery backlog (438 of 2756 discovered postings),
so it's the platform with the most to gain from this.

This script creates ONE Greenhouse workflow (once - the id is cached in
greenhouse_workflow_state.json so re-running this script never mints a second one,
which would defeat the whole point) and runs it against different Greenhouse
postings. First run per underlying block signature pays full agent cost and
generates the cached script automatically (Skyvern's own default behavior on any
successful NAVIGATION block). Pass run_with="code" on a later run to attempt cached
replay instead of a fresh agent reasoning loop.

Reuses real_job_test.py's PROFILE_BLOCK/NEVER_SUBMIT prompt text and DB-based safety
watchdogs unchanged (don't fork already-tuned, safety-critical text) - only the
run/poll plumbing differs, since a workflow run has a `wr_...` run_id but the
underlying NavigationBlock execution still creates a normal task_id/actions rows
(resolved via workflow_run_blocks), so the exact same safety checks apply unmodified.
"""
import os
import sys
import json
import time
import asyncio
import httpx
import psycopg
from pathlib import Path
from skyvern import Skyvern

sys.path.insert(0, str(Path(__file__).parent))
import real_job_test as base  # noqa: E402  (reuse profile text + watchdog SQL, don't duplicate)
import cost_helper  # noqa: E402

STATE_PATH = Path(__file__).parent.parent / "greenhouse_workflow_state.json"

COMPLETE_CRITERION = (
    "Every field this profile has data for is filled in with the correct value, and the page "
    "is now on its final review/submit step (or there is nothing left that can be filled) - as "
    "long as the Submit/Apply/Send/Confirm/Create-Account button itself has NOT been clicked."
)
TERMINATE_CRITERION = (
    "A required field has no corresponding profile data and cannot be resolved with the "
    "placeholder/substitution rules given, or the page shows a hard error or dead end (e.g. a "
    "bot-detection wall) that blocks any further progress."
)

NAVIGATION_GOAL = (
    f"Fill out this real Greenhouse job application form.\n{base.PROFILE_BLOCK}\n{base.NEVER_SUBMIT}"
)


def _workflow_definition() -> dict:
    return {
        "title": "Greenhouse Job Application (persisted, cacheable)",
        "workflow_definition": {
            "parameters": [
                {"parameter_type": "workflow", "key": "job_url", "workflow_parameter_type": "string"},
            ],
            "blocks": [
                {
                    "block_type": "navigation",
                    "label": "fill_application",
                    "url": "{{job_url}}",
                    "navigation_goal": NAVIGATION_GOAL,
                    "parameter_keys": ["job_url"],
                    "complete_criterion": COMPLETE_CRITERION,
                    "terminate_criterion": TERMINATE_CRITERION,
                    "include_action_history_in_verification": True,
                    "max_steps_per_run": 50,
                }
            ],
        },
    }


async def get_or_create_workflow(skyvern: Skyvern) -> str:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        print(f"[setup] reusing existing workflow {state['workflow_permanent_id']} "
              f"(created {state['created_at']})", flush=True)
        return state["workflow_permanent_id"]
    workflow = await skyvern.create_workflow(json_definition=_workflow_definition())
    state = {
        "workflow_id": workflow.workflow_id,
        "workflow_permanent_id": workflow.workflow_permanent_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }
    STATE_PATH.write_text(json.dumps(state, indent=2))
    print(f"[setup] created new workflow {workflow.workflow_permanent_id}", flush=True)
    return workflow.workflow_permanent_id


def _resolve_task_id(workflow_run_id: str) -> str | None:
    """A workflow run's single NavigationBlock still executes as a normal task
    under the hood - this is the row that actually links workflow_run_id -> task_id,
    letting the existing task_id-keyed watchdog queries work completely unmodified."""
    with psycopg.connect(**base.DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT task_id FROM workflow_run_blocks WHERE workflow_run_id = %s "
                "AND task_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                (workflow_run_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def _workflow_run_status(workflow_run_id: str) -> str | None:
    with psycopg.connect(**base.DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM workflow_runs WHERE workflow_run_id = %s", (workflow_run_id,))
            row = cur.fetchone()
            return row[0] if row else None


def _workflow_run_meta(workflow_run_id: str) -> dict:
    with psycopg.connect(**base.DB_CONN_KWARGS) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT run_with, code_gen, failure_reason FROM workflow_runs WHERE workflow_run_id = %s",
                (workflow_run_id,),
            )
            row = cur.fetchone()
            return {"run_with": row[0], "code_gen": row[1], "failure_reason": row[2]} if row else {}


async def _cancel_workflow_and_task(skyvern: Skyvern, job_id: str, workflow_run_id: str, task_id: str | None) -> None:
    """SAFETY-CRITICAL: cancelling a workflow run (skyvern.cancel_run on a `wr_...`
    id) does NOT cancel the underlying block's task - confirmed live: a cancelled
    raft run's workflow_runs.status flipped to 'canceled' immediately, but its
    task_id kept producing new actions/tokens for at least another 30+ seconds
    (browser still genuinely executing server-side). For the stuck-loop/cost-waste
    watchdog this is merely expensive; for the submit-click and Enter-keypress
    safety checks it is a real risk - a "cancelled" run could still go on to click
    Submit for real. The task-level cancel isn't exposed on the new Runs API for a
    block-owned task (skyvern.cancel_run(task_id) 404s: "Run not found"), so this
    hits the legacy /api/v1/tasks/{task_id}/cancel endpoint directly, same one
    real_job_test.py's ad-hoc tasks are cancelled through under the hood."""
    try:
        await skyvern.cancel_run(workflow_run_id)
    except Exception as exc:
        print(f"[{job_id}] failed to cancel workflow run {workflow_run_id}: {exc}", flush=True)
    if not task_id:
        task_id = _resolve_task_id(workflow_run_id)
    if task_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{base.BASE_URL}/api/v1/tasks/{task_id}/cancel",
                    headers={"x-api-key": base.API_KEY},
                )
        except Exception as exc:
            print(f"[{job_id}] failed to cancel underlying task {task_id}: {exc}", flush=True)


async def run_one(skyvern: Skyvern, workflow_permanent_id: str, job_id: str, job_url: str,
                   run_with: str | None = None, timeout: float = 1800) -> dict:
    # 900s (real_job_test.py's default) proved too tight for the last two attempts
    # here (raft, rackner) - not a stuck loop (watchdog=False both times, token
    # counts normal-to-low) but genuine DeepSeek API latency degradation confirmed
    # live in supervised_server.log: individual extract-actions calls that are
    # normally 5-10s took 60-154s, with completely normal (small) input token
    # counts and ~99% cache-hit rates - ruling out a prompt-bloat cause. This is
    # external/transient, not something this script or the Workflow API caused.
    # Raised timeout to give the same external conditions more room, rather than
    # treating a slow LLM provider as a caching-design flaw.
    print(f"[{job_id}] START {time.strftime('%Y-%m-%d %H:%M:%S %Z')} run_with={run_with!r}", flush=True)
    t0 = time.time()
    watchdog_triggered = None
    submit_alarm = None
    enter_alarm = None
    err = None
    result = None
    task_id = None
    run_id = None
    try:
        started = await skyvern.run_workflow(
            workflow_id=workflow_permanent_id,
            parameters={"job_url": job_url},
            run_with=run_with,
            wait_for_completion=False,
            timeout=timeout,
        )
        run_id = started.run_id
        terminal_statuses = {"completed", "failed", "terminated", "canceled", "timed_out"}
        cancel_requested_at = None
        while True:
            await asyncio.sleep(base.WATCHDOG_POLL_S)
            if time.time() - t0 > timeout:
                err = "client-side timeout waiting for run to finish"
                await _cancel_workflow_and_task(skyvern, job_id, run_id, task_id)
                break
            status = _workflow_run_status(run_id)
            if status in terminal_statuses:
                if cancel_requested_at is not None and status == "completed":
                    watchdog_triggered = None
                break

            if task_id is None:
                task_id = _resolve_task_id(run_id)
            if task_id is None:
                continue  # block's task row not created yet - nothing to check this cycle

            submit_hit = base._check_no_submit_clicked(task_id)
            if submit_hit and cancel_requested_at is None:
                print(
                    f"\n{'!' * 70}\n[{job_id}] CRITICAL: SUBMIT-TYPE CLICK DETECTED on run {run_id}\n"
                    f"  action_id={submit_hit['action_id']} button_type={submit_hit['button_type']!r} "
                    f"visible_text={submit_hit['visible_text']!r}\n  Cancelling immediately.\n{'!' * 70}\n",
                    flush=True,
                )
                await _cancel_workflow_and_task(skyvern, job_id, run_id, task_id)
                cancel_requested_at = time.time() - t0
                submit_alarm = submit_hit
                continue

            enter_hit = base._check_enter_keypress(task_id)
            if enter_hit and cancel_requested_at is None:
                print(f"\n{'!' * 70}\n[{job_id}] CRITICAL: ENTER KEYPRESS DETECTED on run {run_id}\n{'!' * 70}\n",
                      flush=True)
                await _cancel_workflow_and_task(skyvern, job_id, run_id, task_id)
                cancel_requested_at = time.time() - t0
                enter_alarm = enter_hit
                continue

            loop_sig = base._detect_stuck_loop(task_id)
            if loop_sig and cancel_requested_at is None:
                print(f"[{job_id}] WATCHDOG: repeat pattern {loop_sig} - cancelling run {run_id}", flush=True)
                await _cancel_workflow_and_task(skyvern, job_id, run_id, task_id)
                cancel_requested_at = time.time() - t0
                watchdog_triggered = {"reason": loop_sig, "detected_at": cancel_requested_at}

        result = await skyvern.get_run(run_id)
        if task_id is None:
            task_id = _resolve_task_id(run_id)
        if task_id:
            final_submit_hit = base._check_no_submit_clicked(task_id)
            if final_submit_hit and submit_alarm is None:
                submit_alarm = final_submit_hit
            final_enter_hit = base._check_enter_keypress(task_id)
            if final_enter_hit and enter_alarm is None:
                enter_alarm = final_enter_hit
    except Exception as e:
        err = err or str(e)

    elapsed = time.time() - t0
    meta = _workflow_run_meta(run_id) if run_id else {}
    cost = None
    if task_id:
        try:
            cost = cost_helper.fetch_and_compute(task_id).summary()
        except Exception as cost_exc:
            cost = {"error": str(cost_exc)}

    print(
        f"[{job_id}] END elapsed={elapsed:.1f}s error={err} watchdog={bool(watchdog_triggered)} "
        f"SUBMIT_ALARM={bool(submit_alarm)} ENTER_ALARM={bool(enter_alarm)} "
        f"run_with_actual={meta.get('run_with')!r} code_gen={meta.get('code_gen')} "
        f"tokens={cost.get('total_tokens') if cost else None}",
        flush=True,
    )

    out = {
        "id": job_id,
        "url": job_url,
        "run_with_requested": run_with,
        "elapsed_seconds": elapsed,
        "error": err,
        "watchdog_triggered": watchdog_triggered,
        "submit_alarm": submit_alarm,
        "enter_alarm": enter_alarm,
        "task_id_resolved": task_id,
        "workflow_run_meta": meta,
        "cost": cost,
        "result": json.loads(result.model_dump_json()) if result else None,
    }
    out_dir = Path(__file__).parent.parent / "real_job_results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"workflow-{job_id}.json").write_text(json.dumps(out, indent=2))
    print(f"[{job_id}] saved -> real_job_results/workflow-{job_id}.json", flush=True)
    return out


async def main():
    skyvern = Skyvern(base_url=base.BASE_URL, api_key=base.API_KEY)
    workflow_permanent_id = await get_or_create_workflow(skyvern)

    # IMPORTANT: run_with="code" must be passed on EVERY run, including the very
    # first one on a fresh cache key - not just once caching is "established".
    # Root-caused live: this workflow's own code_version defaults to 2 at creation
    # (confirmed via `workflows.code_version` - not something this script sets).
    # Skyvern's cache-key resolution (is_adaptive_caching_from_effective_state,
    # forge/sdk/workflow/models/workflow.py:280) branches on the REQUEST's own
    # run_with, not just the workflow's: passing run_with=None inherits the
    # workflow-level default ("agent") -> non-adaptive cache key (no ":v2" suffix).
    # Passing run_with="code" explicitly -> code_version>=2 -> adaptive cache key
    # (":v2" suffix appended). These are two DIFFERENT cache buckets that never
    # see each other's scripts. An earlier run in this same investigation (grvty,
    # run_with=None) published a script under the non-adaptive key; a later
    # run_with="code" run (raft) looked in the adaptive key, got a real cache miss,
    # and fell back to full agent mode (442K tokens, 900s timeout) - not a caching
    # failure, a test-sequencing mistake. Corrected here: every job below uses
    # run_with="code" so cache generation and lookup always target the same key.
    jobs = [
        # Leg A: fresh company, fresh cache key under the corrected scheme -
        # expected cache miss, generates the (correctly-keyed) script on success.
        ("rackner-ai-ml-engineer", "https://job-boards.greenhouse.io/rackner/jobs/4676295005", "code"),
        # Leg B: different company, same subdomain/platform - the real cache-hit
        # generalization test now that both legs use a consistent run_with.
        ("translucent-senior-data-engineer",
         "https://job-boards.greenhouse.io/translucent/jobs/4246634009", "code"),
        # --- Earlier, imperfectly-sequenced legs kept for the historical record ---
        # grvty: run_with=None -> wrote to the non-adaptive cache key (see note above).
        ("grvty-ai-ml-engineer", "https://job-boards.greenhouse.io/grvty/jobs/4273921009", None),
        # robinhood: different Greenhouse subdomain (boards.greenhouse.io, not
        # job-boards.greenhouse.io) - cache_key_value is scoped per-domain, so this
        # was a genuine cache miss by domain mismatch, not a cross-company test.
        ("robinhood-machine-learning-engineer",
         "https://boards.greenhouse.io/robinhood/jobs/7960680?t=gh_src=&gh_jid=7960680", "code"),
        # raft: same subdomain as grvty but run_with="code" against grvty's
        # non-adaptive-keyed script - the run_with mismatch described above.
        ("raft-ai-ml-engineer", "https://job-boards.greenhouse.io/raft/jobs/6011015004", "code"),
    ]
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for job_id, job_url, run_with in jobs:
        if only and job_id != only:
            continue
        await run_one(skyvern, workflow_permanent_id, job_id, job_url, run_with=run_with)


if __name__ == "__main__":
    asyncio.run(main())
