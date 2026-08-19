-- Source for Desktop "OmniDex" (bundle path defaults to Omnidex.app).
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
-- signal) must be silent. Recoverable failures (ops shell already up, duplicate
-- launch, signal quit) are silent. Real failures show log tail + Automation hint
-- when osascript/System Events is denied.
--
-- CHR2-009: __JOB_HUNTER_ROOT__ is substituted by rebuild_desktop_app.sh
--   (script-relative ROOT). Do not hand-edit the absolute path here.

property jhRoot : "__JOB_HUNTER_ROOT__"

on jhLauncherLog()
	return jhRoot & "/logs/dashboard_launcher.out"
end jhLauncherLog

on jhServerLog()
	return jhRoot & "/logs/dashboard_server.out"
end jhServerLog

on jhLogTail(maxLines)
	try
		set launcherLog to quoted form of my jhLauncherLog()
		set serverLog to quoted form of my jhServerLog()
		set tailN to maxLines * 2
		return do shell script "/usr/bin/tail -n " & maxLines & " " & launcherLog & " " & serverLog & " 2>/dev/null | /usr/bin/tail -n " & tailN
	on error
		return "(no log output yet)"
	end try
end jhLogTail

on jhDashboardServing()
	try
		do shell script "for p in 8787 8788 8789 8790 8791 8792; do curl -sf \"http://127.0.0.1:$p/\" 2>/dev/null | /usr/bin/grep -q 'class=\"ops-shell\"' && exit 0; done; exit 1"
		return true
	on error
		return false
	end try
end jhDashboardServing

on jhIsAutomationDenied(errMsg)
	set e to errMsg as text
	if e contains "not allowed assistive" then return true
	if e contains "Not authorized" then return true
	if e contains "assistive access" then return true
	if e contains "-1743" then return true
	if e contains "1002" and e contains "System Events" then return true
	return false
end jhIsAutomationDenied

on jhIsRecoverableFailure(errMsg, errNum)
	-- do shell script: exit status → errNum 1..255; signal kill → other codes.
	if errNum < 1 or errNum > 255 then return true

	set e to errMsg as text
	if e contains "exited with status 0" then return true
	if e contains "terminated due to receipt of a signal" then return true
	if e contains "already launched" then return true
	if e contains "already running" then return true
	if e contains "focusing UI" then return true
	if e contains "another launcher is live" then return true
	if e contains "recoverable:" then return true

	-- Post-reboot / race: launcher failed but ops dashboard is already serving.
	if my jhDashboardServing() then return true

	return false
end jhIsRecoverableFailure

on jhAutomationHint()
	return "macOS Automation is required to focus the dashboard window." & return & return & "System Settings → Privacy & Security → Automation:" & return & "  • Enable OmniDex → System Events" & return & "  • If OmniDex is missing, open Terminal and run:" & return & "    osascript -e 'tell application \"System Events\" to keystroke \"\"'" & return & "    then approve the prompt and retry." & return & return & "Click the Dock icon again after granting access."
end jhAutomationHint

on jhBuildFailureMessage(errMsg, isFocus)
	set body to errMsg as text
	set body to body & return & return & "Log tail (launcher + server):" & return & my jhLogTail(14)

	if my jhIsAutomationDenied(errMsg) then
		set body to body & return & return & my jhAutomationHint()
	end if

	return body
end jhBuildFailureMessage

on jhHandleShellError(errMsg, errNum, isFocus)
	if my jhIsRecoverableFailure(errMsg, errNum) then return

	set alertTitle to "OmniDex failed"
	if isFocus then set alertTitle to "OmniDex focus failed"

	display alert alertTitle message my jhBuildFailureMessage(errMsg, isFocus) as critical buttons {"OK"} default button "OK"
end jhHandleShellError

on jhRunLauncher(extraArgs)
	set logPath to quoted form of my jhLauncherLog()
	set cmd to jhRoot & "/dashboard/launch_dashboard.sh"
	if extraArgs is not "" then set cmd to cmd & " " & extraArgs
	set cmd to cmd & " >> " & logPath & " 2>&1"
	do shell script cmd
end jhRunLauncher

on run
	try
		with timeout of 86400 seconds
			my jhRunLauncher("")
		end timeout
	on error errMsg number errNum
		my jhHandleShellError(errMsg, errNum, false)
	end try
end run

on reopen
	-- Fired when the Dock icon is clicked while this applet is already running
	-- (on run is blocked on the launcher). Focus/create the dashboard UI only.
	-- --focus-ui upgrades to a full launch when the server is down (CHR2-011).
	try
		my jhRunLauncher("--focus-ui")
	on error errMsg number errNum
		my jhHandleShellError(errMsg, errNum, true)
	end try
end reopen
