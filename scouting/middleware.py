from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


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
                messages.error(request, 'Le CMS est reserve au super-admin.')
                return redirect('dashboard')
        return self.get_response(request)
