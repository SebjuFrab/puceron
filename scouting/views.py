import datetime
import csv
import json
import secrets
import string
import unicodedata
from collections import defaultdict
from io import BytesIO
from io import StringIO
from statistics import fmean, median

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Avg, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

try:
    from openpyxl import Workbook
except Exception:  # pragma: no cover
    Workbook = None

from .forms import (
    PlantActionForm,
    PlantSeriesForm,
    ProducerAccountCreationForm,
    ProducerImportForm,
    ProducerProfileUpdateForm,
    RecommendationDismissForm,
    ScoutingRecordForm,
    UserProfileForm,
)
from .decision_engine import evaluate_record_recommendation
from .models import (
    ActionType,
    AuxiliaryTaxon,
    DEPARTMENT_CHOICES,
    DecisionLever,
    InfoContentPage,
    InfoIndexPage,
    LeafAuxiliaryObservation,
    LeafObservation,
    PlantAction,
    PlantSeries,
    RecommendationDismissReason,
    RecommendationResponse,
    ScoutingRecord,
    UserProfile,
    Variety,
)
from .utils import display_user_name

User = get_user_model()


def _get_profile(user):
    return UserProfile.objects.get_or_create(user=user)[0]


def _is_technician(user):
    if user.is_superuser:
        return True
    profile = _get_profile(user)
    return profile.role == UserProfile.ROLE_TECHNICIAN


def _can_manage_producers(user):
    return bool(user.is_authenticated and (user.is_superuser or _is_technician(user)))


def _technician_visibility_q(user, profile_prefix='user__profile'):
    profile = _get_profile(user)
    assigned_lookup = f'{profile_prefix}__assigned_technician' if profile_prefix else 'assigned_technician'
    department_lookup = f'{profile_prefix}__department' if profile_prefix else 'department'
    base_query = Q(**{assigned_lookup: user})
    if profile.department:
        base_query |= Q(**{f'{assigned_lookup}__isnull': True, department_lookup: profile.department})
    return base_query


def _series_queryset_for_user(user):
    qs = PlantSeries.objects.select_related('crop', 'conduct_type', 'variety', 'user', 'user__profile').filter(
        is_active=True
    )
    if user.is_superuser:
        return qs
    profile = _get_profile(user)
    if profile.role == UserProfile.ROLE_TECHNICIAN:
        return qs.filter(user__profile__role=UserProfile.ROLE_PRODUCER).filter(
            _technician_visibility_q(user, 'user__profile')
        )
    return qs.filter(user=user)


def _accessible_producer_profiles(user):
    qs = (
        UserProfile.objects.select_related('user', 'assigned_technician')
        .prefetch_related('user__plant_series')
        .filter(role=UserProfile.ROLE_PRODUCER)
    )
    if user.is_superuser:
        return qs.order_by('farm_name', 'user__username')
    return qs.filter(_technician_visibility_q(user, '')).order_by('farm_name', 'user__username')


def _filter_records(request, queryset):
    year = request.GET.get('year')
    crop = request.GET.get('crop')
    department = request.GET.get('department')
    producer = request.GET.get('producer')

    if year:
        queryset = queryset.filter(year=year)
    if crop:
        queryset = queryset.filter(crop=crop)
    if department:
        queryset = queryset.filter(department=department)
    if producer:
        queryset = queryset.filter(user_id=producer)
    return queryset


def _parse_count(value):
    if value in (None, ''):
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(parsed, 0)


def _parse_positive_int(value, default=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


CSV_IMPORT_COLUMN_ALIASES = {
    'raison social': 'farm_name',
    'raison sociale': 'farm_name',
    'nom': 'last_name',
    'prenom': 'first_name',
    'departement': 'department',
    'mail': 'email',
    'adresse': 'street_address',
    'code postal': 'postal_code',
    'commune': 'city',
    'idtek referents': 'technician_ref',
    'idtek referent': 'technician_ref',
    'idtek reference': 'technician_ref',
    'mobile': 'phone',
}

CSV_IMPORT_REQUIRED_FIELDS = (
    'farm_name',
    'last_name',
    'first_name',
    'email',
    'street_address',
    'postal_code',
    'city',
)


def _normalize_csv_header(value):
    normalized = unicodedata.normalize('NFKD', str(value or ''))
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii')
    ascii_value = ' '.join(ascii_value.replace('_', ' ').replace('\t', ' ').split())
    return ascii_value.strip().lower()


def _decode_csv_upload(uploaded_file):
    raw = uploaded_file.read()
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def _load_csv_rows(uploaded_file):
    content = _decode_csv_upload(uploaded_file)
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=';,|\t,')
    except csv.Error:
        class _FallbackDialect(csv.excel):
            delimiter = ';'
        dialect = _FallbackDialect

    reader = csv.DictReader(StringIO(content), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError('Le fichier CSV est vide ou sans entetes.')

    normalized_headers = {}
    for original in reader.fieldnames:
        normalized = _normalize_csv_header(original)
        mapped = CSV_IMPORT_COLUMN_ALIASES.get(normalized)
        if mapped:
            normalized_headers[original] = mapped

    missing = [field for field in CSV_IMPORT_REQUIRED_FIELDS if field not in normalized_headers.values()]
    if missing:
        raise ValueError(
            'Colonnes manquantes dans le CSV: ' + ', '.join(missing) + '.'
        )

    rows = []
    for index, row in enumerate(reader, start=2):
        mapped_row = {'_line': index}
        for original, mapped in normalized_headers.items():
            mapped_row[mapped] = (row.get(original) or '').strip()
        if not any(value for key, value in mapped_row.items() if key != '_line'):
            continue
        rows.append(mapped_row)
    return rows


def _random_temporary_password():
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(10))


def _generate_unique_username(first_name, last_name, farm_name, email):
    if email:
        base = (email.split('@', 1)[0] or '').strip()
    else:
        base = ''
    if not base:
        base = '.'.join(part for part in [first_name, last_name] if part).strip('.')
    if not base:
        base = farm_name or 'producteur'
    slug = unicodedata.normalize('NFKD', base).encode('ascii', 'ignore').decode('ascii').lower()
    slug = ''.join(ch if ch.isalnum() else '.' for ch in slug)
    slug = '.'.join(filter(None, slug.split('.'))).strip('.')
    slug = slug[:120] or 'producteur'
    candidate = slug
    suffix = 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f'{slug[:140]}-{suffix}'
        suffix += 1
    return candidate[:150]


def _resolve_import_technician(importer, technician_ref):
    if not importer.is_superuser:
        technician_profile = _get_profile(importer)
        if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
            raise ValueError('Seuls les techniciens ou super-admin peuvent importer des producteurs.')
        return importer

    reference = (technician_ref or '').strip()
    if not reference:
        raise ValueError('Colonne "IDtek referents" obligatoire pour un import super-admin.')

    technician_qs = User.objects.filter(profile__role=UserProfile.ROLE_TECHNICIAN)
    technician = None
    if reference.isdigit():
        technician = technician_qs.filter(id=int(reference)).first()
    if technician is None:
        technician = technician_qs.filter(username__iexact=reference).first()
    if technician is None:
        technician = technician_qs.filter(email__iexact=reference).first()
    if technician is None:
        raise ValueError(f'Technicien introuvable pour la reference "{reference}".')
    return technician


def _upsert_producer_from_csv_row(row, importer, update_existing):
    technician = _resolve_import_technician(importer, row.get('technician_ref', ''))
    technician_profile = _get_profile(technician)
    if technician_profile.role != UserProfile.ROLE_TECHNICIAN:
        raise ValueError(f'{display_user_name(technician)} n est pas technicien.')
    if not technician_profile.department:
        raise ValueError(f'{display_user_name(technician)} n a pas de departement renseigne.')

    email = (row.get('email') or '').strip().lower()
    if not email:
        raise ValueError('Email obligatoire pour identifier ou creer le producteur.')

    existing_email_user = User.objects.filter(email__iexact=email).first()
    if existing_email_user and not update_existing:
        raise ValueError(f'Un utilisateur existe deja avec l email {email}.')
    existing_user = existing_email_user if update_existing else None
    created = False
    temporary_password = ''

    with transaction.atomic():
        if existing_user:
            user = existing_user
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile.role not in (UserProfile.ROLE_PRODUCER, ''):
                raise ValueError(f'L utilisateur {display_user_name(user)} existe deja avec un role non producteur.')
            action = 'updated'
        else:
            user = User(
                username=_generate_unique_username(
                    row.get('first_name', ''),
                    row.get('last_name', ''),
                    row.get('farm_name', ''),
                    email,
                ),
                email=email,
            )
            temporary_password = _random_temporary_password()
            user.set_password(temporary_password)
            action = 'created'
            created = True

        user.first_name = row.get('first_name', '')
        user.last_name = row.get('last_name', '')
        user.email = email
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = UserProfile.ROLE_PRODUCER
        profile.assigned_technician = technician
        profile.department = technician_profile.department
        profile.farm_name = row.get('farm_name', '')
        profile.phone = row.get('phone', '')
        profile.street_address = row.get('street_address', '')
        profile.postal_code = row.get('postal_code', '')
        profile.city = row.get('city', '')
        profile.save()

    requested_department = (row.get('department') or '').strip()
    notes = []
    if requested_department and requested_department != technician_profile.department:
        notes.append(
            f'Departement CSV {requested_department} remplace par {technician_profile.department} (technicien referent).'
        )

    return {
        'status': action,
        'created': created,
        'user': user,
        'profile': profile,
        'technician': technician,
        'temporary_password': temporary_password,
        'note': ' '.join(notes),
    }


def _dashboard_series_queryset(user):
    return (
        PlantSeries.objects.filter(user=user, is_active=True)
        .select_related('crop', 'conduct_type', 'variety', 'user__profile')
        .order_by('name')
    )


def _chart_color_for_action_type(action_type):
    return {
        'manual': '#f59f00',
        'treatment': '#d9480f',
        'release': '#198754',
    }.get(action_type.category, '#0d6efd')


def _dashboard_aggregate(values, aggregation):
    if not values:
        return None
    if aggregation == 'average':
        return round(float(fmean(values)), 2)
    return round(float(median(values)), 2)


def _closest_week_value(week_to_value, target_week):
    available = [week for week, value in week_to_value.items() if value is not None]
    if not available:
        return 0.0
    if target_week in week_to_value and week_to_value[target_week] is not None:
        return week_to_value[target_week]
    closest_week = min(available, key=lambda week: (abs(week - target_week), week))
    return float(week_to_value[closest_week])


def _action_marker_value(series_values, group_values, target_week, stack_level):
    base_candidates = [
        _closest_week_value(series_values, target_week),
        _closest_week_value(group_values, target_week),
    ]
    base_value = max(base_candidates) if base_candidates else 0.0
    offset = max(0.15, base_value * 0.12 if base_value else 0.15)
    return round(base_value + (offset * (stack_level + 1)), 2)


def _serialize_action_summary(action):
    parts = [action.action_type.name, action.get_scope_display()]
    if action.molecule_id:
        parts.append(action.molecule.name)
    if action.auxiliary_taxon_id:
        parts.append(action.auxiliary_taxon.name)
    if action.notes:
        parts.append(action.notes)
    return ' | '.join(parts)


def _serialize_action_details(action):
    parts = []
    if action.molecule_id:
        parts.append(action.molecule.name)
    if action.auxiliary_taxon_id:
        parts.append(action.auxiliary_taxon.name)
    if action.notes:
        parts.append(action.notes)
    return ' | '.join(parts) if parts else 'Sans detail complementaire'


def _producer_dashboard_context(request):
    profile = _get_profile(request.user)
    series_list = list(_dashboard_series_queryset(request.user))
    selected_series = None
    selected_series_id = _parse_positive_int(request.GET.get('series'))
    if selected_series_id:
        selected_series = next((series for series in series_list if series.id == selected_series_id), None)
    if selected_series is None and series_list:
        selected_series = series_list[0]

    if selected_series is None:
        return {
            'dashboard_mode': 'producer',
            'series_list': series_list,
            'selected_series': None,
        }

    same_crop_series_qs = PlantSeries.objects.filter(is_active=True, crop=selected_series.crop).select_related(
        'variety',
        'user__profile',
    )
    available_years = sorted(same_crop_series_qs.values_list('year', flat=True).distinct(), reverse=True)
    comparison_year = _parse_positive_int(request.GET.get('comparison_year'), selected_series.year)
    if available_years and comparison_year not in available_years:
        comparison_year = selected_series.year

    aggregation = request.GET.get('aggregation')
    if aggregation not in {'median', 'average'}:
        aggregation = 'median'

    organic_mode = request.GET.get('organic_mode')
    if organic_mode not in {'bio', 'non_bio', 'both'}:
        organic_mode = selected_series.organic_mode or 'bio'
    organic_mode_labels = {
        'bio': 'Bio (AB)',
        'non_bio': 'Non bio',
        'both': 'Bio et non bio',
    }

    department_map = dict(DEPARTMENT_CHOICES)
    selected_departments = [value for value in request.GET.getlist('departments') if value in department_map]
    available_departments = []
    for department_code in same_crop_series_qs.values_list('user__profile__department', flat=True).distinct():
        if department_code in department_map:
            available_departments.append(
                {
                    'code': department_code,
                    'label': department_map[department_code],
                }
            )
    available_departments.sort(key=lambda item: item['code'])

    available_varieties = list(
        Variety.objects.filter(crop=selected_series.crop, plant_series__is_active=True)
        .distinct()
        .order_by('name')
    )
    allowed_variety_ids = {variety.id for variety in available_varieties}
    selected_variety_ids = {
        _parse_positive_int(value)
        for value in request.GET.getlist('varieties')
        if _parse_positive_int(value) in allowed_variety_ids
    }

    technician_only = request.GET.get('technician_only') == '1' and bool(profile.assigned_technician_id)

    series_records = list(
        selected_series.records.filter(year=selected_series.year)
        .select_related('crop_ref', 'plant_series')
        .prefetch_related('leaf_observations')
        .order_by('week', 'scouting_date')
    )
    comparison_records_qs = (
        ScoutingRecord.objects.filter(
            plant_series__isnull=False,
            crop_ref=selected_series.crop,
            year=comparison_year,
        )
        .select_related('plant_series', 'variety_ref', 'user__profile')
        .prefetch_related('leaf_observations')
    )
    if organic_mode != 'both':
        comparison_records_qs = comparison_records_qs.filter(plant_series__organic_mode=organic_mode)
    if selected_departments:
        comparison_records_qs = comparison_records_qs.filter(department__in=selected_departments)
    if selected_variety_ids:
        comparison_records_qs = comparison_records_qs.filter(variety_ref_id__in=selected_variety_ids)
    if technician_only and profile.assigned_technician_id:
        comparison_records_qs = comparison_records_qs.filter(
            user__profile__assigned_technician_id=profile.assigned_technician_id
        )
    comparison_records = list(comparison_records_qs.order_by('week', 'scouting_date'))

    series_week_map = {}
    series_aphid_values = {}
    series_aux_values = {}
    for record in series_records:
        series_week_map[record.week] = record
        series_aphid_values[record.week] = round(float(record.aphid_infested_percent), 2)
        series_aux_values[record.week] = round(float(record.auxiliaries_per_plant), 2)

    group_values = defaultdict(lambda: {'aphid': [], 'aux': []})
    comparison_series_ids = set()
    for record in comparison_records:
        group_values[record.week]['aphid'].append(float(record.aphid_infested_percent))
        group_values[record.week]['aux'].append(float(record.auxiliaries_per_plant))
        if record.plant_series_id:
            comparison_series_ids.add(record.plant_series_id)

    actions = list(
        selected_series.actions.filter(action_date__year=selected_series.year)
        .select_related('action_type', 'molecule', 'auxiliary_taxon')
        .order_by('action_date', 'id')
    )
    action_weeks = [action.action_date.isocalendar().week for action in actions]

    weeks = sorted(set(series_week_map.keys()) | set(group_values.keys()) | set(action_weeks))
    labels = [f'S{week}' for week in weeks]
    week_index_map = {week: index for index, week in enumerate(weeks)}

    series_aphid_points = [series_aphid_values.get(week) for week in weeks]
    series_aux_points = [series_aux_values.get(week) for week in weeks]
    group_aphid_values = {
        week: _dashboard_aggregate(group_values[week]['aphid'], aggregation) if week in group_values else None
        for week in weeks
    }
    group_aux_values = {
        week: _dashboard_aggregate(group_values[week]['aux'], aggregation) if week in group_values else None
        for week in weeks
    }
    group_aphid_points = [group_aphid_values.get(week) for week in weeks]
    group_aux_points = [group_aux_values.get(week) for week in weeks]

    grouped_actions = defaultdict(lambda: defaultdict(list))
    for action in actions:
        grouped_actions[action.action_type][action.action_date.isocalendar().week].append(action)
    week_action_order = {}
    for week in sorted(set(action_weeks)):
        week_action_order[week] = [
            action_type.id
            for action_type in sorted(
                [action_type for action_type, actions_by_week in grouped_actions.items() if week in actions_by_week],
                key=lambda action_type: (action_type.display_order, action_type.id),
            )
        ]

    infestation_action_datasets = []
    auxiliary_action_datasets = []
    action_cards = []
    for action_type, actions_by_week in grouped_actions.items():
        icon_key = action_type.resolved_chart_icon
        color = _chart_color_for_action_type(action_type)
        infestation_data = [None] * len(weeks)
        aux_data = [None] * len(weeks)
        infestation_meta = [None] * len(weeks)
        aux_meta = [None] * len(weeks)
        for week, week_actions in sorted(actions_by_week.items()):
            index = week_index_map.get(week)
            if index is None:
                continue
            stack_level = week_action_order.get(week, [action_type.id]).index(action_type.id)
            infestation_data[index] = _action_marker_value(series_aphid_values, group_aphid_values, week, stack_level)
            aux_data[index] = _action_marker_value(series_aux_values, group_aux_values, week, stack_level)
            tooltip_lines = [f"{action.action_date.strftime('%d/%m/%Y')} - {_serialize_action_summary(action)}" for action in week_actions]
            meta = {
                'week': week,
                'count': len(week_actions),
                'lines': tooltip_lines,
            }
            infestation_meta[index] = meta
            aux_meta[index] = meta

        infestation_action_datasets.append(
            {
                'label': action_type.name,
                'data': infestation_data,
                'customData': infestation_meta,
                'pointStyle': icon_key,
                'backgroundColor': color,
                'borderColor': color,
            }
        )
        auxiliary_action_datasets.append(
            {
                'label': action_type.name,
                'data': aux_data,
                'customData': aux_meta,
                'pointStyle': icon_key,
                'backgroundColor': color,
                'borderColor': color,
            }
        )

        for action in sorted(
            [item for sublist in actions_by_week.values() for item in sublist],
            key=lambda item: (item.action_date, item.id),
        ):
            action_cards.append(
                {
                    'week': action.action_date.isocalendar().week,
                    'date': action.action_date.strftime('%d/%m/%Y'),
                    'icon_symbol': action_type.chart_icon_symbol,
                    'color': color,
                    'type_name': action_type.name,
                    'scope': action.get_scope_display(),
                    'details': _serialize_action_details(action),
                }
            )

    action_cards.sort(key=lambda item: (item['week'], item['date']))

    comparison_label = 'Mediane du groupe' if aggregation == 'median' else 'Moyenne du groupe'
    comparison_subtitle_parts = [f'Culture {selected_series.crop.name}', f'Annee groupe {comparison_year}']
    comparison_subtitle_parts.append(organic_mode_labels[organic_mode])
    if selected_departments:
        comparison_subtitle_parts.append(f"{len(selected_departments)} departement(s)")
    if selected_variety_ids:
        comparison_subtitle_parts.append(f"{len(selected_variety_ids)} variete(s)")
    if technician_only and profile.assigned_technician_id:
        comparison_subtitle_parts.append('Seulement mon technicien')

    latest_record = series_records[-1] if series_records else None
    return {
        'dashboard_mode': 'producer',
        'series_list': series_list,
        'selected_series': selected_series,
        'selected_series_latest_record': latest_record,
        'comparison_year': comparison_year,
        'available_years': available_years,
        'aggregation': aggregation,
        'organic_mode': organic_mode,
        'available_departments': available_departments,
        'selected_departments': selected_departments,
        'available_varieties': available_varieties,
        'selected_variety_ids': selected_variety_ids,
        'technician_only': technician_only,
        'can_filter_technician_only': bool(profile.assigned_technician_id),
        'comparison_record_count': len(comparison_records),
        'comparison_series_count': len(comparison_series_ids),
        'comparison_label': comparison_label,
        'comparison_subtitle': ' | '.join(comparison_subtitle_parts),
        'chart_labels': labels,
        'series_aphid_points': series_aphid_points,
        'group_aphid_points': group_aphid_points,
        'series_aux_points': series_aux_points,
        'group_aux_points': group_aux_points,
        'infestation_action_datasets': infestation_action_datasets,
        'auxiliary_action_datasets': auxiliary_action_datasets,
        'action_cards': action_cards,
    }


def _target_user_for_series(request_user, selected_series, is_tech_user):
    if is_tech_user:
        return selected_series.user
    if selected_series.user_id == request_user.id:
        return request_user
    return selected_series.user


def _profile_address_context(profile):
    return {
        'profile_map_initial_lat': float(profile.latitude) if profile.latitude is not None else 46.603354,
        'profile_map_initial_lng': float(profile.longitude) if profile.longitude is not None else 1.888334,
        'profile_has_coordinates': profile.latitude is not None and profile.longitude is not None,
    }


def _recommendation_record_queryset_for_user(user):
    qs = ScoutingRecord.objects.select_related('plant_series', 'crop_ref', 'plant_series__crop').prefetch_related(
        'leaf_observations'
    )
    if user.is_superuser:
        return qs
    profile = _get_profile(user)
    if profile.role == UserProfile.ROLE_TECHNICIAN:
        return qs.filter(_technician_visibility_q(user))
    return qs.filter(user=user)


def _build_initial_leaf_state(record):
    data = {
        'aphids': {},
        'auxData': {},
    }
    leaves = (
        record.leaf_observations.all()
        .prefetch_related('auxiliary_observations__taxon')
        .order_by('plant_number', 'leaf_index')
    )
    for leaf in leaves:
        pos = leaf.leaf_position
        if not pos.startswith('leaf_'):
            pos = f'leaf_{leaf.leaf_index}'
        aphid_key = f'p{leaf.plant_number}_{pos}_aphid'
        data['aphids'][aphid_key] = bool(leaf.aphid_present)
        leaf_key = f'{leaf.plant_number}-{pos}'
        data['auxData'][leaf_key] = {}
        for aux in leaf.auxiliary_observations.all():
            if aux.count <= 0:
                continue
            taxon_id = str(aux.taxon_id)
            data['auxData'][leaf_key][taxon_id] = {
                'taxonId': taxon_id,
                'name': aux.taxon.name,
                'count': aux.count,
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
def producer_create_view(request):
    if not _can_manage_producers(request.user):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')

    creator_profile = _get_profile(request.user)
    if (not request.user.is_superuser) and not creator_profile.department:
        messages.error(request, 'Renseignez votre departement avant de creer un producteur.')
        return redirect('my_profile')

    if request.method == 'POST':
        form = ProducerAccountCreationForm(request.POST, creator=request.user)
        if form.is_valid():
            created_user = form.save()
            messages.success(
                request,
                f'Compte producteur cree: {display_user_name(created_user)} (identifiant: {created_user.username})',
            )
            return redirect('producer_create')
    else:
        form = ProducerAccountCreationForm(creator=request.user)

    return render(
        request,
        'scouting/producer_create.html',
        {
            'form': form,
            'is_super_admin_creator': request.user.is_superuser,
        },
    )


@login_required
def producer_import_view(request):
    if not _can_manage_producers(request.user):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')

    creator_profile = _get_profile(request.user)
    if (not request.user.is_superuser) and not creator_profile.department:
        messages.error(request, 'Renseignez votre departement avant d importer des producteurs.')
        return redirect('my_profile')

    results = []
    summary = None
    if request.method == 'POST':
        form = ProducerImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                rows = _load_csv_rows(form.cleaned_data['csv_file'])
            except ValueError as exc:
                form.add_error('csv_file', str(exc))
            else:
                update_existing = form.cleaned_data['update_existing']
                created_count = 0
                updated_count = 0
                error_count = 0

                for row in rows:
                    missing = [
                        field
                        for field in CSV_IMPORT_REQUIRED_FIELDS
                        if not (row.get(field) or '').strip()
                    ]
                    if missing:
                        error_count += 1
                        results.append(
                            {
                                'line': row['_line'],
                                'status': 'error',
                                'message': 'Champs obligatoires manquants: ' + ', '.join(missing),
                            }
                        )
                        continue

                    try:
                        result = _upsert_producer_from_csv_row(row, request.user, update_existing)
                    except ValueError as exc:
                        error_count += 1
                        results.append(
                            {
                                'line': row['_line'],
                                'status': 'error',
                                'message': str(exc),
                            }
                        )
                        continue

                    if result['created']:
                        created_count += 1
                    else:
                        updated_count += 1
                    results.append(
                        {
                            'line': row['_line'],
                            'status': result['status'],
                            'producer_name': display_user_name(result['user']),
                            'username': result['user'].username,
                            'email': result['user'].email,
                            'technician_name': display_user_name(result['technician']),
                            'temporary_password': result['temporary_password'],
                            'message': result['note'] or '',
                        }
                    )

                summary = {
                    'total': len(rows),
                    'created': created_count,
                    'updated': updated_count,
                    'errors': error_count,
                }
                if error_count:
                    messages.warning(
                        request,
                        f'Import termine: {created_count} crees, {updated_count} mis a jour, {error_count} en erreur.',
                    )
                else:
                    messages.success(
                        request,
                        f'Import termine: {created_count} crees, {updated_count} mis a jour.',
                    )
    else:
        form = ProducerImportForm()

    expected_columns = [
        'Raison social',
        'Nom',
        'Prenom',
        'Departement',
        'mail',
        'Adresse',
        'code postal',
        'commune',
        'IDtek referents',
        'mobile',
    ]
    return render(
        request,
        'scouting/producer_import.html',
        {
            'form': form,
            'results': results,
            'summary': summary,
            'expected_columns': expected_columns,
            'is_super_admin_creator': request.user.is_superuser,
            'current_technician_name': display_user_name(request.user) if not request.user.is_superuser else '',
        },
    )


@login_required
def producer_update_view(request, producer_id):
    if not _can_manage_producers(request.user):
        messages.error(request, 'Acces reserve aux techniciens et au super-admin.')
        return redirect('dashboard')

    producer_profile_qs = _accessible_producer_profiles(request.user).select_related('user')
    producer_profile = get_object_or_404(producer_profile_qs, user_id=producer_id)
    producer_user = producer_profile.user

    if request.method == 'POST':
        form = ProducerProfileUpdateForm(
            request.POST,
            instance=producer_profile,
            editor=request.user,
            producer_user=producer_user,
        )
        if form.is_valid():
            form.save()
            messages.success(request, 'Profil producteur mis a jour.')
            next_view = request.POST.get('next') or request.GET.get('next')
            if next_view == 'technician_records':
                return redirect(f"{reverse('technician_records')}?producer={producer_user.id}")
            return redirect('producer_update', producer_id=producer_user.id)
    else:
        form = ProducerProfileUpdateForm(
            instance=producer_profile,
            editor=request.user,
            producer_user=producer_user,
        )

    context = {
        'form': form,
        'producer_profile': producer_profile,
        'producer_user': producer_user,
        'is_super_admin_editor': request.user.is_superuser,
    }
    context.update(_profile_address_context(producer_profile))
    return render(request, 'scouting/producer_update.html', context)


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


@login_required
def my_series_view(request):
    profile = _get_profile(request.user)
    if (not request.user.is_superuser) and _is_technician(request.user):
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
    if request.user.is_superuser:
        series_qs = PlantSeries.objects.select_related('crop', 'conduct_type', 'variety', 'user').prefetch_related(
            records_prefetch
        )
    else:
        series_qs = (
            PlantSeries.objects.filter(user=request.user)
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
            series.user = request.user
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
        PlantSeries.objects.filter(user=request.user, is_active=True)
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

    record = get_object_or_404(_recommendation_record_queryset_for_user(request.user), id=record_id)
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


@login_required
def my_profile_view(request):
    profile = _get_profile(request.user)
    technician_profile = _get_profile(profile.assigned_technician) if profile.assigned_technician_id else None
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profil mis a jour.')
            return redirect('my_profile')
    else:
        form = UserProfileForm(instance=profile, user=request.user)
    context = {
        'form': form,
        'profile': profile,
        'technician_profile': technician_profile,
    }
    context.update(_profile_address_context(profile))
    return render(request, 'scouting/my_profile.html', context)


@login_required
def my_records_view(request):
    records = ScoutingRecord.objects.filter(user=request.user).prefetch_related('leaf_observations')
    records = _filter_records(request, records)
    actions = PlantAction.objects.filter(user=request.user).select_related(
        'action_type',
        'plant_series',
        'molecule',
        'auxiliary_taxon',
    )
    return render(request, 'scouting/my_records.html', {'records': records, 'actions': actions})


@login_required
def technician_records_view(request):
    if not _is_technician(request.user):
        messages.error(request, 'Acces reserve aux techniciens.')
        return redirect('dashboard')

    producer_profiles = list(_accessible_producer_profiles(request.user))
    selected_producer = None
    selected_producer_id = request.GET.get('producer')

    if selected_producer_id:
        selected_producer = next(
            (profile for profile in producer_profiles if str(profile.user_id) == str(selected_producer_id)),
            None,
        )
        if selected_producer is None:
            messages.error(request, 'Le producteur selectionne est introuvable ou hors perimetre.')

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
    if not request.user.is_superuser:
        visibility_query = _technician_visibility_q(request.user)
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
            'producer_map_data_json': json.dumps(producer_map_data),
            'mapped_producers_count': len(producer_map_data),
            'producers_without_coordinates': producers_without_coordinates,
            'selected_producer': selected_producer,
            'selected_producer_data': selected_producer_data,
            'selected_series': selected_series,
        },
    )


@login_required
def export_records_view(request):
    if Workbook is None:
        return HttpResponse('openpyxl manquant: installer openpyxl pour l export Excel.', status=500)

    scope = request.GET.get('scope', 'me')
    if scope == 'all' and _is_technician(request.user):
        qs = ScoutingRecord.objects.select_related('user').prefetch_related('leaf_observations')
        if not request.user.is_superuser:
            qs = qs.filter(_technician_visibility_q(request.user))
    else:
        qs = (
            ScoutingRecord.objects.filter(user=request.user)
            .select_related('user')
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
            rec.get_crop_display(),
            rec.conduct_type_ref.name if rec.conduct_type_ref else '',
            rec.variety_ref.name if rec.variety_ref else '',
            rec.scouting_date.isoformat(),
            rec.year,
            rec.week,
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
