import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.cloud_client import DEFAULT_CLOUD_MODEL as CLOUD_MODEL
from clients.router import classify_telegram


def test_classify_telegram_routes_math_to_cloud():
    model, intent = classify_telegram("prove the eigenvalue decomposition theorem")
    assert (model, intent) == (CLOUD_MODEL, "math")


def test_classify_telegram_routes_email_to_cloud():
    model, intent = classify_telegram("check my inbox and send me a summary on telegram")
    assert (model, intent) == (CLOUD_MODEL, "email")
