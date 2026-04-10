from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import (
    ConductType,
    Crop,
    DecisionRule,
    PlantSeries,
    RecommendationDismissReason,
    RecommendationResponse,
    ScoutingRecord,
    ServicePlant,
    UserProfile,
    Variety,
)
from .view_dashboard_support import _technician_dashboard_context


class RecommendationDismissViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='producer',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.user,
            role=UserProfile.ROLE_PRODUCER,
            department='29',
        )
        self.crop = Crop.objects.create(name='Concombre test')
        self.conduct_type = ConductType.objects.create(name='Sous abri test')
        self.variety = Variety.objects.create(
            crop=self.crop,
            name='Loustik',
            created_by=self.user,
        )
        self.series = PlantSeries.objects.create(
            user=self.user,
            name='Serie A',
            crop=self.crop,
            conduct_type=self.conduct_type,
            organic_mode='bio',
            variety=self.variety,
            year=2026,
            plants_count=10,
            leaves_per_plant=3,
        )
        self.record = ScoutingRecord.objects.create(
            user=self.user,
            plant_series=self.series,
            crop_ref=self.crop,
            conduct_type_ref=self.conduct_type,
            variety_ref=self.variety,
            department='29',
            crop=self.crop.name,
            scouting_date=date.fromisocalendar(2026, 14, 2),
            year=2026,
            week=14,
            entry_mode='quick',
            observed_plants_count=10,
            observed_leaves_count=30,
            aphid_infested_leaves_count=15,
            aphid_infested_percent=Decimal('50.00'),
            auxiliary_mode='quick',
            auxiliary_total=0,
        )
        self.rule = DecisionRule.objects.create(
            crop=self.crop,
            title='Intervenir',
            description='Regle de test',
            week_min=1,
            week_max=53,
            infestation_min=Decimal('20.00'),
            infestation_max=Decimal('100.00'),
            auxiliary_min=Decimal('0.00'),
            auxiliary_max=Decimal('1.00'),
            priority=1,
        )
        self.dismiss_reason = RecommendationDismissReason.objects.create(
            label='Observation trop ancienne',
        )

    def test_post_dismiss_creates_response_and_redirects(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('recommendation_dismiss', args=[self.record.id]),
            {
                'dismiss_reason': self.dismiss_reason.id,
                'dismiss_note': 'Le producteur a deja traite le sujet.',
                'next': '/mes-recommandations/',
            },
        )

        self.assertRedirects(response, '/mes-recommandations/')
        recommendation_response = RecommendationResponse.objects.get(
            record=self.record,
            rule=self.rule,
        )
        self.assertEqual(recommendation_response.status, 'dismissed')
        self.assertEqual(recommendation_response.handled_by, self.user)
        self.assertEqual(recommendation_response.dismiss_reason, self.dismiss_reason)
        self.assertEqual(
            recommendation_response.dismiss_note,
            'Le producteur a deja traite le sujet.',
        )


class PlantSeriesServicePlantTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='producer-series',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.user,
            role=UserProfile.ROLE_PRODUCER,
            department='29',
        )
        self.crop = Crop.objects.create(name='Aubergine serie test')
        self.conduct_type = ConductType.objects.create(name='Conduite serie test')
        self.variety = Variety.objects.create(
            crop=self.crop,
            name='Variete serie test',
            created_by=self.user,
        )
        self.service_plant_1 = ServicePlant.objects.create(
            code='basilic-test',
            name='Basilic',
            latin_name='Ocimum basilicum',
        )
        self.service_plant_2 = ServicePlant.objects.create(
            code='tagete-test',
            name='Tagete',
            latin_name='Tagetes patula',
        )

    def _series_form_payload(self, **overrides):
        payload = {
            'name': 'Serie service',
            'crop': str(self.crop.id),
            'conduct_type': str(self.conduct_type.id),
            'organic_mode': 'bio',
            'variety': str(self.variety.id),
            'greenhouse': '',
            'year': '2026',
            'planting_week': '',
            'plants_count': '10',
            'leaves_per_plant': '3',
            'is_active': 'on',
        }
        payload.update(overrides)
        return payload

    def test_create_series_with_service_plants(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('my_series'),
            self._series_form_payload(
                has_service_plants='on',
                service_plants=[str(self.service_plant_1.id), str(self.service_plant_2.id)],
            ),
        )

        self.assertRedirects(response, reverse('my_series'))
        series = PlantSeries.objects.get(user=self.user, name='Serie service')
        self.assertTrue(series.has_service_plants)
        self.assertSetEqual(
            set(series.service_plants.values_list('id', flat=True)),
            {self.service_plant_1.id, self.service_plant_2.id},
        )

    def test_update_series_without_service_plants_clears_existing_selection(self):
        series = PlantSeries.objects.create(
            user=self.user,
            name='Serie a modifier',
            crop=self.crop,
            conduct_type=self.conduct_type,
            organic_mode='bio',
            variety=self.variety,
            year=2026,
            plants_count=10,
            leaves_per_plant=3,
            has_service_plants=True,
        )
        series.service_plants.set([self.service_plant_1, self.service_plant_2])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('my_series'),
            self._series_form_payload(
                series_id=str(series.id),
                name=series.name,
            ),
        )

        self.assertRedirects(response, reverse('my_series'))
        series.refresh_from_db()
        self.assertFalse(series.has_service_plants)
        self.assertEqual(series.service_plants.count(), 0)

    def test_series_requires_service_plant_selection_when_checkbox_is_checked(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('my_series'),
            self._series_form_payload(has_service_plants='on'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choisissez au moins une plante de service.')


class TechnicianDashboardComparisonTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.technician = get_user_model().objects.create_user(
            username='technician-dashboard',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.technician,
            role=UserProfile.ROLE_TECHNICIAN,
            department='29',
        )
        self.crop = Crop.objects.create(name='Tomate dashboard test')
        self.conduct_type = ConductType.objects.create(name='Conduite dashboard test')
        self.variety = Variety.objects.create(
            crop=self.crop,
            name='Variete dashboard test',
            created_by=self.technician,
        )
        self.producer_1 = self._create_producer('producer-dashboard-1', 'GAEC A')
        self.producer_2 = self._create_producer('producer-dashboard-2', 'GAEC B')
        self.series_1 = self._create_series(self.producer_1, 'Serie 1')
        self.series_2 = self._create_series(self.producer_2, 'Serie 2')
        self._create_record(self.producer_1, self.series_1, Decimal('40.00'), 4)
        self._create_record(self.producer_2, self.series_2, Decimal('60.00'), 2)

    def _create_producer(self, username, farm_name):
        producer = get_user_model().objects.create_user(
            username=username,
            password='secret',
        )
        UserProfile.objects.create(
            user=producer,
            role=UserProfile.ROLE_PRODUCER,
            department='29',
            farm_name=farm_name,
            assigned_technician=self.technician,
        )
        return producer

    def _create_series(self, user, name):
        return PlantSeries.objects.create(
            user=user,
            name=name,
            crop=self.crop,
            conduct_type=self.conduct_type,
            organic_mode='bio',
            variety=self.variety,
            year=2026,
            plants_count=10,
            leaves_per_plant=3,
        )

    def _create_record(self, user, series, aphid_percent, auxiliary_total):
        return ScoutingRecord.objects.create(
            user=user,
            plant_series=series,
            crop_ref=self.crop,
            conduct_type_ref=self.conduct_type,
            variety_ref=self.variety,
            department='29',
            crop=self.crop.name,
            scouting_date=date.fromisocalendar(2026, 14, 2),
            year=2026,
            week=14,
            entry_mode='quick',
            observed_plants_count=10,
            observed_leaves_count=30,
            aphid_infested_leaves_count=int((aphid_percent / Decimal('100')) * Decimal('30')),
            aphid_infested_percent=aphid_percent,
            auxiliary_mode='quick',
            auxiliary_total=auxiliary_total,
        )

    def _context(self, **query_params):
        request = self.factory.get(reverse('dashboard'), query_params)
        request.user = self.technician
        request.session = {}
        return _technician_dashboard_context(request)

    def test_average_reference_is_added_for_technician_dashboard(self):
        context = self._context(
            crop=str(self.crop.id),
            year='2026',
            organic_mode='bio',
            comparison_mode='average',
        )

        self.assertEqual(context['comparison_mode'], 'average')
        self.assertEqual(context['comparison_mode_label'], 'Moyenne du groupe')
        self.assertEqual(context['comparison_match_count'], 2)
        aphid_reference = next(dataset for dataset in context['aphid_datasets'] if dataset['label'] == 'Moyenne du groupe')
        aux_reference = next(dataset for dataset in context['aux_datasets'] if dataset['label'] == 'Moyenne du groupe')
        self.assertEqual(aphid_reference['data'], [50.0])
        self.assertEqual(aux_reference['data'], [0.3])

    def test_average_reference_respects_technician_filters(self):
        context = self._context(
            crop=str(self.crop.id),
            year='2026',
            organic_mode='bio',
            comparison_mode='average',
            producer_filter_submitted='1',
            producers=[str(self.producer_1.id)],
        )

        self.assertEqual(context['comparison_match_count'], 1)
        self.assertSetEqual(context['selected_producer_ids'], {self.producer_1.id})
        aphid_reference = next(dataset for dataset in context['aphid_datasets'] if dataset['label'] == 'Moyenne du groupe')
        aux_reference = next(dataset for dataset in context['aux_datasets'] if dataset['label'] == 'Moyenne du groupe')
        self.assertEqual(aphid_reference['data'], [40.0])
        self.assertEqual(aux_reference['data'], [0.4])
