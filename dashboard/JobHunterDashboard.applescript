-- Source for Desktop "Job Hunter Dashboard.app".
-- Rebuild with custom icon: ./dashboard/rebuild_desktop_app.sh
-- Launches launch_dashboard.sh → server.py :8787 → dedicated-profile Chrome for
--   Testing --app=http://127.0.0.1:8787/ (dashboard_ui_profile/). Never hosted by
--   /Applications/Google Chrome.app: that would make it the running
--   com.google.Chrome instance and hijack Dock/Spotlight "Google Chrome".
-- Quit/close: UI → /api/shutdown → kills form-fill Chrome-for-Testing +
--   OpenClaw PartyRock CDP (:18800 / ~/.openclaw/browser/…) + legacy
--   partyrock_chrome_profile leftovers + dashboard UI → applet exits Dock.
-- Refresh: /api/restart keeps UI + OpenClaw PartyRock + CAPTCHA/Ready fill
--   hold (procs + CfT); JS reloads in place.
-- Long timeout: launch_dashboard.sh waits until explicit Quit / last window
--   close / Cmd+Q stops the server (idle heartbeat stall does not quit).
--
-- Dock click while already running: `on reopen` focuses the existing UI via
--   launch_dashboard.sh --focus-ui (System Events by PID). Never re-launches
--   Chrome against the same profile (avoids blank extra windows).
--
-- Exit handling: clean quit (shell status 0) and Dock Cmd+Q (shell killed by
-- signal) must be silent. Only real non-zero shell failures show a dialog —
-- otherwise macOS/Script Runner surfaces "exited with status N" on every exit.
--
-- CHR2-009: __JOB_HUNTER_ROOT__ is substituted by rebuild_desktop_app.sh
--   (script-relative ROOT). Do not hand-edit the absolute path here.

on run
	try
		with timeout of 86400 seconds
			do shell script "__JOB_HUNTER_ROOT__/dashboard/launch_dashboard.sh >> __JOB_HUNTER_ROOT__/logs/dashboard_launcher.out 2>&1"
		end timeout
	on error errMsg number errNum
		-- do shell script: exit status → errNum 1..255; signal kill → other codes
		-- (often 1000+) with message "terminated due to receipt of a signal".
		if errNum ≥ 1 and errNum ≤ 255 then
			display alert "Job Hunter Dashboard failed" message errMsg as critical buttons {"OK"} default button "OK"
		end if
		-- errNum 0 / signal / cancel: intentional quit — exit silently
	end try
end run

on reopen
	-- Fired when the Dock icon is clicked while this applet is already running
	-- (on run is blocked on the launcher). Focus/create the dashboard UI only.
	-- CHR2-010: surface --focus-ui failures (do not swallow silently).
	try
		do shell script "__JOB_HUNTER_ROOT__/dashboard/launch_dashboard.sh --focus-ui >> __JOB_HUNTER_ROOT__/logs/dashboard_launcher.out 2>&1"
	on error errMsg number errNum
		if errNum ≥ 1 and errNum ≤ 255 then
			display alert "Job Hunter Dashboard focus failed" message errMsg as critical buttons {"OK"} default button "OK"
		end if
	end try
end reopen
