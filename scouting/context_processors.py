from django.db import OperationalError, ProgrammingError
from wagtail.models import Site

from .models import SiteContentSettings, UserProfile
from .view_access import (
    _effective_profile,
    _is_acting_as_producer,
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
            'acting_as_producer': False,
            'show_producer_nav': False,
            'show_technician_nav': False,
            'can_manage_producers': False,
            'site_content_settings': site_content_settings,
        }

    real_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    effective_profile = _effective_profile(request)
    acting_as_producer = _is_acting_as_producer(request)
    can_manage_producers = request.user.is_superuser or real_profile.role == UserProfile.ROLE_TECHNICIAN
    return {
        'viewer_profile': effective_profile,
        'real_viewer_profile': real_profile,
        'active_producer_profile': effective_profile if acting_as_producer else None,
        'acting_as_producer': acting_as_producer,
        'show_producer_nav': _show_producer_interface(request),
        'show_technician_nav': _show_technician_interface(request),
        'can_manage_producers': can_manage_producers and not acting_as_producer,
        'site_content_settings': site_content_settings,
    }
