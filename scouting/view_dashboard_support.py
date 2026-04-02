from collections import defaultdict

from .models import PlantAction, PlantSeries, ScoutingRecord
from .utils import display_user_name
from .view_access import _effective_profile, _effective_user, _manager_user, _parse_positive_int, _series_queryset_for_user

DASHBOARD_SERIES_COLORS = [
    '#0d6efd',
    '#198754',
    '#d9480f',
    '#7b2cbf',
    '#0ca678',
    '#f59f00',
    '#c2255c',
    '#1c7ed6',
    '#2b8a3e',
    '#5f3dc4',
]

ORGANIC_MODE_LABELS = {
    'both': 'Bio et non bio',
    'bio': 'Bio',
    'non_bio': 'Non bio',
}


def _dashboard_series_queryset(user):
    return (
        PlantSeries.objects.filter(user=user, is_active=True)
        .select_related('crop', 'conduct_type', 'variety', 'user__profile')
        .order_by('crop__name', 'year', 'name')
    )



def _chart_color_for_action_type(action_type):
    return {
        'manual': '#f59f00',
        'treatment': '#d9480f',
        'release': '#198754',
    }.get(action_type.category, '#0d6efd')



def _serialize_action_details(action):
    parts = []
    if action.molecule_id:
        parts.append(action.molecule.name)
    if action.auxiliary_taxon_id:
        parts.append(action.auxiliary_taxon.name)
    if action.notes:
        parts.append(action.notes)
    return ' | '.join(parts) if parts else 'Sans detail complementaire'



def _series_chart_dataset(series, weeks, record_map, color):
    values = []
    for week in weeks:
        record = record_map.get(week)
        values.append(record if record is not None else None)
    return {
        'id': series.id,
        'label': series.name,
        'data': values,
        'borderColor': color,
        'backgroundColor': color,
    }



def _producer_dashboard_context(request):
    effective_user = _effective_user(request)
    profile = _effective_profile(request)
    series_list = list(_dashboard_series_queryset(effective_user))

    if not series_list:
        return {
            'dashboard_mode': 'producer',
            'series_list': [],
            'selected_crop': None,
            'selected_year': None,
            'displayed_series': [],
            'chart_labels': [],
            'aphid_datasets': [],
            'aux_datasets': [],
            'action_cards': [],
            'available_years': [],
            'crop_options': [],
            'show_all_series_default': True,
        }

    crop_map = {}
    for series in series_list:
        crop_map.setdefault(series.crop_id, series.crop)
    crop_options = sorted(crop_map.values(), key=lambda crop: crop.name.lower())

    selected_crop_id = _parse_positive_int(request.GET.get('crop'))
    if selected_crop_id not in crop_map:
        selected_crop_id = crop_options[0].id
    selected_crop = crop_map[selected_crop_id]

    crop_series = [series for series in series_list if series.crop_id == selected_crop_id]
    available_years = sorted({series.year for series in crop_series}, reverse=True)
    selected_year = _parse_positive_int(request.GET.get('year'), available_years[0] if available_years else None)
    if selected_year not in available_years and available_years:
        selected_year = available_years[0]

    year_series = [series for series in crop_series if series.year == selected_year]
    allowed_series_ids = {series.id for series in year_series}
    submitted_series_filter = request.GET.get('series_filter_submitted') == '1'
    raw_selected_series_ids = [
        parsed_id
        for raw_value in request.GET.getlist('visible_series')
        for parsed_id in [_parse_positive_int(raw_value)]
        if parsed_id
    ]
    selected_series_ids = {
        parsed_id
        for parsed_id in raw_selected_series_ids
        if parsed_id in allowed_series_ids
    }
    if not submitted_series_filter or (raw_selected_series_ids and not selected_series_ids):
        selected_series_ids = set(allowed_series_ids)

    displayed_series = [series for series in year_series if series.id in selected_series_ids]
    displayed_series_ids = {series.id for series in displayed_series}

    records = list(
        effective_user.records.filter(plant_series_id__in=allowed_series_ids, year=selected_year)
        .select_related('plant_series', 'crop_ref')
        .prefetch_related('leaf_observations')
        .order_by('week', 'scouting_date', 'id')
    )
    weeks = sorted({record.week for record in records if record.week and record.plant_series_id in displayed_series_ids})

    aphid_by_series = defaultdict(dict)
    aux_by_series = defaultdict(dict)
    last_record_by_series = {}
    for record in records:
        aphid_by_series[record.plant_series_id][record.week] = round(float(record.aphid_infested_percent), 2)
        aux_by_series[record.plant_series_id][record.week] = round(float(record.auxiliaries_per_plant), 2)
        last_record_by_series[record.plant_series_id] = record

    aphid_datasets = []
    aux_datasets = []
    for index, series in enumerate(displayed_series):
        color = DASHBOARD_SERIES_COLORS[index % len(DASHBOARD_SERIES_COLORS)]
        aphid_datasets.append(_series_chart_dataset(series, weeks, aphid_by_series.get(series.id, {}), color))
        aux_datasets.append(_series_chart_dataset(series, weeks, aux_by_series.get(series.id, {}), color))
        series.chart_color = color
        series.latest_record = last_record_by_series.get(series.id)

    action_cards = []
    actions = list(
        effective_user.plant_actions.filter(
            plant_series_id__in=displayed_series_ids,
            action_date__year=selected_year,
        )
        .select_related('action_type', 'molecule', 'auxiliary_taxon', 'plant_series')
        .order_by('action_date', 'id')
    )
    color_by_series_id = {series.id: series.chart_color for series in displayed_series}
    for action in actions:
        action_cards.append(
            {
                'week': action.action_date.isocalendar().week,
                'date': action.action_date.strftime('%d/%m/%Y'),
                'icon_symbol': action.action_type.chart_icon_symbol,
                'color': color_by_series_id.get(action.plant_series_id, _chart_color_for_action_type(action.action_type)),
                'type_name': action.action_type.name,
                'scope': action.get_scope_display(),
                'details': _serialize_action_details(action),
                'series_name': action.plant_series.name,
            }
        )

    displayed_records = [record for record in records if record.plant_series_id in displayed_series_ids]
    latest_weeks = sorted({record.week for record in displayed_records if record.week}, reverse=True)
    latest_week = latest_weeks[0] if latest_weeks else None

    return {
        'dashboard_mode': 'producer',
        'series_list': series_list,
        'crop_options': crop_options,
        'selected_crop': selected_crop,
        'selected_year': selected_year,
        'available_years': available_years,
        'year_series': year_series,
        'displayed_series': displayed_series,
        'hidden_series_count': max(len(year_series) - len(displayed_series), 0),
        'chart_labels': [f'S{week}' for week in weeks],
        'aphid_datasets': aphid_datasets,
        'aux_datasets': aux_datasets,
        'action_cards': action_cards,
        'record_count': len(displayed_records),
        'series_count': len(year_series),
        'displayed_series_count': len(displayed_series),
        'latest_week': latest_week,
        'show_all_series_default': not submitted_series_filter,
        'selected_series_ids': selected_series_ids,
        'profile': profile,
    }


def _technician_dashboard_context(request):
    profile = _effective_profile(request)
    manager_user = _manager_user(request)
    series_list = list(_series_queryset_for_user(manager_user))

    if not series_list:
        return {
            'dashboard_mode': 'technician',
            'series_list': [],
            'selected_crop': None,
            'selected_year': None,
            'displayed_series': [],
            'chart_labels': [],
            'aphid_datasets': [],
            'aux_datasets': [],
            'action_cards': [],
            'available_years': [],
            'crop_options': [],
            'available_producers': [],
            'available_varieties': [],
            'selected_producer_ids': set(),
            'selected_variety_ids': set(),
            'selected_series_ids': set(),
            'series_filter_submitted': False,
            'producer_filter_submitted': False,
            'variety_filter_submitted': False,
        }

    crop_map = {}
    for series in series_list:
        crop_map.setdefault(series.crop_id, series.crop)
    crop_options = sorted(crop_map.values(), key=lambda crop: crop.name.lower())

    selected_crop_id = _parse_positive_int(request.GET.get('crop'))
    if selected_crop_id not in crop_map:
        selected_crop_id = crop_options[0].id
    selected_crop = crop_map[selected_crop_id]

    crop_series = [series for series in series_list if series.crop_id == selected_crop_id]
    available_years = sorted({series.year for series in crop_series}, reverse=True)
    selected_year = _parse_positive_int(request.GET.get('year'), available_years[0] if available_years else None)
    if selected_year not in available_years and available_years:
        selected_year = available_years[0]

    organic_mode = request.GET.get('organic_mode')
    if organic_mode not in {'bio', 'non_bio', 'both'}:
        organic_mode = 'both'

    year_series = [series for series in crop_series if series.year == selected_year]
    if organic_mode != 'both':
        year_series = [series for series in year_series if series.organic_mode == organic_mode]

    available_producers_map = {}
    for series in year_series:
        producer_name = series.user.profile.farm_name or display_user_name(series.user)
        available_producers_map[series.user_id] = producer_name
    available_producers = [
        {'id': producer_id, 'name': producer_name}
        for producer_id, producer_name in sorted(available_producers_map.items(), key=lambda item: item[1].lower())
    ]
    allowed_producer_ids = {producer['id'] for producer in available_producers}
    producer_filter_submitted = request.GET.get('producer_filter_submitted') == '1'
    raw_selected_producer_ids = [
        parsed_id
        for raw_value in request.GET.getlist('producers')
        for parsed_id in [_parse_positive_int(raw_value)]
        if parsed_id
    ]
    selected_producer_ids = {
        parsed_id
        for parsed_id in raw_selected_producer_ids
        if parsed_id in allowed_producer_ids
    }
    if not producer_filter_submitted or (raw_selected_producer_ids and not selected_producer_ids):
        selected_producer_ids = set(allowed_producer_ids)

    producer_filtered_series = [series for series in year_series if series.user_id in selected_producer_ids]

    available_varieties_map = {}
    for series in producer_filtered_series:
        available_varieties_map[series.variety_id] = series.variety.name
    available_varieties = [
        {'id': variety_id, 'name': variety_name}
        for variety_id, variety_name in sorted(available_varieties_map.items(), key=lambda item: item[1].lower())
    ]
    allowed_variety_ids = {variety['id'] for variety in available_varieties}
    variety_filter_submitted = request.GET.get('variety_filter_submitted') == '1'
    raw_selected_variety_ids = [
        parsed_id
        for raw_value in request.GET.getlist('varieties')
        for parsed_id in [_parse_positive_int(raw_value)]
        if parsed_id
    ]
    selected_variety_ids = {
        parsed_id
        for parsed_id in raw_selected_variety_ids
        if parsed_id in allowed_variety_ids
    }
    if not variety_filter_submitted or (raw_selected_variety_ids and not selected_variety_ids):
        selected_variety_ids = set(allowed_variety_ids)

    variety_filtered_series = [series for series in producer_filtered_series if series.variety_id in selected_variety_ids]

    allowed_series_ids = {series.id for series in variety_filtered_series}
    series_filter_submitted = request.GET.get('series_filter_submitted') == '1'
    raw_selected_series_ids = [
        parsed_id
        for raw_value in request.GET.getlist('visible_series')
        for parsed_id in [_parse_positive_int(raw_value)]
        if parsed_id
    ]
    selected_series_ids = {
        parsed_id
        for parsed_id in raw_selected_series_ids
        if parsed_id in allowed_series_ids
    }
    if not series_filter_submitted or (raw_selected_series_ids and not selected_series_ids):
        selected_series_ids = set(allowed_series_ids)

    displayed_series = [series for series in variety_filtered_series if series.id in selected_series_ids]
    displayed_series_ids = {series.id for series in displayed_series}

    records = list(
        ScoutingRecord.objects.filter(plant_series_id__in=allowed_series_ids, year=selected_year)
        .select_related('plant_series', 'crop_ref', 'user', 'user__profile')
        .prefetch_related('leaf_observations')
        .order_by('week', 'scouting_date', 'id')
    )
    if not manager_user.is_superuser:
        visible_user_ids = {series.user_id for series in variety_filtered_series}
        records = [record for record in records if record.user_id in visible_user_ids]
    weeks = sorted({record.week for record in records if record.week and record.plant_series_id in displayed_series_ids})

    aphid_by_series = defaultdict(dict)
    aux_by_series = defaultdict(dict)
    last_record_by_series = {}
    for record in records:
        aphid_by_series[record.plant_series_id][record.week] = round(float(record.aphid_infested_percent), 2)
        aux_by_series[record.plant_series_id][record.week] = round(float(record.auxiliaries_per_plant), 2)
        last_record_by_series[record.plant_series_id] = record

    aphid_datasets = []
    aux_datasets = []
    for index, series in enumerate(displayed_series):
        color = DASHBOARD_SERIES_COLORS[index % len(DASHBOARD_SERIES_COLORS)]
        aphid_datasets.append(_series_chart_dataset(series, weeks, aphid_by_series.get(series.id, {}), color))
        aux_datasets.append(_series_chart_dataset(series, weeks, aux_by_series.get(series.id, {}), color))
        series.chart_color = color
        series.latest_record = last_record_by_series.get(series.id)
        series.producer_name = series.user.profile.farm_name or display_user_name(series.user)

    actions = list(
        PlantAction.objects.filter(
            plant_series_id__in=displayed_series_ids,
            action_date__year=selected_year,
        )
        .select_related('action_type', 'molecule', 'auxiliary_taxon', 'plant_series', 'user', 'user__profile')
        .order_by('action_date', 'id')
    )
    if not manager_user.is_superuser:
        visible_user_ids = {series.user_id for series in displayed_series}
        actions = [action for action in actions if action.user_id in visible_user_ids]
    color_by_series_id = {series.id: series.chart_color for series in displayed_series}
    action_cards = []
    for action in actions:
        action_cards.append(
            {
                'week': action.action_date.isocalendar().week,
                'date': action.action_date.strftime('%d/%m/%Y'),
                'icon_symbol': action.action_type.chart_icon_symbol,
                'color': color_by_series_id.get(action.plant_series_id, _chart_color_for_action_type(action.action_type)),
                'type_name': action.action_type.name,
                'scope': action.get_scope_display(),
                'details': _serialize_action_details(action),
                'series_name': action.plant_series.name,
                'producer_name': action.user.profile.farm_name or display_user_name(action.user),
            }
        )

    displayed_records = [record for record in records if record.plant_series_id in displayed_series_ids]
    latest_weeks = sorted({record.week for record in displayed_records if record.week}, reverse=True)
    latest_week = latest_weeks[0] if latest_weeks else None

    return {
        'dashboard_mode': 'technician',
        'profile': profile,
        'series_list': series_list,
        'crop_options': crop_options,
        'selected_crop': selected_crop,
        'selected_year': selected_year,
        'available_years': available_years,
        'organic_mode': organic_mode,
        'organic_mode_label': ORGANIC_MODE_LABELS.get(organic_mode, 'Bio et non bio'),
        'year_series': variety_filtered_series,
        'displayed_series': displayed_series,
        'hidden_series_count': max(len(variety_filtered_series) - len(displayed_series), 0),
        'chart_labels': [f'S{week}' for week in weeks],
        'aphid_datasets': aphid_datasets,
        'aux_datasets': aux_datasets,
        'action_cards': action_cards,
        'record_count': len(displayed_records),
        'series_count': len(variety_filtered_series),
        'displayed_series_count': len(displayed_series),
        'latest_week': latest_week,
        'available_producers': available_producers,
        'available_varieties': available_varieties,
        'selected_producer_ids': selected_producer_ids,
        'selected_variety_ids': selected_variety_ids,
        'selected_series_ids': selected_series_ids,
        'series_filter_submitted': series_filter_submitted,
        'producer_filter_submitted': producer_filter_submitted,
        'variety_filter_submitted': variety_filter_submitted,
    }
