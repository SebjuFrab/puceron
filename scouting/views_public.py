from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import ScoutingRecord
from .views_support import (
    _filter_records,
    _get_profile,
    _info_pages_queryset,
    _is_technician,
    _producer_dashboard_context,
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
    data = {
        'name': 'PUCERON',
        'short_name': 'PUCERON',
        'start_url': '/',
        'display': 'standalone',
        'background_color': '#ffffff',
        'theme_color': '#198754',
        'description': 'Suivi pucerons et auxiliaires en cultures sous abri.',
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
    profile = _get_profile(request.user)
    if not _is_technician(request.user):
        return render(request, 'scouting/dashboard_compare.html', _producer_dashboard_context(request))

    records = ScoutingRecord.objects.all()
    records = _filter_records(request, records)

    avg_values = records.aggregate(
        avg_aphid=Avg('aphid_infested_percent'),
        avg_aux=Avg('auxiliary_total'),
    )
    weekly = (
        records.values('year', 'week')
        .annotate(avg_aphid=Avg('aphid_infested_percent'), avg_aux=Avg('auxiliary_total'))
        .order_by('year', 'week')
    )

    labels = [f"S{item['week']}-{item['year']}" for item in weekly]
    aphid_points = [float(item['avg_aphid']) for item in weekly]
    aux_points = [float(item['avg_aux']) / 10.0 for item in weekly]

    return render(
        request,
        'scouting/dashboard.html',
        {
            'profile': profile,
            'avg_aphid': round(float(avg_values['avg_aphid'] or 0), 2),
            'avg_aux_per_plant': round(float(avg_values['avg_aux'] or 0) / 10.0, 2),
            'labels': labels,
            'aphid_points': aphid_points,
            'aux_points': aux_points,
        },
    )


