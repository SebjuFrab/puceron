from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from .view_access import _get_profile
from .models import UserProfile


class SuperuserCmsOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ''
        if path == '/cms/' or path.startswith('/cms/'):
            if not request.user.is_authenticated:
                login_url = reverse('login')
                return redirect(f'{login_url}?next={request.get_full_path()}')
            if not request.user.is_superuser:
                messages.error(request, 'Le CMS est réservé au super-admin.')
                return redirect('dashboard')
        return self.get_response(request)


class TechnicianLicenseBlockedMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._allowed_paths = {
            reverse('logout'),
            reverse('login'),
        }

    def __call__(self, request):
        if not request.user.is_authenticated or request.user.is_superuser:
            return self.get_response(request)

        profile = _get_profile(request.user)
        if profile.role != UserProfile.ROLE_TECHNICIAN or profile.has_active_license:
            return self.get_response(request)

        path = request.path or ''
        if path in self._allowed_paths:
            return self.get_response(request)
        if path.startswith('/static/') or path.startswith('/media/') or path.startswith('/accounts/'):
            return self.get_response(request)

        message = profile.deactivation_message or 'Votre licence technicien est inactive. Contactez le super-admin.'
        return TemplateResponse(
            request,
            'scouting/access_denied.html',
            {
                'access_denied_title': 'Accès refusé',
                'access_denied_message': message,
            },
            status=403,
        )
