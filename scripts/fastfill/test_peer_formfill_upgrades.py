"""Unit tests for peer-repo inspired Select One / post-advance field waits."""

from __future__ import annotations

import inspect


def test_is_workday_select_placeholder():
    from exp_workday_selectors import is_workday_select_placeholder

    assert is_workday_select_placeholder(None)
    assert is_workday_select_placeholder("")
    assert is_workday_select_placeholder("Select One")
    assert is_workday_select_placeholder("select")
    assert is_workday_select_placeholder("Select One Required")
    assert is_workday_select_placeholder("Country Select One Required")
    assert is_workday_select_placeholder("Select a value")
    assert not is_workday_select_placeholder("Colorado")
    assert not is_workday_select_placeholder("United States of America")
    assert not is_workday_select_placeholder("Mobile")


def test_select_one_uses_placeholder_cleared_verify():
    """jobhard-style: Select One verify uses is_workday_select_placeholder + verify_via."""
    import exp_workday_selectors as m

    src = inspect.getsource(m._fill_select_one_by_label)
    assert "is_workday_select_placeholder" in src
    assert "verify_via" in src
    assert "contains_pick" in src


def test_gate_waits_for_fields_after_advance():
    """antomicblitz #3: after Next, poll until form fields mount."""
    import exp_workday_selectors as m

    assert callable(m.wait_for_workday_form_fields)
    src = inspect.getsource(m._gate_then_advance)
    assert "wait_for_workday_form_fields" in src
    assert "fields_ready_after_advance" in src


def test_wait_for_workday_form_fields_exported():
    from exp_workday_selectors import wait_for_workday_form_fields

    src = inspect.getsource(wait_for_workday_form_fields)
    assert "formField-" in src
    assert "fields_not_ready" in src


if __name__ == "__main__":
    test_is_workday_select_placeholder()
    test_select_one_uses_placeholder_cleared_verify()
    test_gate_waits_for_fields_after_advance()
    test_wait_for_workday_form_fields_exported()
    print("ok")
