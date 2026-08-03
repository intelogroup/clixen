"""
Unit tests for query_guard.check_food_intent().

Covers:
  - fires on bare hunger/food desire with no service or location
  - fires on generic dish query without service/location
  - fires on "where can I get X" food sourcing
  - suppressed when delivery service explicitly mentioned
  - suppressed when a specific restaurant is named
  - suppressed when has_history is True
  - location mismatch detection (Boston vs Everett)
  - harness integration: guard short-circuits before model call
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.query_guard import check_all as check_food_intent


# ── check_food_intent should fire ─────────────────────────────────────────────

class TestFoodIntentFires:
    """Guard should return a clarifying question."""

    def test_im_hungry(self):
        result = check_food_intent("I'm hungry")
        assert result is not None
        assert "DoorDash" in result or "Uber Eats" in result

    def test_i_want_food(self):
        assert check_food_intent("i want food") is not None

    def test_i_want_bowl_of_rice(self):
        result = check_food_intent("I want a bowl of rice with black beans")
        assert result is not None
        assert "delivered" in result or "delivery" in result

    def test_where_can_i_get_pad_thai(self):
        result = check_food_intent("Where can I get some pad thai")
        assert result is not None
        assert "delivery" in result or "restaurant" in result

    def test_i_need_dinner(self):
        assert check_food_intent("i need dinner") is not None

    def test_i_crave_sushi(self):
        assert check_food_intent("i crave sushi") is not None

    def test_im_starving(self):
        assert check_food_intent("I'm starving") is not None

    def test_whats_good_to_eat(self):
        assert check_food_intent("What's good to eat around here") is not None


class TestFoodIntentLocationMismatch:
    """Guard detects when query mentions a city different from known address."""

    def test_boston_address_mismatch(self):
        result = check_food_intent("Where can I get a burrito in Boston")
        assert result is not None
        assert "Boston" in result
        assert "Everett" in result

    def test_cambridge_address_mismatch(self):
        result = check_food_intent("I want pizza in Cambridge")
        assert result is not None
        assert "Cambridge" in result
        assert "Everett" in result

    def test_everett_matches_no_mismatch(self):
        assert check_food_intent("I want pizza in Everett") is None


# ── check_food_intent should NOT fire ─────────────────────────────────────────

class TestFoodIntentSuppressedWithService:
    """Explicit delivery service → no guard."""

    def test_doordash_mentioned(self):
        assert check_food_intent("Show me Chipotle on DoorDash") is None

    def test_ubereats_mentioned(self):
        assert check_food_intent("Order pizza from Uber Eats") is None

    def test_delivery_mentioned(self):
        assert check_food_intent("I want delivery from Chipotle") is None

    def test_pickup_mentioned(self):
        assert check_food_intent("I want pickup from Taco Bell") is None


class TestFoodIntentSuppressedWithPlace:
    """Specific restaurant or location → no guard."""

    def test_restaurant_near_me(self):
        assert check_food_intent("Find Mexican restaurants near me") is None

    def test_specific_restaurant(self):
        assert check_food_intent("What are the hours for Taco Bell") is None

    def test_address_given(self):
        assert check_food_intent("Pizza delivery to 123 Main St") is None

    def test_door_dash_plus_in_boston(self):
        assert check_food_intent("Chipotle delivery in Boston") is None


class TestFoodIntentWithHistory:
    """History only suppresses deictic guard; food/sports/data run regardless."""

    def test_im_hungry_with_history(self):
        assert check_food_intent("I'm hungry", has_history=True) is not None

    def test_bowl_of_rice_with_history(self):
        assert check_food_intent("I want a bowl of rice", has_history=True) is not None

    def test_where_pad_thai_with_history(self):
        assert check_food_intent("Where can I get pad thai", has_history=True) is not None


class TestFoodIntentNonFood:
    """Non-food queries get caught by other guards in check_all priority order."""

    def test_weather(self):
        """'what's the weather today' triggers the weather guard (no city
        named), same pattern as test_sports_missing_year below — stale since
        the weather-no-location guard (query_guard.py:~405, added
        2026-07-10) runs before the food guard in check_all's priority order."""
        assert check_food_intent("What's the weather today") == "Which city's weather do you want?"

    def test_programming(self):
        assert check_food_intent("how do I reverse a list in Python") is None

    def test_sports_missing_year(self):
        """'who won the super bowl' triggers sports guard (missing year), not food."""
        result = check_food_intent("who won the super bowl")
        assert result is not None
        assert "year" in result or "edition" in result

    def test_tech_question(self):
        assert check_food_intent("what is the capital of France") is None

    def test_greeting(self):
        assert check_food_intent("hello how are you") is None

    def test_deictic_fires(self):
        """'explain this process' triggers deictic guard, not food."""
        result = check_food_intent("explain this process")
        assert result is not None
        assert "referring to" in result or "context" in result


# ── harness integration: guard short-circuits before local_chat ────────────────

class TestHarnessFoodIntentGuard:
    """Guard in harness.py must return clarifying question without calling the model."""

    def test_hungry_query_skips_model(self):
        """harness.run() with 'I'm hungry' must NOT call local_chat."""
        import harness
        with (
            patch.object(harness, "local_chat") as mock_chat,
            patch.object(harness, "conv_get", return_value=[]),
            patch.object(harness, "conv_append"),
            patch.object(harness, "trim_to_budget", return_value=[]),
            patch.object(harness, "compact_old_turns"),
            patch.object(harness, "get_lock", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            response, model_used, intent = harness.run(
                "I'm hungry",
                chat_id="test-food-session-1",
            )

        mock_chat.assert_not_called()
        assert "?" in response
        assert model_used == "guard"
        assert intent == "unclear"

    def test_doordash_query_reaches_model(self):
        """Explicit DoorDash query must reach local_chat normally."""
        import harness
        mock_result = "Here is Chipotle on DoorDash..."
        with (
            patch.object(harness, "local_chat", return_value=mock_result) as mock_chat,
            patch.object(harness, "conv_get", return_value=[]),
            patch.object(harness, "conv_append"),
            patch.object(harness, "trim_to_budget", return_value=[]),
            patch.object(harness, "compact_old_turns"),
            patch.object(harness, "get_lock", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            response, model_used, intent = harness.run(
                "Show me Chipotle on DoorDash",
                chat_id="test-food-session-2",
            )

        mock_chat.assert_called_once()
        assert model_used != "guard"
