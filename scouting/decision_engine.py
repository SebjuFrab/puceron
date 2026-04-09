from decimal import Decimal

from .models import DecisionRule, RecommendationResponse


NO_RULE_MESSAGE = 'Situation anormale, vous pouvez appeler votre technicien.'


def _safe_decimal(value):
    if value is None:
        return Decimal('0')
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def compute_record_indicators(record):
    if record.entry_mode == 'quick':
        observed_leaves_count = record.observed_leaves_count or 0
        infested_leaves_count = record.aphid_infested_leaves_count or 0
        plants_count = record.observed_plants_count or 10
    else:
        leaves = list(record.leaf_observations.all())
        observed_leaves_count = len(leaves)
        infested_leaves_count = sum(1 for leaf in leaves if leaf.aphid_present)
        plants_count = (
            record.plant_series.plants_count
            if record.plant_series_id and record.plant_series and record.plant_series.plants_count
            else 10
        )
    total_auxiliaries = record.auxiliary_total or 0
    crop = record.crop_ref or (record.plant_series.crop if record.plant_series_id and record.plant_series else None)
    metric_key = crop.decision_aux_metric if crop else 'per_plant'

    if metric_key == 'per_observed_leaf':
        denominator = observed_leaves_count or 1
        metric_label = 'Auxiliaires / feuille observee'
    elif metric_key == 'per_infested_leaf':
        denominator = infested_leaves_count or 1
        metric_label = 'Auxiliaires / feuille infestee'
    else:
        denominator = plants_count or 1
        metric_label = 'Auxiliaires / plant'

    metric_value = _safe_decimal(total_auxiliaries) / Decimal(str(denominator))
    return {
        'week': record.week,
        'infestation_percent': _safe_decimal(record.aphid_infested_percent),
        'auxiliary_metric_key': metric_key,
        'auxiliary_metric_label': metric_label,
        'auxiliary_metric_value': metric_value.quantize(Decimal('0.01')),
        'total_auxiliaries': total_auxiliaries,
        'plants_count': plants_count,
        'observed_leaves_count': observed_leaves_count,
        'infested_leaves_count': infested_leaves_count,
    }


def _rule_matches(rule, indicators):
    week = indicators['week']
    infestation = indicators['infestation_percent']
    auxiliary_value = indicators['auxiliary_metric_value']

    week_min = rule.week_min if rule.week_min is not None else 1
    week_max = rule.week_max if rule.week_max is not None else 53
    if week < week_min or week > week_max:
        return False

    infestation_min = rule.infestation_min if rule.infestation_min is not None else Decimal('0')
    if infestation < infestation_min:
        return False
    if rule.infestation_max is not None and infestation >= rule.infestation_max:
        return False

    auxiliary_min = rule.auxiliary_min if rule.auxiliary_min is not None else Decimal('0')
    if auxiliary_value < auxiliary_min:
        return False
    if rule.auxiliary_max is not None and auxiliary_value >= rule.auxiliary_max:
        return False
    return True


def evaluate_record_recommendation(record):
    indicators = compute_record_indicators(record)
    crop = record.crop_ref or (record.plant_series.crop if record.plant_series_id and record.plant_series else None)
    if crop is None:
        return {
            'status': 'no_rule',
            'message': NO_RULE_MESSAGE,
            'rule': None,
            'levers': [],
            'indicators': indicators,
            'response': None,
            'is_open': False,
        }

    candidate_rules = (
        DecisionRule.objects.filter(crop=crop, is_active=True)
        .prefetch_related('levers__action_type', 'levers__molecule', 'levers__auxiliary_taxon')
        .order_by('priority', 'id')
    )
    for rule in candidate_rules:
        if _rule_matches(rule, indicators):
            levers = [lever for lever in rule.levers.all() if lever.is_active]
            response = (
                RecommendationResponse.objects.select_related('dismiss_reason', 'lever', 'action')
                .filter(record=record, rule=rule)
                .first()
            )
            return {
                'status': 'matched',
                'message': None,
                'rule': rule,
                'levers': levers,
                'indicators': indicators,
                'response': response,
                'is_open': response is None,
            }

    return {
        'status': 'no_rule',
        'message': NO_RULE_MESSAGE,
        'rule': None,
        'levers': [],
        'indicators': indicators,
        'response': None,
        'is_open': False,
    }
