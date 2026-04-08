from django.urls import path

from . import views

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('infos/', views.info_index_view, name='info_index'),
    path('infos/<str:page_key>/', views.info_page_view, name='info_page'),
    path('offline/', views.offline_view, name='offline'),
    path('manifest.webmanifest', views.manifest_view, name='manifest'),
    path('sw.js', views.service_worker_view, name='service_worker'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('mon-profil/', views.my_profile_view, name='my_profile'),
    path('comptes/nouveau-producteur/', views.producer_create_view, name='producer_create'),
    path('comptes/import-producteurs/', views.producer_import_view, name='producer_import'),
    path('comptes/import-producteurs/template/', views.producer_import_template_view, name='producer_import_template'),
    path('comptes/producteur/<int:producer_id>/modifier/', views.producer_update_view, name='producer_update'),
    path('mes-series/', views.my_series_view, name='my_series'),
    path('mes-recommandations/', views.my_recommendations_view, name='my_recommendations'),
    path('saisie/', views.record_create_view, name='record_create'),
    path('saisie/action/', views.action_create_view, name='action_create'),
    path(
        'technicien/controle/<int:producer_id>/',
        views.producer_control_start_view,
        name='producer_control_start',
    ),
    path('technicien/controle/quitter/', views.producer_control_stop_view, name='producer_control_stop'),
    path(
        'super-admin/controle-technicien/<int:technician_id>/',
        views.technician_control_start_view,
        name='technician_control_start',
    ),
    path(
        'super-admin/controle-technicien/quitter/',
        views.technician_control_stop_view,
        name='technician_control_stop',
    ),
    path(
        'recommandations/<int:record_id>/ne-pas-suivre/',
        views.recommendation_dismiss_view,
        name='recommendation_dismiss',
    ),
    path('saisie/<int:record_id>/modifier/', views.record_update_view, name='record_update'),
    path('saisie/<int:record_id>/supprimer/', views.record_delete_view, name='record_delete'),
    path('mes-donnees/', views.my_records_view, name='my_records'),
    path('actions/<int:action_id>/supprimer/', views.action_delete_view, name='action_delete'),
    path('technicien/donnees/', views.technician_records_view, name='technician_records'),
    path('export.xlsx', views.export_records_view, name='export_records'),
    path('export-actions.xlsx', views.export_actions_view, name='export_actions'),
]
