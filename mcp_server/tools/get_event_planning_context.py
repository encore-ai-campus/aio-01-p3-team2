"""Read the game catalog before the event planner creates a draft."""

from ._data import load_game_data

TARGET_USERS = {"new_user", "returning_user", "all_user"}


def get_event_planning_context(target_user: str, duration: int) -> dict:
    """Return game choices and the selected user's rules for a 1–7 day event."""
    if not isinstance(target_user, str) or target_user not in TARGET_USERS:
        raise ValueError("target_user must be new_user, returning_user, or all_user")
    if type(duration) is not int or not 1 <= duration <= 7:
        raise ValueError("duration must be an integer from 1 to 7")

    data = load_game_data()
    segment = data["user_segments"][target_user].copy()
    if target_user != "all_user":
        segment["expected_levels"] = {
            day: level
            for day, level in segment["expected_levels"].items()
            if int(day) <= duration
        }

    return {
        "target_user": target_user,
        "duration": duration,
        "contents": data["contents"],
        "items": data["items"],
        "user_segments": {target_user: segment},
        "game_data_version": data["game_data_version"],
        "source": "primary",
    }
