from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .decision_engine import evaluate_record_recommendation
from .forms import PlantSeriesForm, RecommendationDismissForm
from .models import PlantSeries, RecommendationResponse, ScoutingRecord, Variety
from .view_access import _effective_profile, _effective_user, _is_technician, _show_producer_interface
from .view_recommendation_support import (
    _dismiss_reasons_queryset,
    _latest_series_recommendation,
    _recommendation_record_queryset_for_user,
    _sanitize_next_url,
)


@login_required
def my_series_view(request):
    profile = _effective_profile(request)
    effective_user = _effective_user(request)
    if (not request.user.is_superuser) and _is_technician(request.user) and not _show_producer_interface(request):
        messages.error(request, 'La gestion de series est reservee aux producteurs.')
        return redirect('dashboard')

    records_prefetch = Prefetch(
        'records',
        queryset=ScoutingRecord.objects.select_related('crop_ref', 'plant_series').prefetch_related(
            'leaf_observations',
            'recommendation_responses__dismiss_reason',
            'recommendation_responses__lever',
            'recommendation_responses__action',
        ),
    )
    if request.user.is_superuser and not _show_producer_interface(request):
        series_qs = PlantSeries.objects.select_related('crop', 'conduct_type', 'variety', 'user').prefetch_related(
            records_prefetch
        )
    else:
        series_qs = (
            PlantSeries.objects.filter(user=effective_user)
            .select_related('crop', 'conduct_type', 'variety')
            .prefetch_related(records_prefetch)
        )
    editing_id = request.GET.get('edit')
    editing_instance = None
    if editing_id:
        try:
            editing_instance = series_qs.get(id=int(editing_id))
        except (ValueError, PlantSeries.DoesNotExist):
            messages.warning(request, 'Serie a modifier introuvable.')

    if request.method == 'POST':
        post_series_id = request.POST.get('series_id')
        if post_series_id:
            try:
                editing_instance = series_qs.get(id=int(post_series_id))
            except (ValueError, PlantSeries.DoesNotExist):
                editing_instance = None
        form = PlantSeriesForm(request.POST, request.FILES, instance=editing_instance)
        if form.is_valid():
            series = form.save(commit=False)
            series.user = effective_user
            new_variety_name = (form.cleaned_data.get('new_variety_name') or '').strip()
            if new_variety_name:
                variety = Variety.objects.filter(crop=series.crop, name__iexact=new_variety_name).first()
                if variety is None:
                    variety = Variety.objects.create(
                        crop=series.crop,
                        name=new_variety_name,
                        is_active=True,
                        created_by=request.user,
                    )
                series.variety = variety
            series.save()
            messages.success(request, 'Serie enregistree.')
            return redirect('my_series')
    else:
        form = PlantSeriesForm(instance=editing_instance)

    series_list = list(series_qs)
    for series in series_list:
        _latest_series_recommendation(series)

    varieties = Variety.objects.filter(is_active=True).values('id', 'name', 'crop_id')
    return render(
        request,
        'scouting/my_series.html',
        {
            'form': form,
            'series_list': series_list,
            'editing_instance': editing_instance,
            'varieties': list(varieties),
            'profile': profile,
        },
    )


@login_required
def my_recommendations_view(request):
    if (not request.user.is_superuser) and _is_technician(request.user) and not _show_producer_interface(request):
        messages.error(request, 'Les recommandations producteur sont accessibles depuis un compte producteur.')
        return redirect('technician_records')

    effective_user = _effective_user(request)
    records_prefetch = Prefetch(
        'records',
        queryset=ScoutingRecord.objects.select_related('crop_ref', 'plant_series').prefetch_related(
            'leaf_observations',
            'recommendation_responses__dismiss_reason',
            'recommendation_responses__lever',
            'recommendation_responses__action',
        ),
    )
    series_list = (
        PlantSeries.objects.filter(user=effective_user, is_active=True)
        .select_related('crop', 'conduct_type', 'variety')
        .prefetch_related(records_prefetch)
        .order_by('name')
    )
    recommendation_cards = []
    for series in series_list:
        recommendation = _latest_series_recommendation(series)
        if recommendation and recommendation['rule'] and recommendation['is_open']:
            recommendation_cards.append(
                {
                    'series': series,
                    'record': series.latest_record,
                    'recommendation': recommendation,
                }
            )

    return render(
        request,
        'scouting/my_recommendations.html',
        {
            'recommendation_cards': recommendation_cards,
            'dismiss_reasons': _dismiss_reasons_queryset(),
        },
    )


@login_required
def recommendation_dismiss_view(request, record_id):
    if request.method != 'POST':
        return redirect('my_recommendations')

    record = get_object_or_404(_recommendation_record_queryset_for_user(_effective_user(request)), id=record_id)
    recommendation = evaluate_record_recommendation(record)
    next_url = _sanitize_next_url(request.POST.get('next'), reverse('my_recommendations'))

    if not recommendation['rule']:
        messages.error(request, 'Aucune recommandation active a fermer pour ce comptage.')
        return redirect(next_url)

    form = RecommendationDismissForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Le formulaire de non-suivi est invalide.')
        return redirect(next_url)

    RecommendationResponse.objects.update_or_create(
        record=record,
        rule=recommendation['rule'],
        defaults={
            'status': 'dismissed',
            'handled_by': request.user,
            'dismiss_reason': form.cleaned_data['dismiss_reason'],
            'dismiss_note': form.cleaned_data['dismiss_note'],
            'lever': None,
            'action': None,
        },
    )
    messages.success(request, 'Recommandation retiree des recommandations en cours.')
    return redirect(next_url)
