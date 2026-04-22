from django.db import OperationalError, ProgrammingError
from wagtail.models import Site

from .models import SiteContentSettings, TechnicianCoFollowRequest, UserProfile
from .view_access import (
    _active_technician_profiles_for_producer,
    _can_manage_producers,
    _effective_access_restriction,
    _effective_profile,
    _get_profile,
    _manager_user,
    _acting_technician_profile,
    _is_effective_producer_read_only,
    _is_effective_technician_denied,
    _is_acting_as_technician,
    _is_acting_as_producer,
    _is_technician,
    _show_producer_interface,
    _show_technician_interface,
)



def _site_content_settings(request):
    try:
        site = Site.find_for_request(request)
        if site is None:
            site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            return None
        return SiteContentSettings.for_site(site)
    except (OperationalError, ProgrammingError):
        return None



def viewer_flags(request):
    site_content_settings = _site_content_settings(request)
    if not request.user.is_authenticated:
        return {
            'viewer_profile': None,
            'real_viewer_profile': None,
            'active_producer_profile': None,
            'active_technician_profile': None,
            'acting_as_producer': False,
            'acting_as_technician': False,
            'show_producer_nav': False,
            'show_technician_nav': False,
            'can_manage_producers': False,
            'effective_producer_read_only': False,
            'effective_technician_denied': False,
            'effective_access_message': '',
            'active_producer_technicians': [],
            'pending_cofollow_request_count': 0,
            'site_content_settings': site_content_settings,
        }

    real_profile = _get_profile(request.user)
    effective_profile = _effective_profile(request)
    active_technician_profile = _acting_technician_profile(request)
    acting_as_technician = _is_acting_as_technician(request)
    acting_as_producer = _is_acting_as_producer(request)
    can_manage_producers = _can_manage_producers(_manager_user(request))
    restriction = _effective_access_restriction(request, for_write=True)
    manager_user = _manager_user(request)
    show_technician_nav = _show_technician_interface(request)
    pending_cofollow_request_count = 0
    if show_technician_nav and _is_technician(manager_user):
        pending_cofollow_request_count = TechnicianCoFollowRequest.objects.filter(
            target_technician=manager_user,
            status=TechnicianCoFollowRequest.STATUS_PENDING,
        ).count()
    active_producer_technicians = []
    if effective_profile.role == UserProfile.ROLE_PRODUCER:
        active_producer_technicians = _active_technician_profiles_for_producer(effective_profile)
    return {
        'viewer_profile': effective_profile,
        'real_viewer_profile': real_profile,
        'active_producer_profile': effective_profile if acting_as_producer else None,
        'active_technician_profile': active_technician_profile,
        'acting_as_technician': acting_as_technician,
        'acting_as_producer': acting_as_producer,
        'show_producer_nav': _show_producer_interface(request),
        'show_technician_nav': show_technician_nav,
        'can_manage_producers': can_manage_producers and not acting_as_producer,
        'effective_producer_read_only': _is_effective_producer_read_only(request),
        'effective_technician_denied': _is_effective_technician_denied(request),
        'effective_access_message': restriction['message'] if restriction else '',
        'active_producer_technicians': active_producer_technicians,
        'pending_cofollow_request_count': pending_cofollow_request_count,
        'site_content_settings': site_content_settings,
    }
