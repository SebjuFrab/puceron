from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

from .api_views import (
    AuxiliaryTaxonViewSet,
    ConductTypeViewSet,
    CropViewSet,
    PlantSeriesViewSet,
    ScoutingRecordViewSet,
    UserProfileViewSet,
    VarietyViewSet,
)

router = DefaultRouter()
router.register(r'records', ScoutingRecordViewSet, basename='api-records')
router.register(r'profiles', UserProfileViewSet, basename='api-profiles')
router.register(r'auxiliaries', AuxiliaryTaxonViewSet, basename='api-auxiliaries')
router.register(r'crops', CropViewSet, basename='api-crops')
router.register(r'conduct-types', ConductTypeViewSet, basename='api-conduct-types')
router.register(r'varieties', VarietyViewSet, basename='api-varieties')
router.register(r'plant-series', PlantSeriesViewSet, basename='api-plant-series')

urlpatterns = [
    path('token/', obtain_auth_token, name='api-token'),
    path('', include(router.urls)),
]
