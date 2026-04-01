from django.db.models import Q
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .api_serializers import (
    AuxiliaryTaxonSerializer,
    ConductTypeSerializer,
    CropSerializer,
    PlantSeriesSerializer,
    ScoutingRecordSerializer,
    UserProfileSerializer,
    VarietySerializer,
)
from .models import AuxiliaryTaxon, ConductType, Crop, PlantSeries, ScoutingRecord, UserProfile, Variety


def _technician_visibility_q(user, profile_prefix='user__profile'):
    profile = UserProfile.objects.get_or_create(user=user)[0]
    assigned_lookup = f'{profile_prefix}__assigned_technician' if profile_prefix else 'assigned_technician'
    department_lookup = f'{profile_prefix}__department' if profile_prefix else 'department'
    query = Q(**{assigned_lookup: user})
    if profile.department:
        query |= Q(**{f'{assigned_lookup}__isnull': True, department_lookup: profile.department})
    return query


class IsAdminOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return request.user and request.user.is_staff


class UserProfileViewSet(viewsets.ModelViewSet):
    serializer_class = UserProfileSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return UserProfile.objects.select_related('user').all()
        return UserProfile.objects.select_related('user').filter(user=user)

    def get_permissions(self):
        if self.action in ['destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    @action(detail=False, methods=['get'])
    def me(self, request):
        profile = UserProfile.objects.get_or_create(user=request.user)[0]
        serializer = self.get_serializer(profile)
        return Response(serializer.data)


class AuxiliaryTaxonViewSet(viewsets.ModelViewSet):
    queryset = AuxiliaryTaxon.objects.all()
    serializer_class = AuxiliaryTaxonSerializer
    permission_classes = [IsAdminOrReadOnly]


class CropViewSet(viewsets.ModelViewSet):
    queryset = Crop.objects.all()
    serializer_class = CropSerializer
    permission_classes = [IsAdminOrReadOnly]


class ConductTypeViewSet(viewsets.ModelViewSet):
    queryset = ConductType.objects.all()
    serializer_class = ConductTypeSerializer
    permission_classes = [IsAdminOrReadOnly]


class VarietyViewSet(viewsets.ModelViewSet):
    serializer_class = VarietySerializer

    def get_queryset(self):
        qs = Variety.objects.all()
        crop = self.request.query_params.get('crop')
        if crop:
            qs = qs.filter(crop_id=crop)
        return qs

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, is_active=True)


class PlantSeriesViewSet(viewsets.ModelViewSet):
    serializer_class = PlantSeriesSerializer

    def get_queryset(self):
        user = self.request.user
        qs = PlantSeries.objects.select_related('crop', 'conduct_type', 'variety')
        if user.is_superuser:
            return qs
        profile = UserProfile.objects.get_or_create(user=user)[0]
        if profile.role == UserProfile.ROLE_TECHNICIAN:
            return qs.filter(_technician_visibility_q(user, 'user__profile'))
        return qs.filter(user=user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ScoutingRecordViewSet(viewsets.ModelViewSet):
    serializer_class = ScoutingRecordSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = ScoutingRecord.objects.select_related('user').prefetch_related(
            'leaf_observations__auxiliary_observations__taxon'
        )

        year = self.request.query_params.get('year')
        crop = self.request.query_params.get('crop')
        department = self.request.query_params.get('department')

        if not user.is_superuser:
            profile = UserProfile.objects.get_or_create(user=user)[0]
            if profile.role == UserProfile.ROLE_TECHNICIAN:
                queryset = queryset.filter(_technician_visibility_q(user))
            else:
                queryset = queryset.filter(user=user)

        if year:
            queryset = queryset.filter(year=year)
        if crop:
            queryset = queryset.filter(crop=crop)
        if department:
            queryset = queryset.filter(department=department)
        return queryset

    def perform_create(self, serializer):
        serializer.save()
