from .views_producers import (
    producer_create_view,
    producer_import_template_view,
    producer_import_view,
    producer_update_view,
)
from .views_public import (
    dashboard_view,
    info_index_view,
    info_page_view,
    landing_view,
    manifest_view,
    offline_view,
    service_worker_view,
)
from .views_records import (
    action_create_view,
    my_profile_view,
    my_recommendations_view,
    my_records_view,
    my_series_view,
    recommendation_dismiss_view,
    record_create_view,
    record_update_view,
)
from .views_technician import export_records_view, technician_records_view

__all__ = [
    'action_create_view',
    'dashboard_view',
    'export_records_view',
    'info_index_view',
    'info_page_view',
    'landing_view',
    'manifest_view',
    'my_profile_view',
    'my_recommendations_view',
    'my_records_view',
    'my_series_view',
    'offline_view',
    'producer_create_view',
    'producer_import_template_view',
    'producer_import_view',
    'producer_update_view',
    'recommendation_dismiss_view',
    'record_create_view',
    'record_update_view',
    'service_worker_view',
    'technician_records_view',
]
