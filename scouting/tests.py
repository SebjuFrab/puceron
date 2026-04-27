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
    ProducerTechnicianAssignment,
    RecommendationDismissReason,
    RecommendationResponse,
    ScoutingRecord,
    ServicePlant,
    TechnicianCoFollowRequest,
    TechnicianCoFollowRequestItem,
    UserProfile,
    Variety,
)
from .view_dashboard_support import _technician_dashboard_context


class RecommendationDismissViewTests(TestCase):
    def setUp(self):
        self.technician = get_user_model().objects.create_user(
            username='technician-recommendation',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.technician,
            role=UserProfile.ROLE_TECHNICIAN,
            license_status=UserProfile.LICENSE_STATUS_ACTIVE,
            department='29',
        )
        self.user = get_user_model().objects.create_user(
            username='producer',
            password='secret',
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            role=UserProfile.ROLE_PRODUCER,
            department='29',
        )
        ProducerTechnicianAssignment.objects.create(
            producer_profile=self.profile,
            technician=self.technician,
            is_active=True,
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
        self.technician = get_user_model().objects.create_user(
            username='technician-series',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.technician,
            role=UserProfile.ROLE_TECHNICIAN,
            license_status=UserProfile.LICENSE_STATUS_ACTIVE,
            department='29',
        )
        self.user = get_user_model().objects.create_user(
            username='producer-series',
            password='secret',
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            role=UserProfile.ROLE_PRODUCER,
            department='29',
        )
        ProducerTechnicianAssignment.objects.create(
            producer_profile=self.profile,
            technician=self.technician,
            is_active=True,
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
        profile = UserProfile.objects.create(
            user=producer,
            role=UserProfile.ROLE_PRODUCER,
            department='29',
            farm_name=farm_name,
            assigned_technician=self.technician,
        )
        ProducerTechnicianAssignment.objects.create(
            producer_profile=profile,
            technician=self.technician,
            is_active=True,
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

    def test_producer_selection_reselects_all_varieties_and_series_when_none_are_explicitly_unchecked(self):
        context = self._context(
            crop=str(self.crop.id),
            year='2026',
            organic_mode='bio',
            producer_filter_submitted='1',
            producers=[str(self.producer_1.id)],
            variety_filter_submitted='1',
            series_filter_submitted='0',
        )

        self.assertSetEqual(context['selected_producer_ids'], {self.producer_1.id})
        self.assertSetEqual(context['selected_variety_ids'], {self.variety.id})
        self.assertSetEqual(context['selected_series_ids'], {self.series_1.id})
        self.assertEqual(context['displayed_series_count'], 1)

    def test_producer_selection_keeps_explicitly_unchecked_varieties_out_of_series_reset(self):
        other_variety = Variety.objects.create(
            crop=self.crop,
            name='Variete dashboard secondaire',
            created_by=self.technician,
        )
        other_series = PlantSeries.objects.create(
            user=self.producer_1,
            name='Serie 1 bis',
            crop=self.crop,
            conduct_type=self.conduct_type,
            organic_mode='bio',
            variety=other_variety,
            year=2026,
            plants_count=10,
            leaves_per_plant=3,
        )

        context = self._context(
            crop=str(self.crop.id),
            year='2026',
            organic_mode='bio',
            producer_filter_submitted='1',
            producers=[str(self.producer_1.id)],
            variety_filter_submitted='1',
            excluded_varieties=str(other_variety.id),
            series_filter_submitted='0',
        )

        self.assertSetEqual(context['selected_producer_ids'], {self.producer_1.id})
        self.assertSetEqual(context['selected_variety_ids'], {self.variety.id})
        self.assertSetEqual(context['selected_series_ids'], {self.series_1.id})
        self.assertNotIn(other_series.id, context['selected_series_ids'])
        self.assertEqual(context['displayed_series_count'], 1)


class TechnicianCoFollowWorkflowTests(TestCase):
    def setUp(self):
        self.source_technician = get_user_model().objects.create_user(
            username='tech-source',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.source_technician,
            role=UserProfile.ROLE_TECHNICIAN,
            license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        )

        self.target_technician = get_user_model().objects.create_user(
            username='tech-target',
            password='secret',
        )
        UserProfile.objects.create(
            user=self.target_technician,
            role=UserProfile.ROLE_TECHNICIAN,
            license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        )

        self.producer_profile_1 = self._create_producer_profile('producer-cofollow-1', 'GAEC CoFollow 1')
        self.producer_profile_2 = self._create_producer_profile('producer-cofollow-2', 'GAEC CoFollow 2')

        self.request_obj = TechnicianCoFollowRequest.objects.create(
            source_technician=self.source_technician,
            target_technician=self.target_technician,
            message='Pouvez-vous reprendre une partie du suivi ?',
            status=TechnicianCoFollowRequest.STATUS_PENDING,
        )
        TechnicianCoFollowRequestItem.objects.create(
            request=self.request_obj,
            producer_profile=self.producer_profile_1,
            decision=TechnicianCoFollowRequestItem.DECISION_PENDING,
        )
        TechnicianCoFollowRequestItem.objects.create(
            request=self.request_obj,
            producer_profile=self.producer_profile_2,
            decision=TechnicianCoFollowRequestItem.DECISION_PENDING,
        )

    def _create_producer_profile(self, username, farm_name):
        producer = get_user_model().objects.create_user(
            username=username,
            password='secret',
        )
        profile = UserProfile.objects.create(
            user=producer,
            role=UserProfile.ROLE_PRODUCER,
            farm_name=farm_name,
            assigned_technician=self.source_technician,
        )
        ProducerTechnicianAssignment.objects.create(
            producer_profile=profile,
            technician=self.source_technician,
            is_active=True,
        )
        return profile

    def test_target_can_process_partial_acceptance(self):
        self.client.force_login(self.target_technician)

        response = self.client.post(
            reverse('technician_cofollow_review', args=[self.request_obj.id]),
            {
                'accepted_producers': [str(self.producer_profile_1.id)],
            },
        )

        self.assertRedirects(response, reverse('technician_records'))
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, TechnicianCoFollowRequest.STATUS_PARTIAL)
        self.assertIsNotNone(self.request_obj.responded_at)

        item_1 = TechnicianCoFollowRequestItem.objects.get(
            request=self.request_obj,
            producer_profile=self.producer_profile_1,
        )
        item_2 = TechnicianCoFollowRequestItem.objects.get(
            request=self.request_obj,
            producer_profile=self.producer_profile_2,
        )
        self.assertEqual(item_1.decision, TechnicianCoFollowRequestItem.DECISION_ACCEPTED)
        self.assertEqual(item_2.decision, TechnicianCoFollowRequestItem.DECISION_REJECTED)

        self.assertTrue(
            ProducerTechnicianAssignment.objects.filter(
                producer_profile=self.producer_profile_1,
                technician=self.target_technician,
                is_active=True,
            ).exists()
        )
        self.assertFalse(
            ProducerTechnicianAssignment.objects.filter(
                producer_profile=self.producer_profile_2,
                technician=self.target_technician,
                is_active=True,
            ).exists()
        )

    def test_management_page_shows_pending_requests_and_metrics(self):
        self.client.force_login(self.target_technician)

        response = self.client.get(reverse('technician_producer_management'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Demandes de co-suivi en attente')
        self.assertContains(response, 'GAEC CoFollow 1')
        self.assertContains(response, 'GAEC CoFollow 2')
        self.assertContains(response, 'Demande de')

    def test_technician_records_view_does_not_duplicate_records_with_multi_assignment(self):
        crop = Crop.objects.create(name='Concombre dedup test')
        conduct_type = ConductType.objects.create(name='Conduite dedup test')
        variety = Variety.objects.create(
            crop=crop,
            name='Variete dedup test',
            created_by=self.source_technician,
        )
        series = PlantSeries.objects.create(
            user=self.producer_profile_1.user,
            name='Serie dedup',
            crop=crop,
            conduct_type=conduct_type,
            organic_mode='bio',
            variety=variety,
            year=2026,
            plants_count=10,
            leaves_per_plant=3,
        )
        record = ScoutingRecord.objects.create(
            user=self.producer_profile_1.user,
            plant_series=series,
            crop_ref=crop,
            conduct_type_ref=conduct_type,
            variety_ref=variety,
            department='29',
            crop=crop.name,
            scouting_date=date.fromisoformat('2026-04-23'),
            year=2026,
            week=17,
            entry_mode='quick',
            observed_plants_count=10,
            observed_leaves_count=30,
            aphid_infested_leaves_count=0,
            aphid_infested_percent=Decimal('0.00'),
            auxiliary_mode='quick',
            auxiliary_total=6,
        )

        ProducerTechnicianAssignment.objects.get_or_create(
            producer_profile=self.producer_profile_1,
            technician=self.target_technician,
            is_active=True,
            defaults={'created_by': self.source_technician},
        )
        self.client.force_login(self.target_technician)

        response = self.client.get(
            reverse('technician_records'),
            {'producer': self.producer_profile_1.user_id},
        )

        self.assertEqual(response.status_code, 200)
        records = response.context['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, record.id)


class SuperAdminTechnicianManagementTests(TestCase):
    def setUp(self):
        self.super_admin = get_user_model().objects.create_superuser(
            username='root-tech-mgmt',
            email='root-tech-mgmt@example.test',
            password='secret',
        )

        self.technician_user = get_user_model().objects.create_user(
            username='tech-mgmt',
            first_name='Jean',
            last_name='Dupont',
            password='secret',
        )
        self.technician_profile = UserProfile.objects.create(
            user=self.technician_user,
            role=UserProfile.ROLE_TECHNICIAN,
            license_status=UserProfile.LICENSE_STATUS_ACTIVE,
        )

        producer = get_user_model().objects.create_user(
            username='producer-tech-mgmt',
            password='secret',
        )
        producer_profile = UserProfile.objects.create(
            user=producer,
            role=UserProfile.ROLE_PRODUCER,
            farm_name='GAEC Test',
            assigned_technician=self.technician_user,
        )
        ProducerTechnicianAssignment.objects.create(
            producer_profile=producer_profile,
            technician=self.technician_user,
            is_active=True,
        )

    def test_superadmin_page_displays_requested_columns(self):
        self.client.force_login(self.super_admin)

        response = self.client.get(reverse('superadmin_technician_management'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gestion techniciens')
        self.assertContains(response, 'Nom')
        self.assertContains(response, 'Prenom')
        self.assertContains(response, 'Structure')
        self.assertContains(response, 'Actif')
        self.assertContains(response, 'Nb prod')
        self.assertContains(response, 'Dupont')
        self.assertContains(response, 'Jean')
        self.assertContains(response, '1')
