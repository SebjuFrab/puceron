from .decision_engine import evaluate_record_recommendation
from .models import (
    InfoContentPage,
    InfoIndexPage,
    RecommendationDismissReason,
    RecommendationResponse,
    ScoutingRecord,
    UserProfile,
)
from .view_access import _get_profile, _technician_visibility_q

def _recommendation_record_queryset_for_user(user):
    qs = ScoutingRecord.objects.select_related('plant_series', 'crop_ref', 'plant_series__crop').prefetch_related(
        'leaf_observations'
    )
    if user.is_superuser:
        return qs
    profile = _get_profile(user)
    if profile.role == UserProfile.ROLE_TECHNICIAN:
        return qs.filter(_technician_visibility_q(user)).distinct()
    return qs.filter(user=user)


def _build_initial_leaf_state(record):
    data = {
        'aphids': {},
        'auxData': {},
        'pestData': {},
        'plantSpecies': {},
        'plantSpeciesTouched': {},
        'primaryAphidSpecies': str(record.primary_aphid_species_id or ''),
    }
    leaves = (
        record.leaf_observations.all()
        .prefetch_related('auxiliary_observations__taxon', 'other_pest_observations__taxon')
        .order_by('plant_number', 'leaf_index')
    )
    for leaf in leaves:
        pos = leaf.leaf_position
        if not pos.startswith('leaf_'):
            pos = f'leaf_{leaf.leaf_index}'
        aphid_key = f'p{leaf.plant_number}_{pos}_aphid'
        data['aphids'][aphid_key] = bool(leaf.aphid_present)
        if leaf.aphid_species_id:
            plant_key = str(leaf.plant_number)
            data['plantSpecies'][plant_key] = str(leaf.aphid_species_id)
            data['plantSpeciesTouched'][plant_key] = True
        leaf_key = f'{leaf.plant_number}-{pos}'
        data['auxData'][leaf_key] = {}
        data['pestData'][leaf_key] = {}
        for aux in leaf.auxiliary_observations.all():
            if aux.count <= 0:
                continue
            taxon_id = str(aux.taxon_id)
            data['auxData'][leaf_key][taxon_id] = {
                'taxonId': taxon_id,
                'name': aux.taxon.name,
                'count': aux.count,
            }
        for pest in leaf.other_pest_observations.all():
            taxon_id = str(pest.taxon_id)
            data['pestData'][leaf_key][taxon_id] = {
                'taxonId': taxon_id,
                'name': pest.taxon.name,
            }
    return data


def _dismiss_reasons_queryset():
    return RecommendationDismissReason.objects.filter(is_active=True).order_by('display_order', 'label')


def _sanitize_next_url(url, default):
    if url and url.startswith('/'):
        return url
    return default


def _mark_recommendation_followed(record, lever, action, handled_by):
    recommendation = evaluate_record_recommendation(record)
    if not recommendation['rule'] or recommendation['rule'].id != lever.rule_id:
        return
    RecommendationResponse.objects.update_or_create(
        record=record,
        rule=recommendation['rule'],
        defaults={
            'status': 'followed',
            'handled_by': handled_by,
            'dismiss_reason': None,
            'dismiss_note': '',
            'lever': lever,
            'action': action,
        },
    )


def _latest_series_recommendation(series):
    latest_record = next(iter(series.records.all()), None)
    series.latest_record = latest_record
    series.latest_recommendation = evaluate_record_recommendation(latest_record) if latest_record else None
    return series.latest_recommendation


def _info_index_page():
    return InfoIndexPage.objects.live().first()


def _info_pages_queryset():
    index_page = _info_index_page()
    if index_page:
        return (
            InfoContentPage.objects.live()
            .child_of(index_page)
            .prefetch_related('resources__document')
            .order_by('path')
        )
    return InfoContentPage.objects.none()
