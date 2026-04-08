import json
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

try:
    from openpyxl import Workbook
except Exception:  # pragma: no cover
    Workbook = None

from .decision_engine import evaluate_record_recommendation
from .models import AuxiliaryTaxon, PlantAction, PlantSeries, ScoutingRecord
from .utils import display_user_name
from .views_support import (
    ACTING_PRODUCER_SESSION_KEY,
    ACTING_TECHNICIAN_SESSION_KEY,
    _accessible_technician_profiles,
    _accessible_producer_profiles,
    _acting_technician_profile,
    _acting_producer_profile,
    _effective_user,
    _filter_records,
    _is_technician,
    _manager_user,
    _latest_series_recommendation,
    _technician_visibility_q,
)


@login_required
def technician_records_view(request):
    if not _is_technician(request.user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')

    manager_user = _manager_user(request)
    producer_profiles = list(_accessible_producer_profiles(manager_user))
    technician_profiles = list(_accessible_technician_profiles(request.user)) if request.user.is_superuser else []
    active_technician_profile = _acting_technician_profile(request)
    active_control_profile = _acting_producer_profile(request)
    selected_producer = None
    selected_producer_id = request.GET.get('producer')

    if selected_producer_id:
        selected_producer = next(
            (profile for profile in producer_profiles if str(profile.user_id) == str(selected_producer_id)),
            None,
        )
        if selected_producer is None:
            messages.error(request, 'Le producteur sélectionné est introuvable ou hors périmètre.')

    producer_map_data = []
    producers_without_coordinates = []
    selected_producer_data = None

    for profile in producer_profiles:
        active_series = [series for series in profile.user.plant_series.all() if series.is_active]
        first_photo_series = next((series for series in active_series if series.photo), None)
        producer_name = profile.farm_name or display_user_name(profile.user)
        producer_data = {
            'id': profile.user_id,
            'name': producer_name,
            'display_name': display_user_name(profile.user),
            'username': profile.user.username,
            'address': profile.full_address,
            'photo_url': first_photo_series.photo.url if first_photo_series and first_photo_series.photo else '',
            'series_count': len(active_series),
            'target_url': f"{reverse('technician_records')}?producer={profile.user_id}",
            'is_selected': bool(selected_producer and selected_producer.user_id == profile.user_id),
            'is_controlled': bool(active_control_profile and active_control_profile.user_id == profile.user_id),
        }
        if profile.latitude is not None and profile.longitude is not None:
            producer_data['lat'] = float(profile.latitude)
            producer_data['lng'] = float(profile.longitude)
            producer_map_data.append(producer_data)
        else:
            producers_without_coordinates.append(producer_data)
        if selected_producer and selected_producer.user_id == profile.user_id:
            selected_producer_data = producer_data

    records = ScoutingRecord.objects.select_related(
        'user',
        'plant_series',
        'crop_ref',
        'conduct_type_ref',
        'variety_ref',
    ).prefetch_related(
        'leaf_observations',
        'recommendation_responses__dismiss_reason',
        'recommendation_responses__lever',
        'recommendation_responses__action',
    )
    actions = PlantAction.objects.select_related(
        'user',
        'action_type',
        'plant_series',
        'molecule',
        'auxiliary_taxon',
    )
    if not manager_user.is_superuser:
        visibility_query = _technician_visibility_q(manager_user)
        records = records.filter(visibility_query)
        actions = actions.filter(visibility_query)

    if selected_producer:
        records = list(records.filter(user=selected_producer.user))
        for record in records:
            record.recommendation = evaluate_record_recommendation(record)
        actions = actions.filter(user=selected_producer.user)
        selected_series = list(
            PlantSeries.objects.filter(user=selected_producer.user, is_active=True)
            .select_related('crop', 'conduct_type', 'variety')
            .prefetch_related(
                Prefetch(
                    'records',
                    queryset=ScoutingRecord.objects.select_related('crop_ref', 'plant_series').prefetch_related(
                        'leaf_observations',
                        'recommendation_responses__dismiss_reason',
                        'recommendation_responses__lever',
                        'recommendation_responses__action',
                    ),
                )
            )
            .order_by('name')
        )
        for series in selected_series:
            _latest_series_recommendation(series)
    else:
        records = records.none()
        actions = actions.none()
        selected_series = []

    actions = actions.order_by('-action_date', '-created_at')
    return render(
        request,
        'scouting/technician_records.html',
        {
            'records': records,
            'actions': actions,
            'producer_profiles': producer_profiles,
            'technician_profiles': technician_profiles,
            'active_technician_profile': active_technician_profile,
            'producer_map_data_json': json.dumps(producer_map_data),
            'mapped_producers_count': len(producer_map_data),
            'producers_without_coordinates': producers_without_coordinates,
            'selected_producer': selected_producer,
            'selected_producer_data': selected_producer_data,
            'selected_series': selected_series,
            'active_control_profile': active_control_profile,
        },
    )


@login_required
def producer_control_start_view(request, producer_id):
    if request.method != 'POST':
        return redirect('technician_records')
    manager_user = _manager_user(request)
    if not _is_technician(manager_user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')

    producer_profile = get_object_or_404(_accessible_producer_profiles(manager_user), user_id=producer_id)
    request.session[ACTING_PRODUCER_SESSION_KEY] = producer_profile.user_id
    request.session.modified = True
    if hasattr(request, '_acting_technician_profile_cache'):
        delattr(request, '_acting_technician_profile_cache')
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, f'Interface producteur active pour {producer_profile.farm_name or display_user_name(producer_profile.user)}.')
    return redirect('dashboard')


@login_required
def producer_control_stop_view(request):
    if request.method != 'POST':
        return redirect('technician_records')
    request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
    request.session.modified = True
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, 'Retour a la vue technicien.')
    return redirect('technician_records')


@login_required
def technician_control_start_view(request, technician_id):
    if request.method != 'POST':
        return redirect('technician_records')
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve au super-admin.')
        return redirect('dashboard')

    technician_profile = get_object_or_404(_accessible_technician_profiles(request.user), user_id=technician_id)
    request.session[ACTING_TECHNICIAN_SESSION_KEY] = technician_profile.user_id
    request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
    request.session.modified = True
    if hasattr(request, '_acting_technician_profile_cache'):
        delattr(request, '_acting_technician_profile_cache')
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, f'Interface technicien active pour {display_user_name(technician_profile.user)}.')
    return redirect('technician_records')


@login_required
def technician_control_stop_view(request):
    if request.method != 'POST':
        return redirect('technician_records')
    if not request.user.is_superuser:
        messages.error(request, 'Acces reserve au super-admin.')
        return redirect('dashboard')

    request.session.pop(ACTING_TECHNICIAN_SESSION_KEY, None)
    request.session.pop(ACTING_PRODUCER_SESSION_KEY, None)
    request.session.modified = True
    if hasattr(request, '_acting_technician_profile_cache'):
        delattr(request, '_acting_technician_profile_cache')
    if hasattr(request, '_acting_producer_profile_cache'):
        delattr(request, '_acting_producer_profile_cache')
    messages.success(request, 'Retour au compte super-admin.')
    return redirect('technician_records')


@login_required
def export_records_view(request):
    if Workbook is None:
        return HttpResponse('openpyxl manquant: installer openpyxl pour l export Excel.', status=500)

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    scope = request.GET.get('scope', 'me')
    if scope == 'all' and _is_technician(manager_user):
        qs = ScoutingRecord.objects.select_related(
            'user',
            'primary_aphid_species',
            'plant_series',
            'crop_ref',
            'conduct_type_ref',
            'variety_ref',
        ).prefetch_related('leaf_observations')
        if not manager_user.is_superuser:
            qs = qs.filter(_technician_visibility_q(manager_user))
    else:
        qs = (
            ScoutingRecord.objects.filter(user=effective_user)
            .select_related('user', 'primary_aphid_species', 'plant_series', 'crop_ref', 'conduct_type_ref', 'variety_ref')
            .prefetch_related('leaf_observations')
        )

    qs = _filter_records(request, qs)
    taxa = list(AuxiliaryTaxon.objects.order_by('display_order', 'name'))
    wb = Workbook()
    ws = wb.active
    ws.title = 'Comptages'
    header = [
        'Utilisateur',
        'Departement',
        'Serie',
        'Culture',
        'Conduite',
        'Variete',
        'Date saisie',
        'Annee',
        'Semaine',
        'Puceron principal',
        '% feuilles infestees',
        'Auxiliaires total',
        'Auxiliaires/plant',
        'Niveau risque',
        'Commentaire',
    ]
    header.extend([f'{taxon.name} (moy/plant)' for taxon in taxa])
    ws.append(header)
    for rec in qs:
        means = rec.species_means_per_plant()
        row = [
            display_user_name(rec.user),
            rec.department,
            rec.plant_series.name if rec.plant_series else '',
            rec.crop_ref.name if rec.crop_ref_id and rec.crop_ref else (rec.plant_series.crop.name if rec.plant_series_id and rec.plant_series else rec.crop),
            rec.conduct_type_ref.name if rec.conduct_type_ref else '',
            rec.variety_ref.name if rec.variety_ref else '',
            rec.scouting_date.isoformat(),
            rec.year,
            rec.week,
            str(rec.primary_aphid_species or ''),
            float(rec.aphid_infested_percent),
            rec.auxiliary_total,
            rec.auxiliaries_per_plant,
            rec.risk_level,
            rec.comment,
        ]
        row.extend([means.get(taxon.id, 0) for taxon in taxa])
        ws.append(row)

    content = BytesIO()
    wb.save(content)
    content.seek(0)
    response = HttpResponse(
        content.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="comptages_pucerons.xlsx"'
    return response


@login_required
def export_actions_view(request):
    if Workbook is None:
        return HttpResponse('openpyxl manquant: installer openpyxl pour l export Excel.', status=500)

    effective_user = _effective_user(request)
    manager_user = _manager_user(request)
    scope = request.GET.get('scope', 'me')

    if scope == 'all' and _is_technician(manager_user):
        qs = PlantAction.objects.select_related(
            'user',
            'user__profile',
            'action_type',
            'plant_series',
            'plant_series__crop',
            'crop_ref',
            'molecule',
            'auxiliary_taxon',
        )
        if not manager_user.is_superuser:
            qs = qs.filter(_technician_visibility_q(manager_user))
    else:
        qs = PlantAction.objects.filter(user=effective_user).select_related(
            'user',
            'user__profile',
            'action_type',
            'plant_series',
            'plant_series__crop',
            'crop_ref',
            'molecule',
            'auxiliary_taxon',
        )

    department = request.GET.get('department')
    technician = request.GET.get('technician')
    producer = request.GET.get('producer')
    crop = request.GET.get('crop')
    year = request.GET.get('year')
    series = request.GET.get('series')

    if department:
        qs = qs.filter(department=department)
    if technician:
        qs = qs.filter(user__profile__assigned_technician_id=technician)
    if producer:
        qs = qs.filter(user_id=producer)
    if crop:
        qs = qs.filter(Q(crop_ref_id=crop) | Q(plant_series__crop_id=crop))
    if year:
        qs = qs.filter(plant_series__year=year)
    if series:
        qs = qs.filter(plant_series_id=series)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Actions'
    ws.append(
        [
            'Utilisateur',
            'Département',
            'Série',
            'Culture',
            'Année',
            'Date action',
            'Type',
            'Portée',
            'Molécule',
            'Auxiliaire',
            'Détails',
        ]
    )

    for action in qs.order_by('-action_date', '-created_at'):
        crop_name = (
            action.crop_ref.name
            if action.crop_ref_id and action.crop_ref
            else (action.plant_series.crop.name if action.plant_series_id and action.plant_series and action.plant_series.crop_id else '')
        )
        ws.append(
            [
                display_user_name(action.user),
                action.department,
                action.plant_series.name if action.plant_series else '',
                crop_name,
                action.plant_series.year if action.plant_series else '',
                action.action_date.isoformat(),
                action.action_type.name if action.action_type else '',
                action.get_scope_display(),
                action.molecule.name if action.molecule else '',
                action.auxiliary_taxon.name if action.auxiliary_taxon else '',
                action.notes,
            ]
        )

    content = BytesIO()
    wb.save(content)
    content.seek(0)
    response = HttpResponse(
        content.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="actions_pucerons.xlsx"'
    return response
