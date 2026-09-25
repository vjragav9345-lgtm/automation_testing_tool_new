"""Builds the self-contained HTML report for a test run.

Screenshots get inlined as base64 data URIs so the report is a single
file someone can email around without also having to hand over the
screenshots folder.
"""
import base64
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from storage.repository import BASE_DIR

logger = logging.getLogger(__name__)

TEMPLATES_DIR = BASE_DIR / "templates"
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

# same set the dashboard's own script.js (VALIDATION_ACTION_TYPES) uses to
# tell an assertion-style step (validate_*/check_checked/compare_*/...)
# apart from an interaction or a passive data capture - kept in sync with
# it by hand, since the two live in different files with no shared config
VALIDATION_ACTION_TYPES = {
    "validate", "validate_element", "validate_text", "validate_attribute",
    "validate_visible", "validate_url", "validate_value", "validate_enabled",
    "check_checked", "validate_value_range", "compare_value",
    "compare_counts", "count_summary", "detect_duplicates",
    "compare_list_overlap",
}


def _to_data_uri(rel_path):
    if not rel_path:
        return None
    full = BASE_DIR / rel_path
    if not full.exists():
        return None
    try:
        data = base64.b64encode(full.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{data}"
    except OSError as e:
        logger.warning("couldn't embed screenshot %s: %s", rel_path, e)
        return None


def _step_outcome(s):
    """FIX 4 (report/live-log correctness): a step the generator flagged
    not_run (stop_on_failure halted before reaching it) or otp_role (a
    manual-input/OTP step that didn't itself fail) is neither a pass nor a
    genuine failure - CONFIRMED REAL BUG this fixes, against an actual
    Myntra recording: the downloadable report counted 48 never-attempted
    steps as "failed" and listed the first of them as the failure reason.
    Kept in sync with static/js/script.js's own _stepOutcome() by
    construction (same three fields, same precedence)."""
    if s.get("not_run"):
        return "not_run"
    if s.get("otp_role") and s.get("success") is not False:
        return "manual_input"
    return "pass" if s.get("success") else "fail"


def generate_report(execution_result: dict, output_dir: Path = None) -> Path:
    template = _env.get_template("report.html")

    steps = []
    for s in execution_result.get("steps", []):
        steps.append({**s, "screenshot_data": _to_data_uri(s.get("screenshot")), "outcome": _step_outcome(s)})

    ui_elements = execution_result.get("ui_elements", [])
    execution_summary = {
        "total": len(steps),
        "passed": sum(1 for s in steps if s["outcome"] == "pass"),
        "failed": sum(1 for s in steps if s["outcome"] == "fail"),
        "not_run": sum(1 for s in steps if s["outcome"] == "not_run"),
        "manual_input": sum(1 for s in steps if s["outcome"] == "manual_input"),
        "first_failed": next((s for s in steps if s["outcome"] == "fail"), None),
    }
    ui_summary = {
        "total": len(ui_elements),
        "found": sum(1 for e in ui_elements if e.get("status") == "PASS"),
        "missing": sum(1 for e in ui_elements if e.get("status") == "FAIL"),
        "status": execution_result.get("ui_elements_status"),
    }

    product_validation = execution_result.get("product_validation")
    if product_validation:
        product_validation = {**product_validation, "screenshot_data": _to_data_uri(product_validation.get("screenshot"))}

    # step-level validation breakdown for the report's own "Step
    # Validations" section - same distinction (and same numbers) the
    # dashboard's live validation panel already shows (see
    # VALIDATION_ACTION_TYPES in static/js/script.js), so the downloadable
    # report never disagrees with what was shown live.
    validation_steps = [s for s in steps if s.get("action_type") in VALIDATION_ACTION_TYPES]
    action_steps = [s for s in steps if s.get("action_type") not in VALIDATION_ACTION_TYPES]
    validation_summary = {
        "actions_passed": sum(1 for s in action_steps if s["outcome"] == "pass"),
        "actions_total": len(action_steps),
        "validations_passed": sum(1 for s in validation_steps if s["outcome"] == "pass"),
        "validations_total": len(validation_steps),
        "failed_total": sum(1 for s in steps if s["outcome"] == "fail"),
        "not_run_total": sum(1 for s in steps if s["outcome"] == "not_run"),
        "manual_input_total": sum(1 for s in steps if s["outcome"] == "manual_input"),
        "locator_warnings": sum(1 for s in steps if (s.get("locator_report") or {}).get("weak")),
    }

    html = template.render(
        result=execution_result,
        steps=steps,
        ui_elements=ui_elements,
        ui_summary=ui_summary,
        execution_summary=execution_summary,
        validation_steps=validation_steps,
        validation_summary=validation_summary,
        product_validation=product_validation,
        final_screenshot_data=_to_data_uri(execution_result.get("final_screenshot")),
        generated_at=datetime.now().isoformat(),
    )

    # a caller with a per-run output folder already (the dashboard's Run
    # Test flow - see execute_test in executor/run_execution.py) passes
    # it here so the HTML report lands alongside that same run's
    # screenshots and report.json instead of its own separate top-level
    # location. Falls back to the old shared REPORTS_DIR only for a
    # caller that doesn't have a per-run folder to give it.
    if output_dir:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "report.html"
    else:
        run_id = execution_result.get("run_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
        path = REPORTS_DIR / f"report_{run_id}.html"
    path.write_text(html, encoding="utf-8")
    logger.info("wrote report to %s", path)
    return path
