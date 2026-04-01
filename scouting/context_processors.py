from django.db import OperationalError, ProgrammingError
from wagtail.models import Site

from .models import SiteContentSettings, UserProfile


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
            'can_manage_producers': False,
            'site_content_settings': site_content_settings,
        }

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    can_manage_producers = request.user.is_superuser or profile.role == UserProfile.ROLE_TECHNICIAN
    return {
        'viewer_profile': profile,
        'can_manage_producers': can_manage_producers,
        'site_content_settings': site_content_settings,
    }
