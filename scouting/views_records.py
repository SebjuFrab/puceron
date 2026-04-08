from .views_profile import action_delete_view, my_profile_view, my_records_view, record_delete_view
from .views_scouting import action_create_view, action_update_view, record_create_view, record_update_view
from .views_series import my_recommendations_view, my_series_view, recommendation_dismiss_view

__all__ = [
    "action_create_view",
    "action_delete_view",
    "action_update_view",
    "my_profile_view",
    "my_recommendations_view",
    "my_records_view",
    "my_series_view",
    "recommendation_dismiss_view",
    "record_create_view",
    "record_delete_view",
    "record_update_view",
]
