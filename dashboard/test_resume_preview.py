#!/usr/bin/env python3
"""Resume PDF preview: keep the iframe across dossier poll re-renders.

Dummy fixture names only — never logs real resume text.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_JS = HERE / "static" / "app.js"
INDEX_HTML = HERE / "static" / "index.html"
NODE_TEST = HERE / "test_resume_preview.js"


def _fn_body(src: str, name: str) -> str:
    token = f"function {name}("
    start = src.find(token)
    if start < 0:
        return ""
    end = src.find("\nfunction ", start + 1)
    if end < 0:
        return src[start:]
    return src[start:end]


def _css_rules(html: str, selector: str) -> str:
    token = selector + " {"
    start = html.find(token)
    if start < 0:
        return ""
    end = html.find("}", start)
    if end < 0:
        return html[start:]
    return html[start : end + 1]


def test_resume_preview_source_keeps_iframe_across_rerender():
    """Poll must not detach/rebuild the PDF iframe (Chrome flashes white)."""
    src = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "heldFrame.remove()" not in src
    assert "function paintResumePreview(" in src
    assert "function canReuseResumePreviewFrame(" in src
    assert "function ensureDossierPreviewShell(" in src
    assert "resume-preview-host" in src

    mount = _fn_body(src, "mountResumePreview")
    paint = _fn_body(src, "paintResumePreview")
    render = _fn_body(src, "renderDossier")
    panel = _fn_body(src, "renderResumePanel")

    assert "Date.now()" not in mount
    assert "Date.now()" not in paint
    assert "?t=" not in mount
    assert "?t=" not in paint
    assert "Date.now()" not in render or "lastPollAt" in render  # poll clock is fine
    assert 'frame.src = `/resume/' not in render
    assert "${Date.now()}" not in mount
    assert "${Date.now()}" not in paint

    assert "Open in new tab" in panel
    assert "collapseResumePanel()" in panel
    assert 'onclick="collapseResumePanel()"' in panel
    assert "previewJobResume(jobId)" in src

    frame_css = _css_rules(html, ".resume-preview-frame")
    mount_css = _css_rules(html, ".resume-preview-mount")
    assert mount_css
    assert "background: #000000" in mount_css or "background:#000" in mount_css.replace(" ", "")
    assert "#fff" not in frame_css.lower()
    assert "#ffffff" not in frame_css.lower()


def test_resume_preview_node_reuse():
    proc = subprocess.run(
        ["node", str(NODE_TEST)],
        cwd=str(HERE.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "OK test_resume_preview.js" in proc.stdout


def test_resume_face_previews_while_fill_is_running():
    """Primary RESUME click (and saved filename) must preview during FILLING.

    Upload/Clear stay locked; that alert is only for those mutating paths.
    """
    src = APP_JS.read_text(encoding="utf-8")
    face = _fn_body(src, "executeResumeFace")
    popover = _fn_body(src, "renderResumePopover")

    preview_idx = face.find("previewJobResume(jobId)")
    disk_idx = face.find("jobHasDiskResume(job)")
    alert_idx = face.find("Resume upload/clear blocked")
    assert preview_idx >= 0, "RESUME face must open in-dashboard PDF preview"
    assert disk_idx >= 0
    assert disk_idx < alert_idx, "preview/disk check must run before fill lock"
    assert preview_idx < alert_idx, "primary RESUME click must preview before the upload/clear alert"

    assert "previewJobResume(" in popover
    assert 'id="resume-status"' in popover
    assert "midFill" in popover
    assert "Blocked while fill/tailor is running" in popover
    assert "clearJobResume" in popover
    assert "resume-upload-input" in popover


if __name__ == "__main__":
    test_resume_preview_source_keeps_iframe_across_rerender()
    test_resume_preview_node_reuse()
    test_resume_face_previews_while_fill_is_running()
    print("OK test_resume_preview")
