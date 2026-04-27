import datetime
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .decision_engine import evaluate_record_recommendation
from .forms import PlantActionForm, QuickScoutingRecordForm, ScoutingRecordForm
from .models import (
    ActionType,
    AphidSpecies,
    AuxiliaryTaxon,
    DecisionLever,
    LeafAuxiliaryObservation,
    LeafOtherPestObservation,
    LeafObservation,
    OtherPestTaxon,
    PlantAction,
    QuickRecordAphidSpecies,
    QuickRecordAuxiliaryCount,
    QuickRecordOtherPestCount,
    ScoutingRecord,
    UserProfile,
)
from .view_access import (
    _effective_access_restriction,
    _effective_profile,
    _effective_user,
    _get_profile,
    _manager_user,
    _is_acting_as_producer,
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
    _sanitize_next_url,
)


def _leaf_positions_for_series(selected_series):
    leaves_count = selected_series.leaves_per_plant or 3
    labels = {1: 'Basse', 2: 'Milieu', 3: 'Haute'}
    return [(f'leaf_{idx}', labels.get(idx, f'Feuille {idx}')) for idx in range(1, leaves_count + 1)]


def _active_aphid_species():
    return list(
        AphidSpecies.objects.filter(is_active=True)
        .prefetch_related('molecules', 'auxiliary_taxa')
        .order_by('display_order', 'vernacular_name', 'latin_name')
    )


def _default_aphid_species(aphid_species_list):
    for species in aphid_species_list:
        if species.code == 'non-determine':
            return species
    return aphid_species_list[0] if aphid_species_list else None


def _serialize_aphid_species(aphid_species_list):
    payload = []
    for species in aphid_species_list:
        molecules = ', '.join(species.molecules.all().values_list('name', flat=True))
        auxiliaries = ', '.join(species.auxiliary_taxa.all().values_list('name', flat=True))
        payload.append(
            {
                'id': species.id,
                'label': str(species),
                'vernacularName': species.vernacular_name,
                'latinName': species.latin_name,
                'photoUrl': species.photo.url if species.photo else '',
                'moleculesLabel': molecules or 'Aucune molecule renseignee.',
                'auxiliariesLabel': auxiliaries or 'Aucun auxiliaire renseigne.',
                'description': species.description or '',
            }
        )
    return payload


def _serialize_auxiliary_taxa(taxa):
    payload = []
    for taxon in taxa:
        payload.append(
            {
                'id': taxon.id,
                'label': taxon.name,
                'photoUrl': taxon.photo.url if taxon.photo else '',
            }
        )
    return payload


def _serialize_other_pest_taxa(taxa):
    payload = []
    for taxon in taxa:
        payload.append(
            {
                'id': taxon.id,
                'label': taxon.name,
                'photoUrl': taxon.photo.url if taxon.photo else '',
            }
        )
    return payload


def _active_other_pest_taxa():
    return list(OtherPestTaxon.objects.filter(is_active=True).order_by('display_order', 'name'))


def _extract_quick_species_data(post_data, species_lookup, default_aphid_species, aphid_infested_leaves_count):
    observed_species_ids = []
    for key in post_data.keys():
        if not key.startswith('quick_aphid_species_'):
            continue
        if post_data.get(key) != '1':
            continue
        try:
            observed_species_ids.append(int(key.rsplit('_', 1)[-1]))
        except (TypeError, ValueError):
            continue

    observed_species_ids = [species_id for species_id in observed_species_ids if species_id in species_lookup]

    if aphid_infested_leaves_count > 0 and not observed_species_ids and default_aphid_species:
        observed_species_ids = [default_aphid_species.id]

    raw_primary_species_id = post_data.get('primary_aphid_species')
    try:
        primary_species_id = int(raw_primary_species_id) if raw_primary_species_id else None
    except (TypeError, ValueError):
        primary_species_id = None

    primary_species = species_lookup.get(primary_species_id)
    if aphid_infested_leaves_count <= 0:
        observed_species_ids = []
        primary_species = None
    elif len(observed_species_ids) == 1:
        primary_species = species_lookup.get(observed_species_ids[0])
    elif primary_species_id not in observed_species_ids:
        primary_species = None

    return observed_species_ids, primary_species


def _extract_quick_auxiliary_counts(post_data, taxa):
    counts = {}
    for taxon in taxa:
        count = _parse_count(post_data.get(f'quick_aux_{taxon.id}'))
        if count > 0:
            counts[taxon.id] = count
    return counts


def _extract_quick_other_pest_counts(post_data, other_pest_taxa, observed_leaves_count):
    counts = {}
    errors = {}
    for taxon in other_pest_taxa:
        if post_data.get(f'quick_pest_selected_{taxon.id}') != '1':
            continue
        infested_leaves_count = _parse_count(post_data.get(f'quick_pest_{taxon.id}'))
        if infested_leaves_count > observed_leaves_count:
            errors[str(taxon.id)] = (
                "Le nombre de feuilles infestées ne peut pas dépasser les feuilles observées."
            )
        counts[taxon.id] = infested_leaves_count
    return counts, errors


def _save_quick_record_aggregates(record, aphid_species_ids, auxiliary_counts, other_pest_counts):
    record.leaf_observations.all().delete()
    record.quick_aphid_species.all().delete()
    record.quick_auxiliary_counts.all().delete()
    record.quick_other_pest_counts.all().delete()

    QuickRecordAphidSpecies.objects.bulk_create(
        [QuickRecordAphidSpecies(record=record, species_id=species_id) for species_id in aphid_species_ids]
    )
    QuickRecordAuxiliaryCount.objects.bulk_create(
        [
            QuickRecordAuxiliaryCount(record=record, taxon_id=taxon_id, count=count)
            for taxon_id, count in auxiliary_counts.items()
        ]
    )
    QuickRecordOtherPestCount.objects.bulk_create(
        [
            QuickRecordOtherPestCount(
                record=record,
                taxon_id=taxon_id,
                infested_leaves_count=infested_leaves_count,
            )
            for taxon_id, infested_leaves_count in other_pest_counts.items()
        ]
    )


def _build_quick_initial_data(record, taxa, other_pest_taxa):
    auxiliary_counts = {}
    other_pest_counts = {}
    aphid_species_ids = []
    primary_aphid_species_id = ''

    if record:
        auxiliary_counts = {
            str(row.taxon_id): row.count
            for row in record.quick_auxiliary_counts.select_related('taxon').all()
            if row.count > 0
        }
        other_pest_counts = {
            str(row.taxon_id): row.infested_leaves_count
            for row in record.quick_other_pest_counts.select_related('taxon').all()
            if row.infested_leaves_count >= 0
        }
        aphid_species_ids = [str(species_id) for species_id in record.quick_aphid_species.values_list('species_id', flat=True)]
        primary_aphid_species_id = str(record.primary_aphid_species_id or '')

    return {
        'auxiliaryCounts': auxiliary_counts,
        'otherPestCounts': other_pest_counts,
        'aphidSpeciesIds': aphid_species_ids,
        'primaryAphidSpecies': primary_aphid_species_id,
    }


def _build_quick_initial_data_from_post(post_data, taxa, other_pest_taxa):
    aphid_species_ids = [
        key.rsplit('_', 1)[-1]
        for key, value in post_data.items()
        if key.startswith('quick_aphid_species_') and value == '1'
    ]
    auxiliary_counts = {
        str(taxon.id): _parse_count(post_data.get(f'quick_aux_{taxon.id}'))
        for taxon in taxa
        if _parse_count(post_data.get(f'quick_aux_{taxon.id}')) > 0
    }
    other_pest_counts = {
        str(taxon.id): _parse_count(post_data.get(f'quick_pest_{taxon.id}'))
        for taxon in other_pest_taxa
        if post_data.get(f'quick_pest_selected_{taxon.id}') == '1'
    }
    return {
        'auxiliaryCounts': auxiliary_counts,
        'otherPestCounts': other_pest_counts,
        'aphidSpeciesIds': aphid_species_ids,
        'primaryAphidSpecies': str(post_data.get('primary_aphid_species') or ''),
    }


def _quick_record_form_context(
    *,
    form,
    selected_series,
    is_technician,
    target_user,
    record_obj,
    form_mode,
    aphid_species_list,
    default_aphid_species,
    taxa,
    other_pest_taxa,
    quick_initial_data,
):
    return {
        'form': form,
        'selected_series': selected_series,
        'is_technician': is_technician,
        'target_user': target_user,
        'record_obj': record_obj,
        'form_mode': form_mode,
        'hide_mobile_record_cta': True,
        'aphid_species_options': _serialize_aphid_species(aphid_species_list),
        'default_aphid_species_id': default_aphid_species.id if default_aphid_species else '',
        'auxiliary_taxa_options': _serialize_auxiliary_taxa(taxa),
        'other_pest_taxa_options': _serialize_other_pest_taxa(other_pest_taxa),
        'quick_initial_data_json': json.dumps(quick_initial_data),
    }


def _save_quick_record_from_form(
    *,
    form,
    record,
    request,
    selected_series,
    taxa,
    other_pest_taxa,
    aphid_species_lookup,
    default_aphid_species,
):
    observed_plants_count = form.cleaned_data['observed_plants_count']
    observed_leaves_count = form.cleaned_data['observed_leaves_count']
    aphid_infested_leaves_count = form.cleaned_data['aphid_infested_leaves_count']
    observed_species_ids, primary_species = _extract_quick_species_data(
        request.POST,
        aphid_species_lookup,
        default_aphid_species,
        aphid_infested_leaves_count,
    )
    auxiliary_counts = _extract_quick_auxiliary_counts(request.POST, taxa)
    other_pest_counts, pest_errors = _extract_quick_other_pest_counts(
        request.POST,
        other_pest_taxa,
        observed_leaves_count,
    )

    if len(observed_species_ids) > 1 and primary_species is None:
        form.add_error(
            None,
            "Plusieurs espèces de pucerons ont été renseignées. Choisissez l'espèce principale du comptage.",
        )
    for taxon in other_pest_taxa:
        error = pest_errors.get(str(taxon.id))
        if error:
            form.add_error(None, f'{taxon.name} : {error}')

    if form.errors:
        return None

    record.plant_series = selected_series
    record.crop = selected_series.crop.name
    record.crop_ref = selected_series.crop
    record.conduct_type_ref = selected_series.conduct_type
    record.variety_ref = selected_series.variety
    iso_date = record.scouting_date.isocalendar()
    record.year = iso_date.year
    record.week = iso_date.week
    record.entry_mode = 'quick'
    record.auxiliary_mode = 'quick'
    record.observed_plants_count = observed_plants_count
    record.observed_leaves_count = observed_leaves_count
    record.aphid_infested_leaves_count = aphid_infested_leaves_count
    record.aphid_infested_percent = round((aphid_infested_leaves_count / observed_leaves_count) * 100, 2)
    record.auxiliary_total = sum(auxiliary_counts.values())
    record.primary_aphid_species = primary_species

    try:
        with transaction.atomic():
            record.save()
            _save_quick_record_aggregates(record, observed_species_ids, auxiliary_counts, other_pest_counts)
    except IntegrityError:
        form.add_error(None, 'Une autre saisie existe déjà pour cette série et cette semaine.')
        return None

    return record


def _build_leaf_state_from_post(post_data, plants_count, leaves_count, taxa, other_pest_taxa):
    try:
        current_plant = int(post_data.get('current_plant') or 1)
    except (TypeError, ValueError):
        current_plant = 1
    current_plant = min(max(current_plant, 1), max(plants_count, 1))
    data = {
        'aphids': {},
        'auxData': {},
        'pestData': {},
        'plantSpecies': {},
        'plantSpeciesTouched': {},
        'primaryAphidSpecies': str(post_data.get('primary_aphid_species') or ''),
        'currentPlant': current_plant,
        'inFinalStep': post_data.get('form_step') == 'final',
    }
    for plant in range(1, plants_count + 1):
        plant_key = str(plant)
        posted_species = post_data.get(f'plant_{plant}_aphid_species')
        if posted_species:
            data['plantSpecies'][plant_key] = str(posted_species)
        data['plantSpeciesTouched'][plant_key] = post_data.get(f'plant_{plant}_aphid_species_touched') == '1'
        for leaf_idx in range(1, leaves_count + 1):
            leaf_position = f'leaf_{leaf_idx}'
            prefix = f'p{plant}_{leaf_position}'
            aphid_name = f'{prefix}_aphid'
            data['aphids'][aphid_name] = post_data.get(aphid_name) == 'on'
            leaf_key = f'{plant}-{leaf_position}'
            data['auxData'][leaf_key] = {}
            data['pestData'][leaf_key] = {}
            for taxon in taxa:
                count = _parse_count(post_data.get(f'aux_{plant}_{leaf_position}_{taxon.id}'))
                if count <= 0:
                    continue
                taxon_id = str(taxon.id)
                data['auxData'][leaf_key][taxon_id] = {
                    'taxonId': taxon_id,
                    'name': taxon.name,
                    'count': count,
                }
            for taxon in other_pest_taxa:
                if post_data.get(f'pest_{plant}_{leaf_position}_{taxon.id}') != '1':
                    continue
                taxon_id = str(taxon.id)
                data['pestData'][leaf_key][taxon_id] = {
                    'taxonId': taxon_id,
                    'name': taxon.name,
                }
    return data


def _extract_record_species_data(post_data, plants_count, leaves_count, species_lookup, default_aphid_species):
    plant_species_map = {}
    observed_species_ids = set()
    default_species_id = default_aphid_species.id if default_aphid_species else None

    for plant in range(1, plants_count + 1):
        raw_species_id = post_data.get(f'plant_{plant}_aphid_species') or default_species_id
        try:
            species_id = int(raw_species_id) if raw_species_id else None
        except (TypeError, ValueError):
            species_id = default_species_id
        species_obj = species_lookup.get(species_id)
        plant_species_map[plant] = species_obj
        plant_has_aphids = any(
            post_data.get(f'p{plant}_leaf_{leaf_idx}_aphid') == 'on'
            for leaf_idx in range(1, leaves_count + 1)
        )
        if plant_has_aphids and species_obj:
            observed_species_ids.add(species_obj.id)

    raw_primary_species_id = post_data.get('primary_aphid_species')
    try:
        primary_species_id = int(raw_primary_species_id) if raw_primary_species_id else None
    except (TypeError, ValueError):
        primary_species_id = None

    primary_species = species_lookup.get(primary_species_id)
    if len(observed_species_ids) == 1:
        primary_species = species_lookup.get(next(iter(observed_species_ids)))
    elif primary_species_id and primary_species_id not in observed_species_ids:
        primary_species = None
    elif not observed_species_ids:
        primary_species = None

    return plant_species_map, observed_species_ids, primary_species


def _write_leaf_observations(record, post_data, plants_count, leaves_count, taxa, other_pest_taxa, plant_species_map):
    record.leaf_observations.all().delete()
    for plant in range(1, plants_count + 1):
        plant_species = plant_species_map.get(plant)
        for leaf_idx in range(1, leaves_count + 1):
            leaf_position = f'leaf_{leaf_idx}'
            prefix = f'p{plant}_{leaf_position}'
            aphid_present = post_data.get(f'{prefix}_aphid') == 'on'
            leaf = LeafObservation.objects.create(
                record=record,
                plant_number=plant,
                leaf_position=leaf_position,
                leaf_index=leaf_idx,
                aphid_present=aphid_present,
                aphid_species=plant_species if aphid_present else None,
            )
            leaf_aux_rows = []
            leaf_pest_rows = []
            for taxon in taxa:
                key = f'aux_{plant}_{leaf_position}_{taxon.id}'
                count = _parse_count(post_data.get(key))
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
            for taxon in other_pest_taxa:
                if post_data.get(f'pest_{plant}_{leaf_position}_{taxon.id}') != '1':
                    continue
                leaf_pest_rows.append(
                    LeafOtherPestObservation(
                        leaf_observation=leaf,
                        taxon=taxon,
                    )
                )
            if leaf_pest_rows:
                LeafOtherPestObservation.objects.bulk_create(leaf_pest_rows)


def _record_form_context(
    *,
    form,
    plants,
    leaf_positions,
    taxa,
    other_pest_taxa,
    selected_series,
    is_technician,
    target_user,
    record_obj,
    initial_leaf_data,
    form_mode,
    aphid_species_list,
    default_aphid_species,
):
    return {
        'form': form,
        'plants': plants,
        'leaf_positions': leaf_positions,
        'auxiliary_taxa': taxa,
        'other_pest_taxa': other_pest_taxa,
        'selected_series': selected_series,
        'is_technician': is_technician,
        'target_user': target_user,
        'record_obj': record_obj,
        'initial_leaf_data_json': json.dumps(initial_leaf_data),
        'form_mode': form_mode,
        'hide_mobile_record_cta': True,
        'aphid_species_options': _serialize_aphid_species(aphid_species_list),
        'default_aphid_species_id': default_aphid_species.id if default_aphid_species else '',
        'default_aphid_species_label': str(default_aphid_species) if default_aphid_species else 'Non determine',
    }


@login_required
def record_create_view(request):
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('dashboard')

    taxa = list(AuxiliaryTaxon.objects.filter(is_active=True).order_by('display_order', 'name'))
    other_pest_taxa = _active_other_pest_taxa()
    aphid_species_list = _active_aphid_species()
    default_aphid_species = _default_aphid_species(aphid_species_list)
    aphid_species_lookup = {species.id: species for species in aphid_species_list}

    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    effective_profile = _effective_profile(request)
    effective_user = _effective_user(request)
    acting_as_producer = _is_acting_as_producer(request)
    is_tech_user = (
        (not acting_as_producer)
        and (manager_profile.role == UserProfile.ROLE_TECHNICIAN)
        and not acting_as_producer
    )
    series_qs = _series_queryset_for_user(effective_user if acting_as_producer else manager_user)
    recommendation_record = None
    recommendation_result = None
    recommendation_record_id = request.GET.get('recommendation_record') if request.method == 'GET' else None
    if recommendation_record_id:
        recommendation_record = _recommendation_record_queryset_for_user(effective_user).filter(
            id=recommendation_record_id
        ).first()
        if recommendation_record:
            recommendation_result = evaluate_record_recommendation(recommendation_record)

    mode = request.GET.get('mode')
    selected_series = None
    entry_mode = 'detailed'

    if request.method == 'POST':
        post_data = request.POST.copy()
        entry_mode = post_data.get('record_entry_mode') or 'detailed'
        form = (
            QuickScoutingRecordForm(post_data, series_queryset=series_qs)
            if entry_mode == 'quick'
            else ScoutingRecordForm(post_data, series_queryset=series_qs)
        )
        if form.is_valid():
            record = form.save(commit=False)
            selected_series = form.cleaned_data['plant_series']
            if not selected_series:
                form.add_error('plant_series', 'S?lectionnez une s?rie de plants.')
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
            record.user = selected_series.user if acting_as_producer else _target_user_for_series(
                manager_user,
                selected_series,
                is_tech_user,
            )
            record.department = owner_profile.department or effective_profile.department or manager_profile.department

            if entry_mode == 'quick':
                record = _save_quick_record_from_form(
                    form=form,
                    record=record,
                    request=request,
                    selected_series=selected_series,
                    taxa=taxa,
                    other_pest_taxa=other_pest_taxa,
                    aphid_species_lookup=aphid_species_lookup,
                    default_aphid_species=default_aphid_species,
                )
                if record is not None:
                    messages.success(request, 'Comptage rapide enregistr?.')
                    return redirect(f"{reverse('record_create')}?recommendation_record={record.id}")
            else:
                record.crop = selected_series.crop.name
                record.crop_ref = selected_series.crop
                record.conduct_type_ref = selected_series.conduct_type
                record.variety_ref = selected_series.variety
                iso_date = record.scouting_date.isocalendar()
                record.year = iso_date.year
                record.week = iso_date.week
                record.entry_mode = 'detailed'
                record.auxiliary_mode = 'detailed'
                record.aphid_infested_percent = 0
                record.auxiliary_total = 0

                plants_count = selected_series.plants_count or 10
                leaves_count = selected_series.leaves_per_plant or 3
                plant_species_map, observed_species_ids, primary_species = _extract_record_species_data(
                    request.POST,
                    plants_count,
                    leaves_count,
                    aphid_species_lookup,
                    default_aphid_species,
                )
                if len(observed_species_ids) > 1 and primary_species is None:
                    form.add_error(
                        None,
                        "Plusieurs esp?ces de pucerons ont ?t? renseign?es. Choisissez l'esp?ce principale ? la fin du comptage.",
                    )
                if not form.errors:
                    try:
                        with transaction.atomic():
                            record.save()
                            _write_leaf_observations(
                                record,
                                request.POST,
                                plants_count,
                                leaves_count,
                                taxa,
                                other_pest_taxa,
                                plant_species_map,
                            )
                            record.recompute_from_leaf_observations()
                            record.primary_aphid_species = primary_species
                            record.save(update_fields=['primary_aphid_species'])
                    except IntegrityError:
                        form.add_error(None, 'Un comptage existe d?j? pour cette s?rie et cette semaine.')
                    else:
                        messages.success(request, 'Comptage enregistr?.')
                        return redirect(f"{reverse('record_create')}?recommendation_record={record.id}")
    else:
        today = datetime.date.today()
        requested_series_id = request.GET.get('plant_series')
        selected_series = series_qs.filter(id=requested_series_id).first() if requested_series_id else None
        initial = {'scouting_date': today.isoformat()}
        if requested_series_id:
            initial['plant_series'] = requested_series_id
        if mode == 'quick' and selected_series is not None:
            initial.update(
                {
                    'observed_plants_count': selected_series.plants_count or 10,
                    'observed_leaves_count': (selected_series.plants_count or 10) * (selected_series.leaves_per_plant or 3),
                    'aphid_infested_leaves_count': 0,
                }
            )
            form = QuickScoutingRecordForm(initial=initial, series_queryset=series_qs)
            entry_mode = 'quick'
        else:
            form = ScoutingRecordForm(initial=initial, series_queryset=series_qs)

    if selected_series is None:
        selected_series_id = request.POST.get('plant_series') if request.method == 'POST' else request.GET.get('plant_series')
        if selected_series_id:
            selected_series = series_qs.filter(id=selected_series_id).first()

    if selected_series and request.method == 'GET':
        if mode == 'action':
            return redirect(f"{reverse('action_create')}?plant_series={selected_series.id}")
        if mode not in {'count', 'quick'}:
            return render(
                request,
                'scouting/record_choose_mode.html',
                {
                    'selected_series': selected_series,
                    'is_technician': is_tech_user,
                    'target_user': selected_series.user,
                },
            )

    if not selected_series:
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

    if mode == 'quick' or entry_mode == 'quick':
        return render(
            request,
            'scouting/record_quick_form.html',
            _quick_record_form_context(
                form=form,
                selected_series=selected_series,
                is_technician=is_tech_user,
                target_user=selected_series.user,
                record_obj=None,
                form_mode='create',
                aphid_species_list=aphid_species_list,
                default_aphid_species=default_aphid_species,
                taxa=taxa,
                other_pest_taxa=other_pest_taxa,
                quick_initial_data=(
                    _build_quick_initial_data_from_post(request.POST, taxa, other_pest_taxa)
                    if request.method == 'POST'
                    else _build_quick_initial_data(None, taxa, other_pest_taxa)
                ),
            ),
        )

    plants_count = selected_series.plants_count or 10
    leaves_count = selected_series.leaves_per_plant or 3
    plants = list(range(1, plants_count + 1))
    leaf_positions = _leaf_positions_for_series(selected_series)
    initial_leaf_data = {}
    if request.method == 'POST':
        initial_leaf_data = _build_leaf_state_from_post(
            request.POST,
            plants_count,
            leaves_count,
            taxa,
            other_pest_taxa,
        )

    return render(
        request,
        'scouting/record_form.html',
        _record_form_context(
            form=form,
            plants=plants,
            leaf_positions=leaf_positions,
            taxa=taxa,
            other_pest_taxa=other_pest_taxa,
            selected_series=selected_series,
            is_technician=is_tech_user,
            target_user=selected_series.user,
            record_obj=None,
            initial_leaf_data=initial_leaf_data,
            form_mode='create',
            aphid_species_list=aphid_species_list,
            default_aphid_species=default_aphid_species,
        ),
    )


@login_required
def action_create_view(request):
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('dashboard')

    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    effective_profile = _effective_profile(request)
    effective_user = _effective_user(request)
    acting_as_producer = _is_acting_as_producer(request)
    is_tech_user = (
        (not acting_as_producer)
        and (manager_profile.role == UserProfile.ROLE_TECHNICIAN)
        and not acting_as_producer
    )
    series_qs = _series_queryset_for_user(effective_user if acting_as_producer else manager_user)
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
            messages.warning(request, 'Le levier sélectionné est introuvable pour cette culture.')
    if recommendation_record_id:
        recommendation_record = _recommendation_record_queryset_for_user(effective_user).filter(
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
            action.user = selected_series.user if acting_as_producer else _target_user_for_series(
                manager_user,
                selected_series,
                is_tech_user,
            )
            action.entered_by = request.user
            action.plant_series = selected_series
            action.department = owner_profile.department or effective_profile.department or manager_profile.department
            action.crop_ref = selected_series.crop
            action.conduct_type_ref = selected_series.conduct_type
            action.variety_ref = selected_series.variety
            action.decision_lever = selected_lever
            action.save()
            if recommendation_record and selected_lever:
                _mark_recommendation_followed(recommendation_record, selected_lever, action, request.user)
            messages.success(request, 'Action enregistrée.')
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
            'form_mode': 'create',
            'next_url': reverse('record_create') + f'?plant_series={selected_series.id}',
        },
    )


@login_required
def action_update_view(request, action_id):
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('my_records')

    queryset = PlantAction.objects.select_related('plant_series', 'user', 'decision_lever')
    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    effective_user = _effective_user(request)
    acting_as_producer = _is_acting_as_producer(request)
    editor_is_technician = (not acting_as_producer) and (manager_profile.role == UserProfile.ROLE_TECHNICIAN)

    if manager_user.is_superuser and not acting_as_producer:
        action = get_object_or_404(queryset, id=action_id)
    elif editor_is_technician and not acting_as_producer:
        action = get_object_or_404(queryset.filter(_technician_visibility_q(manager_user)).distinct(), id=action_id)
    else:
        action = get_object_or_404(queryset, id=action_id, user=effective_user)

    if not action.plant_series:
        messages.error(request, 'Cette action ne peut pas être modifiée (série manquante).')
        return redirect('my_records')

    selected_series = action.plant_series
    series_qs = _series_queryset_for_user(effective_user if acting_as_producer else manager_user)
    next_url = _sanitize_next_url(
        request.POST.get('next') if request.method == 'POST' else request.GET.get('next'),
        reverse('my_records'),
    )
    selected_lever = action.decision_lever

    if request.method == 'POST':
        post_data = request.POST.copy()
        post_data['plant_series'] = str(selected_series.id)
        form = PlantActionForm(
            post_data,
            instance=action,
            series_queryset=series_qs,
            selected_series=selected_series,
        )
        if form.is_valid():
            updated = form.save(commit=False)
            owner_profile = _get_profile(selected_series.user)
            updated.user = action.user
            updated.entered_by = action.entered_by or request.user
            updated.plant_series = selected_series
            updated.department = owner_profile.department or _effective_profile(request).department or manager_profile.department
            updated.crop_ref = selected_series.crop
            updated.conduct_type_ref = selected_series.conduct_type
            updated.variety_ref = selected_series.variety
            updated.decision_lever = selected_lever
            updated.save()
            messages.success(request, 'Action modifiée.')
            return redirect(next_url)
    else:
        form = PlantActionForm(
            instance=action,
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
            'is_technician': editor_is_technician,
            'target_user': selected_series.user,
            'action_types': action_types,
            'selected_lever': selected_lever,
            'recommendation_record': None,
            'form_mode': 'update',
            'next_url': next_url,
        },
    )


@login_required
def record_update_view(request, record_id):
    restriction = _effective_access_restriction(request, for_write=True)
    if restriction:
        messages.error(request, restriction['message'])
        return redirect('my_records')

    queryset = ScoutingRecord.objects.select_related('plant_series', 'user')
    manager_user = _manager_user(request)
    manager_profile = _get_profile(manager_user)
    effective_user = _effective_user(request)
    acting_as_producer = _is_acting_as_producer(request)
    editor_is_technician = (not acting_as_producer) and (manager_profile.role == UserProfile.ROLE_TECHNICIAN)
    if manager_user.is_superuser and not acting_as_producer:
        record = get_object_or_404(queryset, id=record_id)
    elif editor_is_technician and not acting_as_producer:
        record = get_object_or_404(queryset.filter(_technician_visibility_q(manager_user)).distinct(), id=record_id)
    else:
        record = get_object_or_404(queryset, id=record_id, user=effective_user)
    if not record.plant_series:
        messages.error(request, 'Cette saisie ne peut pas ?tre modifi?e (s?rie manquante).')
        if editor_is_technician and not acting_as_producer:
            return redirect('technician_records')
        return redirect('my_records')

    taxa = list(AuxiliaryTaxon.objects.filter(is_active=True).order_by('display_order', 'name'))
    other_pest_taxa = _active_other_pest_taxa()
    aphid_species_list = _active_aphid_species()
    default_aphid_species = _default_aphid_species(aphid_species_list)
    aphid_species_lookup = {species.id: species for species in aphid_species_list}
    series_qs = _series_queryset_for_user(effective_user if acting_as_producer else manager_user)
    selected_series = record.plant_series
    plants_count = selected_series.plants_count or 10
    leaves_count = selected_series.leaves_per_plant or 3

    if request.method == 'POST':
        post_data = request.POST.copy()
        post_data['plant_series'] = str(selected_series.id)
        entry_mode = record.entry_mode or 'detailed'
        form = (
            QuickScoutingRecordForm(post_data, instance=record, series_queryset=series_qs)
            if entry_mode == 'quick'
            else ScoutingRecordForm(post_data, instance=record, series_queryset=series_qs)
        )
        if form.is_valid():
            updated = form.save(commit=False)
            updated.plant_series = selected_series
            updated.user = record.user
            updated.department = record.department
            if entry_mode == 'quick':
                updated = _save_quick_record_from_form(
                    form=form,
                    record=updated,
                    request=request,
                    selected_series=selected_series,
                    taxa=taxa,
                    other_pest_taxa=other_pest_taxa,
                    aphid_species_lookup=aphid_species_lookup,
                    default_aphid_species=default_aphid_species,
                )
            else:
                updated.crop = selected_series.crop.name
                updated.crop_ref = selected_series.crop
                updated.conduct_type_ref = selected_series.conduct_type
                updated.variety_ref = selected_series.variety
                iso_date = updated.scouting_date.isocalendar()
                updated.year = iso_date.year
                updated.week = iso_date.week
                updated.entry_mode = 'detailed'
                updated.auxiliary_mode = 'detailed'
                updated.aphid_infested_percent = 0
                updated.auxiliary_total = 0

                plant_species_map, observed_species_ids, primary_species = _extract_record_species_data(
                    request.POST,
                    plants_count,
                    leaves_count,
                    aphid_species_lookup,
                    default_aphid_species,
                )
                if len(observed_species_ids) > 1 and primary_species is None:
                    form.add_error(
                        None,
                        "Plusieurs esp?ces de pucerons ont ?t? renseign?es. Choisissez l'esp?ce principale ? la fin du comptage.",
                    )
                if not form.errors:
                    try:
                        with transaction.atomic():
                            updated.save()
                            _write_leaf_observations(
                                updated,
                                request.POST,
                                plants_count,
                                leaves_count,
                                taxa,
                                other_pest_taxa,
                                plant_species_map,
                            )
                            updated.recompute_from_leaf_observations()
                            updated.primary_aphid_species = primary_species
                            updated.save(update_fields=['primary_aphid_species'])
                    except IntegrityError:
                        form.add_error(None, 'Une autre saisie existe d?j? pour cette s?rie et cette semaine.')
                    else:
                        updated = updated

            if updated is not None and not form.errors:
                messages.success(request, 'Saisie modifi?e.')
                next_view = request.POST.get('next') or request.GET.get('next')
                next_producer_id = request.POST.get('producer') or request.GET.get('producer')
                if next_view == 'technician_records' and _is_technician(manager_user) and not acting_as_producer:
                    redirect_url = reverse('technician_records')
                    if next_producer_id:
                        redirect_url = f'{redirect_url}?producer={next_producer_id}'
                    return redirect(redirect_url)
                return redirect('my_records')
    else:
        form = (
            QuickScoutingRecordForm(instance=record, series_queryset=series_qs)
            if record.entry_mode == 'quick'
            else ScoutingRecordForm(instance=record, series_queryset=series_qs)
        )

    if record.entry_mode == 'quick':
        return render(
            request,
            'scouting/record_quick_form.html',
            _quick_record_form_context(
                form=form,
                selected_series=selected_series,
                is_technician=(editor_is_technician and not acting_as_producer) or (
                    (request.user.id != selected_series.user_id) and not acting_as_producer
                ),
                target_user=selected_series.user,
                record_obj=record,
                form_mode='edit',
                aphid_species_list=aphid_species_list,
                default_aphid_species=default_aphid_species,
                taxa=taxa,
                other_pest_taxa=other_pest_taxa,
                quick_initial_data=(
                    _build_quick_initial_data_from_post(request.POST, taxa, other_pest_taxa)
                    if request.method == 'POST'
                    else _build_quick_initial_data(record, taxa, other_pest_taxa)
                ),
            ),
        )

    plants = list(range(1, plants_count + 1))
    leaf_positions = _leaf_positions_for_series(selected_series)
    initial_leaf_data = (
        _build_leaf_state_from_post(request.POST, plants_count, leaves_count, taxa, other_pest_taxa)
        if request.method == 'POST'
        else _build_initial_leaf_state(record)
    )

    return render(
        request,
        'scouting/record_form.html',
        _record_form_context(
            form=form,
            plants=plants,
            leaf_positions=leaf_positions,
            taxa=taxa,
            other_pest_taxa=other_pest_taxa,
            selected_series=selected_series,
            is_technician=(editor_is_technician and not acting_as_producer) or (
                (request.user.id != selected_series.user_id) and not acting_as_producer
            ),
            target_user=selected_series.user,
            record_obj=record,
            initial_leaf_data=initial_leaf_data,
            form_mode='edit',
            aphid_species_list=aphid_species_list,
            default_aphid_species=default_aphid_species,
        ),
    )
