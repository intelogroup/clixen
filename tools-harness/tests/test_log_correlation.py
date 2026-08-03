"""
tests/test_log_correlation.py
Verify that log format contains the CURRENT_RUN_ID context var value.
"""
import io
import logging
from log_config import setup_logging, CURRENT_RUN_ID


def test_log_correlation_injects_run_id():
    """Verify that run_id is formatted into log output."""
    # harness.run() sets CURRENT_RUN_ID but doesn't reset it on return, so a real run_id
    # can still be active here if an earlier test in the same process called harness.run()
    # (e.g. test_harness_history_routing.py). Reset to the ContextVar's own default ("")
    # so this test's "unset" assertion below is isolated from that ordering.
    reset_token = CURRENT_RUN_ID.set("")

    # Write to a string stream to capture output
    log_stream = io.StringIO()
    logger = setup_logging("test_correlation_module")

    # Clear old handlers if any, to avoid duplicate printing during test
    logger.handlers.clear()

    # Add stream handler with the standard formatter
    fmt = "%(asctime)s [%(levelname)s] [%(run_id)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)
    from log_config import RunIdFilter
    run_filter = RunIdFilter()

    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(formatter)
    handler.addFilter(run_filter)
    logger.addHandler(handler)

    # Log something without setting run_id
    logger.info("Message without run_id")
    output_1 = log_stream.getvalue()
    assert "[--------]" in output_1

    # Log something with active run_id
    token = CURRENT_RUN_ID.set("abc12345")
    try:
        logger.info("Message with run_id")
    finally:
        CURRENT_RUN_ID.reset(token)

    output_2 = log_stream.getvalue()
    assert "[abc12345]" in output_2
    CURRENT_RUN_ID.reset(reset_token)
