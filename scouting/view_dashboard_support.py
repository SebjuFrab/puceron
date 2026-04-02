from collections import defaultdict
from statistics import fmean, median

from .models import DEPARTMENT_CHOICES, PlantSeries, ScoutingRecord, Variety
from .view_access import _get_profile, _parse_positive_int

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
