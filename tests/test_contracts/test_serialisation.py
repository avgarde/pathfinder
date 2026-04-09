"""Tests for contract serialisation round-trips."""

import json

from pathfinder.contracts import (
    AppReference,
    ElementReference,
    NavigationContext,
    ScreenObservation,
    ScreenType,
    UIElement,
)


def test_screen_observation_round_trip():
    """ScreenObservation should survive JSON serialisation and deserialisation."""
    obs = ScreenObservation(
        observation_id="test_01",
        screen_purpose="Login screen with email and password fields",
        screen_type=ScreenType.LOGIN,
        app_state={"logged_in": "no"},
        elements=[
            UIElement(
                element_id="e1",
                reference=ElementReference(text="Sign In", resource_id="btn_signin"),
                element_type="button",
                semantic_role="Initiates the sign-in authentication process",
                label="Sign In",
                is_interactive=True,
                possible_actions=["tap"],
                inferred_destination="Home screen",
                confidence=0.95,
            ),
            UIElement(
                element_id="e2",
                reference=ElementReference(text="Email"),
                element_type="text_field",
                semantic_role="Email address input for authentication",
                label="Email",
                is_interactive=True,
                possible_actions=["tap", "type"],
                confidence=0.99,
            ),
        ],
        navigation_context=NavigationContext(
            visible_navigation=["Sign In", "Register"],
            back_available=False,
            inferred_depth=0,
        ),
        confidence=0.92,
    )

    # Serialise
    json_str = obs.model_dump_json(indent=2)
    data = json.loads(json_str)

    # Verify structure
    assert data["observation_id"] == "test_01"
    assert data["screen_type"] == "login"
    assert len(data["elements"]) == 2
    assert data["elements"][0]["semantic_role"] == "Initiates the sign-in authentication process"

    # Deserialise
    obs2 = ScreenObservation.model_validate_json(json_str)
    assert obs2.observation_id == obs.observation_id
    assert obs2.screen_type == ScreenType.LOGIN
    assert len(obs2.elements) == 2
    assert obs2.elements[0].reference.resource_id == "btn_signin"
    assert obs2.elements[1].possible_actions == ["tap", "type"]
    assert obs2.navigation_context.inferred_depth == 0


def test_app_reference_minimal():
    """AppReference should work with minimal fields."""
    ref = AppReference(package_name="com.example.app")
    json_str = ref.model_dump_json()
    ref2 = AppReference.model_validate_json(json_str)
    assert ref2.package_name == "com.example.app"
    assert ref2.name is None
    assert ref2.credentials is None


def test_app_reference_full():
    """AppReference should handle all fields."""
    ref = AppReference(
        package_name="com.example.app",
        name="Example App",
        description="A test application",
        baseline="Standard e-commerce patterns",
        differentiation="Novel AR try-on feature",
        credentials={"email": "test@example.com", "password": "test123"},
    )
    json_str = ref.model_dump_json()
    ref2 = AppReference.model_validate_json(json_str)
    assert ref2.differentiation == "Novel AR try-on feature"
    assert ref2.credentials["email"] == "test@example.com"


def test_screen_observation_empty_elements():
    """ScreenObservation should work with no elements (e.g., loading screen)."""
    obs = ScreenObservation(
        observation_id="loading_01",
        screen_purpose="App loading/splash screen",
        screen_type=ScreenType.LOADING,
        confidence=0.8,
    )
    json_str = obs.model_dump_json()
    obs2 = ScreenObservation.model_validate_json(json_str)
    assert obs2.elements == []
    assert obs2.screen_type == ScreenType.LOADING
