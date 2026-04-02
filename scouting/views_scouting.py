import datetime
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .decision_engine import evaluate_record_recommendation
from .forms import PlantActionForm, ScoutingRecordForm
from .models import (
    ActionType,
    AuxiliaryTaxon,
    DecisionLever,
    LeafAuxiliaryObservation,
    LeafObservation,
    ScoutingRecord,
    UserProfile,
)
from .view_access import (
    _get_profile,
    _is_technician,
    _parse_count,
    _series_queryset_for_user,
    _target_user_for_series,
    _technician_visibility_q,
)
from .view_recommendation_support import (
    _build_initial_leaf_state,
    _dismiss_reasons_queryset,
    _mark_recommendation_followed,
    _recommendation_record_queryset_for_user,
)

@login_required
def record_create_view(request):
    taxa = list(AuxiliaryTaxon.objects.filter(is_active=True).order_by('display_order', 'name'))
    profile = _get_profile(request.user)
    is_tech_user = (not request.user.is_superuser) and (profile.role == UserProfile.ROLE_TECHNICIAN)
    series_qs = _series_queryset_for_user(request.user)
    recommendation_record = None
    recommendation_result = None
    recommendation_record_id = request.GET.get('recommendation_record') if request.method == 'GET' else None
    if recommendation_record_id:
        recommendation_record = _recommendation_record_queryset_for_user(request.user).filter(
            id=recommendation_record_id
        ).first()
        if recommendation_record:
            recommendation_result = evaluate_record_recommendation(recommendation_record)

    mode = request.GET.get('mode')
    selected_series = None
    if request.method == 'POST':
        post_data = request.POST.copy()
        form = ScoutingRecordForm(post_data, series_queryset=series_qs)
        if form.is_valid():
            if is_tech_user and not profile.department:
                form.add_error(None, 'Renseignez votre departement dans Mon profil avant de saisir un comptage.')
                return render(
                    request,
                    'scouting/record_select_series.html',
                    {
                        'series_list': series_qs,
                        'is_technician': is_tech_user,
                        'dismiss_reasons': _dismiss_reasons_queryset(),
                    },
                )
            record = form.save(commit=False)
            selected_series = form.cleaned_data['plant_series']
            if not selected_series:
                form.add_error('plant_series', 'Selectionnez une serie de plants.')
                return render(
                    request,
                    'scouting/record_select_series.html',
                    {
                        'series_list': series_qs,
                        'is_technician': is_tech_user,
                        'dismiss_reasons': _dismiss_reasons_queryset(),
                    },
                )
            owner_profile = _get_profile(selected_series.user)
            record.user = _target_user_for_series(request.user, selected_series, is_tech_user)
            record.department = owner_profile.department or profile.department
            record.crop = selected_series.crop.name
            record.crop_ref = selected_series.crop
            record.conduct_type_ref = selected_series.conduct_type
            record.variety_ref = selected_series.variety
            iso_date = record.scouting_date.isocalendar()
            record.year = iso_date.year
            record.week = iso_date.week
            record.auxiliary_mode = 'detailed'
            record.aphid_infested_percent = 0
            record.auxiliary_total = 0
            try:
                record.save()
            except IntegrityError:
                form.add_error(
                    None,
                    'Un comptage existe deja pour cette serie et cette semaine.',
                )
            else:
                plants_count = selected_series.plants_count or 10
                leaves_count = selected_series.leaves_per_plant or 3
                for plant in range(1, plants_count + 1):
                    for leaf_idx in range(1, leaves_count + 1):
                        leaf_position = f'leaf_{leaf_idx}'
                        prefix = f'p{plant}_{leaf_position}'
                        aphid_present = request.POST.get(f'{prefix}_aphid') == 'on'
                        leaf = LeafObservation.objects.create(
                            record=record,
                            plant_number=plant,
                            leaf_position=leaf_position,
                            leaf_index=leaf_idx,
                            aphid_present=aphid_present,
                        )
                        leaf_aux_rows = []
                        for taxon in taxa:
                            key = f'aux_{plant}_{leaf_position}_{taxon.id}'
                            count = _parse_count(request.POST.get(key))
                            if count > 0:
                                leaf_aux_rows.append(
                                    LeafAuxiliaryObservation(
                                        leaf_observation=leaf,
                                        taxon=taxon,
                                        count=count,
                                    )
                                )
                        if leaf_aux_rows:
                            LeafAuxiliaryObservation.objects.bulk_create(leaf_aux_rows)

                record.recompute_from_leaf_observations()
                messages.success(request, 'Comptage enregistre.')
                return redirect(f"{reverse('record_create')}?recommendation_record={record.id}")
    else:
        today = datetime.date.today()
        requested_series_id = request.GET.get('plant_series')
        series_initial = requested_series_id
        initial = {
            'scouting_date': today.isoformat(),
        }
        if series_initial:
            initial['plant_series'] = series_initial
        form = ScoutingRecordForm(initial=initial, series_queryset=series_qs)
        selected_series = series_qs.filter(id=series_initial).first() if series_initial else None

    if selected_series is None:
        selected_series_id = request.POST.get('plant_series') if request.method == 'POST' else request.GET.get('plant_series')
        if selected_series_id:
            selected_series = series_qs.filter(id=selected_series_id).first()

    if selected_series and request.method == 'GET':
        if mode == 'action':
            return redirect(f"{reverse('action_create')}?plant_series={selected_series.id}")
        if mode != 'count':
            return render(
                request,
                'scouting/record_choose_mode.html',
                {
                    'selected_series': selected_series,
                    'is_technician': is_tech_user,
                    'target_user': selected_series.user,
                },
            )

    plants = []
    leaf_positions = []
    if selected_series:
        plants_count = selected_series.plants_count or 10
        leaves_count = selected_series.leaves_per_plant or 3
        plants = list(range(1, plants_count + 1))
        labels = {1: 'Basse', 2: 'Milieu', 3: 'Haute'}
        for idx in range(1, leaves_count + 1):
            leaf_positions.append((f'leaf_{idx}', labels.get(idx, f'Feuille {idx}')))
    else:
        return render(
            request,
            'scouting/record_select_series.html',
            {
                'series_list': series_qs,
                'is_technician': is_tech_user,
                'recommendation_record': recommendation_record,
                'recommendation_result': recommendation_result,
                'dismiss_reasons': _dismiss_reasons_queryset(),
            },
        )

    return render(
        request,
        'scouting/record_form.html',
        {
            'form': form,
            'plants': plants,
            'leaf_positions': leaf_positions,
            'auxiliary_taxa': taxa,
            'selected_series': selected_series,
            'is_technician': is_tech_user,
            'target_user': selected_series.user,
            'record_obj': None,
            'initial_leaf_data_json': json.dumps({}),
            'form_mode': 'create',
        },
    )


@login_required
def action_create_view(request):
    profile = _get_profile(request.user)
    is_tech_user = (not request.user.is_superuser) and (profile.role == UserProfile.ROLE_TECHNICIAN)
    series_qs = _series_queryset_for_user(request.user)
    selected_series_id = request.POST.get('plant_series') if request.method == 'POST' else request.GET.get('plant_series')
    selected_series = series_qs.filter(id=selected_series_id).first() if selected_series_id else None
    lever_id = request.POST.get('decision_lever') if request.method == 'POST' else request.GET.get('lever')
    recommendation_record_id = (
        request.POST.get('recommendation_record') if request.method == 'POST' else request.GET.get('recommendation_record')
    )

    if selected_series is None:
        return render(
            request,
            'scouting/record_select_series.html',
            {
                'series_list': series_qs,
                'is_technician': is_tech_user,
                'dismiss_reasons': _dismiss_reasons_queryset(),
            },
        )

    selected_lever = None
    recommendation_record = None
    if lever_id:
        selected_lever = (
            DecisionLever.objects.select_related('rule', 'action_type', 'molecule', 'auxiliary_taxon')
            .filter(id=lever_id, is_active=True, rule__is_active=True, rule__crop=selected_series.crop)
            .first()
        )
        if selected_lever is None and request.method == 'GET':
            messages.warning(request, 'Le levier selectionne est introuvable pour cette culture.')
    if recommendation_record_id:
        recommendation_record = _recommendation_record_queryset_for_user(request.user).filter(
            id=recommendation_record_id,
            plant_series=selected_series,
        ).first()

    if request.method == 'POST':
        post_data = request.POST.copy()
        post_data['plant_series'] = str(selected_series.id)
        form = PlantActionForm(
            post_data,
            series_queryset=series_qs,
            selected_series=selected_series,
        )
        if form.is_valid():
            action = form.save(commit=False)
            owner_profile = _get_profile(selected_series.user)
            action.user = _target_user_for_series(request.user, selected_series, is_tech_user)
            action.entered_by = request.user
            action.plant_series = selected_series
            action.department = owner_profile.department or profile.department
            action.crop_ref = selected_series.crop
            action.conduct_type_ref = selected_series.conduct_type
            action.variety_ref = selected_series.variety
            action.decision_lever = selected_lever
            action.save()
            if recommendation_record and selected_lever:
                _mark_recommendation_followed(recommendation_record, selected_lever, action, request.user)
            messages.success(request, 'Action enregistree.')
            return redirect('record_create')
    else:
        initial = {'plant_series': selected_series.id, 'action_date': datetime.date.today().isoformat()}
        if selected_lever:
            initial.update(
                {
                    'action_type': selected_lever.action_type_id,
                    'scope': selected_lever.scope,
                    'molecule': selected_lever.molecule_id,
                    'auxiliary_taxon': selected_lever.auxiliary_taxon_id,
                    'notes': selected_lever.notes_template,
                }
            )
        form = PlantActionForm(
            initial=initial,
            series_queryset=series_qs,
            selected_series=selected_series,
        )

    action_types = list(ActionType.objects.filter(is_active=True).values('id', 'category'))
    return render(
        request,
        'scouting/action_form.html',
        {
            'form': form,
            'selected_series': selected_series,
            'is_technician': is_tech_user,
            'target_user': selected_series.user,
            'action_types': action_types,
            'selected_lever': selected_lever,
            'recommendation_record': recommendation_record,
        },
    )



@login_required
def record_update_view(request, record_id):
    queryset = ScoutingRecord.objects.select_related('plant_series', 'user')
    profile = _get_profile(request.user)
    editor_is_technician = (not request.user.is_superuser) and (profile.role == UserProfile.ROLE_TECHNICIAN)
    if request.user.is_superuser:
        record = get_object_or_404(queryset, id=record_id)
    elif editor_is_technician:
        record = get_object_or_404(queryset.filter(_technician_visibility_q(request.user)), id=record_id)
    else:
        record = get_object_or_404(queryset, id=record_id, user=request.user)
    if not record.plant_series:
        messages.error(request, 'Cette saisie ne peut pas etre modifiee (serie manquante).')
        if editor_is_technician:
            return redirect('technician_records')
        return redirect('my_records')

    taxa = list(AuxiliaryTaxon.objects.filter(is_active=True).order_by('display_order', 'name'))
    series_qs = _series_queryset_for_user(request.user)
    selected_series = record.plant_series
    if request.method == 'POST':
        post_data = request.POST.copy()
        post_data['plant_series'] = str(selected_series.id)
        form = ScoutingRecordForm(post_data, instance=record, series_queryset=series_qs)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.plant_series = selected_series
            updated.user = record.user
            updated.department = record.department
            updated.crop = selected_series.crop.name
            updated.crop_ref = selected_series.crop
            updated.conduct_type_ref = selected_series.conduct_type
            updated.variety_ref = selected_series.variety
            iso_date = updated.scouting_date.isocalendar()
            updated.year = iso_date.year
            updated.week = iso_date.week
            updated.auxiliary_mode = 'detailed'
            updated.aphid_infested_percent = 0
            updated.auxiliary_total = 0
            try:
                updated.save()
            except IntegrityError:
                form.add_error(None, 'Une autre saisie existe deja pour cette serie et cette semaine.')
            else:
                updated.leaf_observations.all().delete()
                plants_count = selected_series.plants_count or 10
                leaves_count = selected_series.leaves_per_plant or 3
                for plant in range(1, plants_count + 1):
                    for leaf_idx in range(1, leaves_count + 1):
                        leaf_position = f'leaf_{leaf_idx}'
                        prefix = f'p{plant}_{leaf_position}'
                        aphid_present = request.POST.get(f'{prefix}_aphid') == 'on'
                        leaf = LeafObservation.objects.create(
                            record=updated,
                            plant_number=plant,
                            leaf_position=leaf_position,
                            leaf_index=leaf_idx,
                            aphid_present=aphid_present,
                        )
                        leaf_aux_rows = []
                        for taxon in taxa:
                            key = f'aux_{plant}_{leaf_position}_{taxon.id}'
                            count = _parse_count(request.POST.get(key))
                            if count > 0:
                                leaf_aux_rows.append(
                                    LeafAuxiliaryObservation(
                                        leaf_observation=leaf,
                                        taxon=taxon,
                                        count=count,
                                    )
                                )
                        if leaf_aux_rows:
                            LeafAuxiliaryObservation.objects.bulk_create(leaf_aux_rows)
                updated.recompute_from_leaf_observations()
                messages.success(request, 'Saisie modifiee.')
                next_view = request.POST.get('next') or request.GET.get('next')
                next_producer_id = request.POST.get('producer') or request.GET.get('producer')
                if next_view == 'technician_records' and _is_technician(request.user):
                    redirect_url = reverse('technician_records')
                    if next_producer_id:
                        redirect_url = f'{redirect_url}?producer={next_producer_id}'
                    return redirect(redirect_url)
                return redirect('my_records')
    else:
        form = ScoutingRecordForm(instance=record, series_queryset=series_qs)

    plants_count = selected_series.plants_count or 10
    leaves_count = selected_series.leaves_per_plant or 3
    plants = list(range(1, plants_count + 1))
    labels = {1: 'Basse', 2: 'Milieu', 3: 'Haute'}
    leaf_positions = [(f'leaf_{idx}', labels.get(idx, f'Feuille {idx}')) for idx in range(1, leaves_count + 1)]

    return render(
        request,
        'scouting/record_form.html',
        {
            'form': form,
            'plants': plants,
            'leaf_positions': leaf_positions,
            'auxiliary_taxa': taxa,
            'selected_series': selected_series,
            'is_technician': editor_is_technician or (request.user.id != selected_series.user_id),
            'target_user': selected_series.user,
            'record_obj': record,
            'initial_leaf_data_json': json.dumps(_build_initial_leaf_state(record)),
            'form_mode': 'edit',
        },
    )


