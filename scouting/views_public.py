from django.contrib.auth.decorators import login_required
from django.db import OperationalError, ProgrammingError
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from wagtail.models import Site

from .models import SiteContentSettings
from .views_support import (
    _effective_profile,
    _info_pages_queryset,
    _producer_dashboard_context,
    _show_producer_interface,
    _technician_dashboard_context,
)


def landing_view(request):
    info_pages = _info_pages_queryset()
    return render(request, 'scouting/landing.html', {'info_pages': info_pages})


@login_required
def info_index_view(request):
    pages = _info_pages_queryset()
    return render(request, 'scouting/info_index.html', {'pages': pages})


@login_required
def info_page_view(request, page_key):
    page = get_object_or_404(_info_pages_queryset(), Q(page_key=page_key) | Q(slug=page_key))
    return render(request, 'scouting/info_page.html', {'page': page})



def offline_view(request):
    return render(request, 'scouting/offline.html')



def manifest_view(request):
    icons = []
    try:
        site = Site.find_for_request(request) or Site.objects.filter(is_default_site=True).first()
        if site:
            site_settings = SiteContentSettings.for_site(site)
            if site_settings and site_settings.favicon:
                icons.append(
                    {
                        'src': site_settings.favicon.file.url,
                        'sizes': f'{site_settings.favicon.width}x{site_settings.favicon.height}',
                        'type': 'image/png',
                        'purpose': 'any',
                    }
                )
    except (OperationalError, ProgrammingError):
        icons = []

    data = {
        'name': 'PUCERON',
        'short_name': 'PUCERON',
        'start_url': '/',
        'display': 'standalone',
        'background_color': '#ffffff',
        'theme_color': '#198754',
        'description': 'Suivi pucerons et auxiliaires en cultures sous abri.',
        'icons': icons,
    }
    return JsonResponse(data, content_type='application/manifest+json')



def service_worker_view(request):
    js = """
const CACHE_NAME = 'puceron-v1';
const URLS = ['/', '/offline/', '/accounts/login/'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(URLS)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
        return res;
      }).catch(() => caches.match(req).then(cached => cached || caches.match('/offline/')))
    );
    return;
  }
  event.respondWith(
    caches.match(req).then(cached => cached || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
      return res;
    }))
  );
});
"""
    return HttpResponse(js, content_type='application/javascript')


@login_required
def dashboard_view(request):
    if _show_producer_interface(request):
        return render(request, 'scouting/dashboard_compare.html', _producer_dashboard_context(request))

    profile = _effective_profile(request)
    context = _technician_dashboard_context(request)
    context['profile'] = profile
    return render(request, 'scouting/dashboard.html', context)
