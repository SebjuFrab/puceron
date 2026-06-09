from collections import defaultdict
from statistics import median

from django.db.models import Count, Sum

from .models import (
    AUXILIARY_SPECIES,
    AuxiliaryTaxon,
    LeafObservation,
    LeafAuxiliaryObservation,
    LeafOtherPestObservation,
    PlantAction,
    PlantSeries,
    QuickRecordAuxiliaryCount,
    QuickRecordOtherPestCount,
    ScoutingRecord,
)
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

OTHER_PEST_CHART_COLORS = [
    '#c2255c',
    '#d9480f',
    '#7b2cbf',
    '#0ca678',
    '#f59f00',
    '#1c7ed6',
    '#2b8a3e',
    '#495057',
]

AUXILIARY_CHART_COLORS = [
    '#198754',
    '#f59f00',
    '#0d6efd',
    '#7b2cbf',
    '#0ca678',
    '#c2255c',
    '#d9480f',
    '#2b8a3e',
    '#1c7ed6',
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
    return ' | '.join(parts) if parts else 'Sans détail complémentaire'



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


def _reference_chart_dataset(weeks, values_by_week, label, color='#495057'):
    return {
        'id': f'reference-{label.lower().replace(" ", "-")}',
        'label': label,
        'data': [values_by_week.get(week) for week in weeks],
        'borderColor': color,
        'backgroundColor': color,
        'borderDash': [8, 6],
        'pointRadius': 5,
        'pointHoverRadius': 7,
        'pointStyle': 'rectRot',
        'pointBackgroundColor': '#ffffff',
        'pointBorderColor': color,
        'pointBorderWidth': 3,
        'borderWidth': 3,
        'tension': 0.18,
    }


def _taxon_reference_datasets(raw_datasets, weeks, label, fallback_name, dataset_prefix):
    if not raw_datasets or not weeks or not label:
        return []

    values_by_taxon = defaultdict(lambda: defaultdict(list))
    meta_by_taxon = {}
    for dataset in raw_datasets:
        taxon_id = dataset.get('taxonId')
        if not taxon_id:
            continue
        meta_by_taxon.setdefault(
            taxon_id,
            {
                'name': dataset.get('label', f'{fallback_name} {taxon_id}'),
                'color': dataset.get('borderColor', '#495057'),
            },
        )
        for index, week in enumerate(weeks):
            if index >= len(dataset.get('data', [])):
                continue
            value = dataset['data'][index]
            if value is None:
                continue
            values_by_taxon[taxon_id][week].append(value)

    aggregator = _average if label == 'Moyenne du groupe' else _median
    datasets = []
    for taxon_id, week_values in values_by_taxon.items():
        taxon_meta = meta_by_taxon.get(taxon_id, {})
        datasets.append(
            {
                'id': f'{dataset_prefix}-reference-{taxon_id}',
                'seriesId': 'reference',
                'taxonId': taxon_id,
                'label': f"{taxon_meta.get('name', f'{fallback_name} {taxon_id}')} ({label})",
                'data': [aggregator(week_values.get(week, [])) for week in weeks],
                'borderColor': taxon_meta.get('color', '#495057'),
                'backgroundColor': taxon_meta.get('color', '#495057'),
                'borderDash': [8, 6],
                'pointRadius': 5,
                'pointHoverRadius': 7,
                'pointStyle': 'rectRot',
                'pointBackgroundColor': '#ffffff',
                'pointBorderColor': taxon_meta.get('color', '#495057'),
                'pointBorderWidth': 3,
                'borderWidth': 3,
                'tension': 0.18,
                'isReference': True,
            }
        )
    return datasets


def _average(values):
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _median(values):
    if not values:
        return None
    return round(float(median(values)), 2)


def _contiguous_week_range(observed_weeks):
    observed_weeks = sorted({week for week in observed_weeks if week})
    if not observed_weeks:
        return []
    return list(range(observed_weeks[0], observed_weeks[-1] + 1))


def _record_auxiliary_divisor(record):
    if record.entry_mode == 'quick' and record.observed_plants_count:
        return record.observed_plants_count
    if record.plant_series_id and record.plant_series:
        return record.plant_series.plants_count or 10
    return record.observed_plants_count or 10


def _auxiliary_chart_datasets(records, weeks):
    scoped_records = [record for record in records if record.week in weeks]
    record_ids = [record.id for record in scoped_records]
    if not record_ids or not weeks:
        return []

    record_weeks_by_series = defaultdict(set)
    records_by_id = {}
    for record in scoped_records:
        records_by_id[record.id] = record
        record_weeks_by_series[record.plant_series_id].add(record.week)

    taxa = list(AuxiliaryTaxon.objects.order_by('display_order', 'name'))
    taxon_by_code = {taxon.code: taxon for taxon in taxa}
    taxon_names = {taxon.id: taxon.name for taxon in taxa}
    taxon_orders = {taxon.id: taxon.display_order or 0 for taxon in taxa}

    totals_by_record_and_taxon = defaultdict(lambda: defaultdict(int))
    records_with_structured_auxiliaries = set()

    quick_rows = list(
        QuickRecordAuxiliaryCount.objects.filter(record_id__in=record_ids)
        .values('record_id', 'taxon_id', 'taxon__name', 'taxon__display_order')
        .annotate(total=Sum('count'))
        .order_by('taxon__display_order', 'taxon__name', 'taxon_id')
    )
    detailed_rows = list(
        LeafAuxiliaryObservation.objects.filter(leaf_observation__record_id__in=record_ids)
        .values(
            'leaf_observation__record_id',
            'taxon_id',
            'taxon__name',
            'taxon__display_order',
        )
        .annotate(total=Sum('count'))
        .order_by('taxon__display_order', 'taxon__name', 'taxon_id')
    )

    for row in quick_rows:
        record_id = row['record_id']
        taxon_id = row['taxon_id']
        totals_by_record_and_taxon[record_id][taxon_id] += row['total'] or 0
        records_with_structured_auxiliaries.add(record_id)
        taxon_names[taxon_id] = row['taxon__name']
        taxon_orders[taxon_id] = row['taxon__display_order'] or 0

    for row in detailed_rows:
        record_id = row['leaf_observation__record_id']
        taxon_id = row['taxon_id']
        totals_by_record_and_taxon[record_id][taxon_id] += row['total'] or 0
        records_with_structured_auxiliaries.add(record_id)
        taxon_names[taxon_id] = row['taxon__name']
        taxon_orders[taxon_id] = row['taxon__display_order'] or 0

    legacy_record_ids = [
        record.id
        for record in scoped_records
        if record.entry_mode != 'quick' and record.id not in records_with_structured_auxiliaries
    ]
    if legacy_record_ids:
        legacy_sums = {code: Sum(code) for code, _ in AUXILIARY_SPECIES}
        for row in (
            LeafObservation.objects.filter(record_id__in=legacy_record_ids)
            .values('record_id')
            .annotate(**legacy_sums)
        ):
            record_id = row['record_id']
            for code, _ in AUXILIARY_SPECIES:
                taxon = taxon_by_code.get(code)
                if not taxon:
                    continue
                total = row.get(code) or 0
                if total:
                    totals_by_record_and_taxon[record_id][taxon.id] += total

    if not totals_by_record_and_taxon:
        return []

    values_by_series_and_taxon = defaultdict(lambda: defaultdict(dict))
    for record_id, totals_by_taxon in totals_by_record_and_taxon.items():
        record = records_by_id.get(record_id)
        if not record:
            continue
        divisor = _record_auxiliary_divisor(record) or 10
        for taxon_id, total in totals_by_taxon.items():
            values_by_series_and_taxon[record.plant_series_id][taxon_id][record.week] = round(total / float(divisor), 2)

    ordered_taxon_ids = sorted(
        {taxon_id for taxon_map in values_by_series_and_taxon.values() for taxon_id in taxon_map.keys()},
        key=lambda taxon_id: (taxon_orders.get(taxon_id, 999), taxon_names.get(taxon_id, '').lower(), taxon_id),
    )
    color_by_taxon_id = {
        taxon_id: AUXILIARY_CHART_COLORS[index % len(AUXILIARY_CHART_COLORS)]
        for index, taxon_id in enumerate(ordered_taxon_ids)
    }

    datasets = []
    for series_id in sorted(values_by_series_and_taxon.keys()):
        for taxon_id in ordered_taxon_ids:
            values = values_by_series_and_taxon[series_id].get(taxon_id)
            if not values:
                continue
            color = color_by_taxon_id[taxon_id]
            datasets.append(
                {
                    'id': f'auxiliary-{series_id}-{taxon_id}',
                    'seriesId': series_id,
                    'taxonId': taxon_id,
                    'label': taxon_names.get(taxon_id, f'Auxiliaire {taxon_id}'),
                    'data': [
                        values.get(week, 0) if week in record_weeks_by_series.get(series_id, set()) else None
                        for week in weeks
                    ],
                    'borderColor': color,
                    'backgroundColor': color,
                }
            )
    return datasets


def _reference_auxiliary_datasets(raw_datasets, weeks, label):
    return _taxon_reference_datasets(raw_datasets, weeks, label, 'Auxiliaire', 'auxiliary')


def _other_pest_chart_datasets(records, weeks):
    record_ids = [record.id for record in records if record.week in weeks]
    if not record_ids or not weeks:
        return []

    record_weeks_by_series = defaultdict(set)
    for record in records:
        if record.week in weeks:
            record_weeks_by_series[record.plant_series_id].add(record.week)

    total_leaves_by_week = {
        (row['record__plant_series_id'], row['record__week']): row['total']
        for row in (
            LeafObservation.objects.filter(record_id__in=record_ids)
            .values('record__plant_series_id', 'record__week')
            .annotate(total=Count('id'))
        )
    }

    rows = list(
        LeafOtherPestObservation.objects.filter(leaf_observation__record_id__in=record_ids)
        .values(
            'leaf_observation__record__plant_series_id',
            'taxon_id',
            'taxon__name',
            'taxon__display_order',
            'leaf_observation__record__week',
        )
        .annotate(touched=Count('leaf_observation_id', distinct=True))
        .order_by('taxon__display_order', 'taxon__name', 'taxon_id')
    )
    quick_rows = list(
        QuickRecordOtherPestCount.objects.filter(record_id__in=record_ids)
        .values(
            'record__plant_series_id',
            'taxon_id',
            'taxon__name',
            'taxon__display_order',
            'record__week',
            'infested_leaves_count',
            'record__observed_leaves_count',
        )
        .order_by('taxon__display_order', 'taxon__name', 'taxon_id')
    )
    if not rows and not quick_rows:
        return []

    values_by_series_and_taxon = defaultdict(lambda: defaultdict(dict))
    taxon_names = {}
    taxon_orders = {}
    for row in rows:
        series_id = row['leaf_observation__record__plant_series_id']
        week = row['leaf_observation__record__week']
        total_leaves = total_leaves_by_week.get((series_id, week)) or 0
        percentage = round((row['touched'] / total_leaves) * 100, 2) if total_leaves else 0
        taxon_id = row['taxon_id']
        values_by_series_and_taxon[series_id][taxon_id][week] = percentage
        taxon_names[taxon_id] = row['taxon__name']
        taxon_orders[taxon_id] = row['taxon__display_order'] or 0
    for row in quick_rows:
        series_id = row['record__plant_series_id']
        week = row['record__week']
        observed_leaves = row['record__observed_leaves_count'] or 0
        percentage = round((row['infested_leaves_count'] / observed_leaves) * 100, 2) if observed_leaves else 0
        taxon_id = row['taxon_id']
        values_by_series_and_taxon[series_id][taxon_id][week] = percentage
        taxon_names[taxon_id] = row['taxon__name']
        taxon_orders[taxon_id] = row['taxon__display_order'] or 0

    datasets = []
    ordered_taxon_ids = sorted({taxon_id for taxon_map in values_by_series_and_taxon.values() for taxon_id in taxon_map.keys()}, key=lambda taxon_id: (taxon_orders.get(taxon_id, 999), taxon_names.get(taxon_id, '').lower(), taxon_id))
    color_by_taxon_id = {
        taxon_id: OTHER_PEST_CHART_COLORS[index % len(OTHER_PEST_CHART_COLORS)]
        for index, taxon_id in enumerate(ordered_taxon_ids)
    }
    for series_id in sorted(values_by_series_and_taxon.keys()):
        for taxon_id in ordered_taxon_ids:
            values = values_by_series_and_taxon[series_id].get(taxon_id)
            if not values:
                continue
            color = color_by_taxon_id[taxon_id]
            datasets.append(
                {
                    'id': f'other-pest-{series_id}-{taxon_id}',
                    'seriesId': series_id,
                    'taxonId': taxon_id,
                    'label': taxon_names.get(taxon_id, f'Ravageur {taxon_id}'),
                    'data': [
                        values.get(week, 0) if week in record_weeks_by_series.get(series_id, set()) else None
                        for week in weeks
                    ],
                    'borderColor': color,
                    'backgroundColor': color,
                }
            )
    return datasets


def _reference_other_pest_datasets(raw_datasets, weeks, label):
    if not raw_datasets or not weeks or not label:
        return []

    values_by_taxon = defaultdict(lambda: defaultdict(list))
    meta_by_taxon = {}
    for dataset in raw_datasets:
        taxon_id = dataset.get('taxonId')
        if not taxon_id:
            continue
        meta_by_taxon.setdefault(
            taxon_id,
            {
                'name': dataset.get('label', f'Ravageur {taxon_id}'),
                'color': dataset.get('borderColor', '#495057'),
            },
        )
        for index, week in enumerate(weeks):
            if index >= len(dataset.get('data', [])):
                continue
            value = dataset['data'][index]
            if value is None:
                continue
            values_by_taxon[taxon_id][week].append(value)

    aggregator = _average if label == 'Moyenne du groupe' else _median
    datasets = []
    for taxon_id, week_values in values_by_taxon.items():
        taxon_meta = meta_by_taxon.get(taxon_id, {})
        datasets.append(
            {
                'id': f'other-pest-reference-{taxon_id}',
                'seriesId': 'reference',
                'taxonId': taxon_id,
                'label': f"{taxon_meta.get('name', f'Ravageur {taxon_id}')} ({label})",
                'data': [aggregator(week_values.get(week, [])) for week in weeks],
                'borderColor': taxon_meta.get('color', '#495057'),
                'backgroundColor': taxon_meta.get('color', '#495057'),
                'borderDash': [8, 6],
                'pointRadius': 5,
                'pointHoverRadius': 7,
                'pointStyle': 'rectRot',
                'pointBackgroundColor': '#ffffff',
                'pointBorderColor': taxon_meta.get('color', '#495057'),
                'pointBorderWidth': 3,
                'borderWidth': 3,
                'tension': 0.18,
                'isReference': True,
            }
        )
    return datasets



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
            'auxiliary_detail_datasets': [],
            'other_pest_datasets': [],
            'action_cards': [],
            'available_years': [],
            'crop_options': [],
            'show_all_series_default': True,
            'comparison_mode': 'median',
            'available_comparison_departments': [],
            'available_comparison_technicians': [],
            'available_comparison_varieties': [],
            'selected_comparison_department': '',
            'selected_comparison_technician_id': None,
            'selected_comparison_variety_id': None,
            'comparison_match_count': 0,
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

    comparison_mode = request.GET.get('comparison_mode')
    if comparison_mode not in {'none', 'average', 'median'}:
        comparison_mode = 'median'

    comparison_base_series = list(
        PlantSeries.objects.filter(
            is_active=True,
            crop_id=selected_crop_id,
            year=selected_year,
        )
        .select_related('variety', 'user', 'user__profile')
        .prefetch_related('user__profile__technician_assignments__technician')
        .order_by('name')
    )

    available_comparison_departments = sorted(
        {
            series.user.profile.department
            for series in comparison_base_series
            if getattr(series.user, 'profile', None) and series.user.profile.department
        }
    )
    selected_comparison_department = (request.GET.get('comparison_department') or '').strip()
    if selected_comparison_department not in available_comparison_departments:
        selected_comparison_department = ''
    comparison_department_series = [
        series
        for series in comparison_base_series
        if not selected_comparison_department or series.user.profile.department == selected_comparison_department
    ]

    available_comparison_technicians_map = {}
    for series in comparison_department_series:
        assignments = series.user.profile.technician_assignments.filter(
            is_active=True,
            technician__profile__license_status='active',
        ).select_related('technician')
        for assignment in assignments:
            available_comparison_technicians_map[assignment.technician.id] = display_user_name(assignment.technician)
    available_comparison_technicians = [
        {'id': technician_id, 'name': technician_name}
        for technician_id, technician_name in sorted(
            available_comparison_technicians_map.items(),
            key=lambda item: item[1].lower(),
        )
    ]
    allowed_comparison_technician_ids = {item['id'] for item in available_comparison_technicians}
    selected_comparison_technician_id = _parse_positive_int(request.GET.get('comparison_technician'))
    if selected_comparison_technician_id not in allowed_comparison_technician_ids:
        selected_comparison_technician_id = None
    comparison_technician_series = [
        series
        for series in comparison_department_series
        if (
            not selected_comparison_technician_id
            or series.user.profile.technician_assignments.filter(
                is_active=True,
                technician_id=selected_comparison_technician_id,
                technician__profile__license_status='active',
            ).exists()
        )
    ]

    available_comparison_varieties_map = {}
    for series in comparison_technician_series:
        available_comparison_varieties_map[series.variety_id] = series.variety.name
    available_comparison_varieties = [
        {'id': variety_id, 'name': variety_name}
        for variety_id, variety_name in sorted(
            available_comparison_varieties_map.items(),
            key=lambda item: item[1].lower(),
        )
    ]
    allowed_comparison_variety_ids = {item['id'] for item in available_comparison_varieties}
    selected_comparison_variety_id = _parse_positive_int(request.GET.get('comparison_variety'))
    if selected_comparison_variety_id not in allowed_comparison_variety_ids:
        selected_comparison_variety_id = None
    comparison_filtered_series = [
        series
        for series in comparison_technician_series
        if not selected_comparison_variety_id or series.variety_id == selected_comparison_variety_id
    ]
    comparison_filtered_series_ids = {series.id for series in comparison_filtered_series}

    records = list(
        effective_user.records.filter(plant_series_id__in=allowed_series_ids, year=selected_year)
        .select_related('plant_series', 'crop_ref')
        .prefetch_related('leaf_observations')
        .order_by('week', 'scouting_date', 'id')
    )

    comparison_records = []
    if comparison_mode != 'none' and comparison_filtered_series_ids:
        comparison_records = list(
            ScoutingRecord.objects.filter(
                plant_series_id__in=comparison_filtered_series_ids,
                year=selected_year,
            )
            .select_related('plant_series', 'crop_ref', 'user', 'user__profile')
            .order_by('week', 'scouting_date', 'id')
        )

    weeks = _contiguous_week_range(
        {
            record.week
            for record in records
            if record.week and record.plant_series_id in displayed_series_ids
        }
        |
        {
            record.week
            for record in comparison_records
            if record.week
        }
    )

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

    comparison_aphid_by_week = defaultdict(list)
    comparison_aux_by_week = defaultdict(list)
    for record in comparison_records:
        if not record.week:
            continue
        comparison_aphid_by_week[record.week].append(round(float(record.aphid_infested_percent), 2))
        comparison_aux_by_week[record.week].append(round(float(record.auxiliaries_per_plant), 2))

    comparison_aggregator = _average if comparison_mode == 'average' else _median
    comparison_label = {
        'average': 'Moyenne du groupe',
        'median': 'Médiane du groupe',
    }.get(comparison_mode)
    if comparison_label and comparison_records:
        aphid_reference_map = {
            week: comparison_aggregator(values)
            for week, values in comparison_aphid_by_week.items()
            if values
        }
        aux_reference_map = {
            week: comparison_aggregator(values)
            for week, values in comparison_aux_by_week.items()
            if values
        }
        if aphid_reference_map:
            aphid_datasets.append(_reference_chart_dataset(weeks, aphid_reference_map, comparison_label))
        if aux_reference_map:
            aux_datasets.append(_reference_chart_dataset(weeks, aux_reference_map, comparison_label))

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
                'series_id': action.plant_series_id,
                'week': action.action_date.isocalendar().week,
                'date': action.action_date.strftime('%d/%m/%Y'),
                'icon_symbol': action.action_type.chart_icon_symbol,
                'color': color_by_series_id.get(action.plant_series_id, _chart_color_for_action_type(action.action_type)),
                'series_color': color_by_series_id.get(action.plant_series_id, _chart_color_for_action_type(action.action_type)),
                'action_color': _chart_color_for_action_type(action.action_type),
                'chart_point_style': action.action_type.resolved_chart_icon,
                'type_name': action.action_type.name,
                'scope': action.get_scope_display(),
                'details': _serialize_action_details(action),
                'series_name': action.plant_series.name,
            }
        )

    displayed_records = [record for record in records if record.plant_series_id in displayed_series_ids]
    auxiliary_detail_datasets = _auxiliary_chart_datasets(displayed_records, weeks)
    if comparison_label and comparison_records:
        comparison_auxiliary_source = _auxiliary_chart_datasets(comparison_records, weeks)
        auxiliary_detail_datasets.extend(
            _reference_auxiliary_datasets(comparison_auxiliary_source, weeks, comparison_label)
        )
    other_pest_datasets = _other_pest_chart_datasets(displayed_records, weeks)
    if comparison_label and comparison_records:
        comparison_other_pest_source = _other_pest_chart_datasets(comparison_records, weeks)
        other_pest_datasets.extend(
            _reference_other_pest_datasets(comparison_other_pest_source, weeks, comparison_label)
        )
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
        'auxiliary_detail_datasets': auxiliary_detail_datasets,
        'other_pest_datasets': other_pest_datasets,
        'action_cards': action_cards,
        'record_count': len(displayed_records),
        'series_count': len(year_series),
        'displayed_series_count': len(displayed_series),
        'latest_week': latest_week,
        'show_all_series_default': not submitted_series_filter,
        'selected_series_ids': selected_series_ids,
        'profile': profile,
        'comparison_mode': comparison_mode,
        'comparison_mode_label': comparison_label,
        'available_comparison_departments': available_comparison_departments,
        'available_comparison_technicians': available_comparison_technicians,
        'available_comparison_varieties': available_comparison_varieties,
        'selected_comparison_department': selected_comparison_department,
        'selected_comparison_technician_id': selected_comparison_technician_id,
        'selected_comparison_variety_id': selected_comparison_variety_id,
        'comparison_match_count': len(comparison_filtered_series),
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
            'auxiliary_detail_datasets': [],
            'other_pest_datasets': [],
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
            'comparison_mode': 'median',
            'comparison_mode_label': 'Médiane du groupe',
            'comparison_match_count': 0,
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

    comparison_mode = request.GET.get('comparison_mode')
    if comparison_mode not in {'none', 'average', 'median'}:
        comparison_mode = 'median'

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
    raw_excluded_variety_ids = [
        parsed_id
        for raw_value in (request.GET.get('excluded_varieties') or '').split(',')
        for parsed_id in [_parse_positive_int(raw_value.strip())]
        if parsed_id
    ]
    excluded_variety_ids = {
        parsed_id
        for parsed_id in raw_excluded_variety_ids
        if parsed_id in allowed_variety_ids
    }
    raw_selected_variety_ids = [
        parsed_id
        for raw_value in request.GET.getlist('varieties')
        for parsed_id in [_parse_positive_int(raw_value)]
        if parsed_id
    ]
    if not variety_filter_submitted:
        selected_variety_ids = set(allowed_variety_ids)
    elif raw_selected_variety_ids or excluded_variety_ids:
        selected_variety_ids = set(allowed_variety_ids) - excluded_variety_ids
    else:
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
    comparison_records = records if comparison_mode != 'none' and allowed_series_ids else []
    weeks = _contiguous_week_range(
        {record.week for record in records if record.week and record.plant_series_id in displayed_series_ids}
        |
        {record.week for record in comparison_records if record.week}
    )

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

    comparison_aphid_by_week = defaultdict(list)
    comparison_aux_by_week = defaultdict(list)
    for record in comparison_records:
        if not record.week:
            continue
        comparison_aphid_by_week[record.week].append(round(float(record.aphid_infested_percent), 2))
        comparison_aux_by_week[record.week].append(round(float(record.auxiliaries_per_plant), 2))

    comparison_aggregator = _average if comparison_mode == 'average' else _median
    comparison_label = {
        'average': 'Moyenne du groupe',
        'median': 'Médiane du groupe',
    }.get(comparison_mode)
    if comparison_label and comparison_records:
        aphid_reference_map = {
            week: comparison_aggregator(values)
            for week, values in comparison_aphid_by_week.items()
            if values
        }
        aux_reference_map = {
            week: comparison_aggregator(values)
            for week, values in comparison_aux_by_week.items()
            if values
        }
        if aphid_reference_map:
            aphid_datasets.append(_reference_chart_dataset(weeks, aphid_reference_map, comparison_label))
        if aux_reference_map:
            aux_datasets.append(_reference_chart_dataset(weeks, aux_reference_map, comparison_label))

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
                'series_id': action.plant_series_id,
                'week': action.action_date.isocalendar().week,
                'date': action.action_date.strftime('%d/%m/%Y'),
                'icon_symbol': action.action_type.chart_icon_symbol,
                'color': color_by_series_id.get(action.plant_series_id, _chart_color_for_action_type(action.action_type)),
                'series_color': color_by_series_id.get(action.plant_series_id, _chart_color_for_action_type(action.action_type)),
                'action_color': _chart_color_for_action_type(action.action_type),
                'chart_point_style': action.action_type.resolved_chart_icon,
                'type_name': action.action_type.name,
                'scope': action.get_scope_display(),
                'details': _serialize_action_details(action),
                'series_name': action.plant_series.name,
                'producer_name': action.user.profile.farm_name or display_user_name(action.user),
            }
        )

    displayed_records = [record for record in records if record.plant_series_id in displayed_series_ids]
    auxiliary_detail_datasets = _auxiliary_chart_datasets(displayed_records, weeks)
    if comparison_label and comparison_records:
        comparison_auxiliary_source = _auxiliary_chart_datasets(comparison_records, weeks)
        auxiliary_detail_datasets.extend(
            _reference_auxiliary_datasets(comparison_auxiliary_source, weeks, comparison_label)
        )
    other_pest_datasets = _other_pest_chart_datasets(displayed_records, weeks)
    if comparison_label and comparison_records:
        comparison_other_pest_source = _other_pest_chart_datasets(comparison_records, weeks)
        other_pest_datasets.extend(
            _reference_other_pest_datasets(comparison_other_pest_source, weeks, comparison_label)
        )
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
        'auxiliary_detail_datasets': auxiliary_detail_datasets,
        'other_pest_datasets': other_pest_datasets,
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
        'comparison_mode': comparison_mode,
        'comparison_mode_label': comparison_label,
        'comparison_match_count': len(variety_filtered_series),
    }
