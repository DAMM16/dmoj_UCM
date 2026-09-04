from django.http import Http404
from django.test import RequestFactory, TestCase

from judge.models import Class, Profile
from judge.models.tests.util import CommonDataMixin, create_user
from judge.utils.users import filter_users_by_shared_class
from judge.views.select2 import GlobalUserSearchSelect2View, UserSearchSelect2View
from judge.views.user import GlobalUserList, UserList, global_user_ranking_redirect, user_ranking_redirect


class ClassFilteredUserListTestCase(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.classmate = create_user(username='classmate')
        cls.other_classmate = create_user(username='other_classmate')
        cls.outsider = create_user(username='outsider')

        cls.first_class = Class.objects.create(
            organization=cls.organizations['open'], name='first-class', slug='first-class',
        )
        cls.second_class = Class.objects.create(
            organization=cls.organizations['open'], name='second-class', slug='second-class',
        )
        cls.first_class.members.add(cls.users['normal'].profile, cls.classmate.profile)
        cls.second_class.members.add(cls.users['normal'].profile, cls.other_classmate.profile)

    def setUp(self):
        self.factory = RequestFactory()

    def test_filter_returns_union_of_shared_classes(self):
        queryset = filter_users_by_shared_class(Profile.objects.all(), self.users['normal'])

        self.assertQuerysetEqual(
            queryset.order_by('user__username'),
            ['classmate', 'normal', 'other_classmate'],
            transform=lambda profile: profile.user.username,
        )

    def test_filter_is_global_for_user_without_class(self):
        queryset = filter_users_by_shared_class(Profile.objects.all(), self.outsider)

        self.assertEqual(queryset.count(), Profile.objects.count())

    def test_leaderboard_uses_class_filter(self):
        request = self.factory.get('/users/')
        request.user = self.users['normal']
        view = UserList()
        view.request = request
        view.order = '-performance_points'

        self.assertSetEqual(
            set(view.get_queryset().values_list('user__username', flat=True)),
            {'normal', 'classmate', 'other_classmate'},
        )

    def test_leaderboard_identifies_single_class_in_heading(self):
        request = self.factory.get('/users/')
        request.user = self.classmate
        request.profile = self.classmate.profile
        view = UserList()
        view.request = request

        heading = str(view.get_content_title())

        self.assertIn('Class first-class in', heading)
        self.assertIn(self.organizations['open'].get_absolute_url(), heading)

    def test_leaderboard_search_uses_class_filter(self):
        request = self.factory.get('/widgets/select2/user_search', {'term': 'outsider'})
        request.user = self.users['normal']
        view = UserSearchSelect2View()
        view.request = request
        view.term = 'outsider'

        self.assertFalse(view.get_queryset().exists())

    def test_global_leaderboard_includes_users_outside_class(self):
        request = self.factory.get('/users/global/')
        request.user = self.users['normal']
        view = GlobalUserList()
        view.request = request
        view.order = '-performance_points'

        self.assertIn('outsider', view.get_queryset().values_list('user__username', flat=True))

    def test_global_leaderboard_search_is_not_class_filtered(self):
        request = self.factory.get('/widgets/select2/global_user_search', {'term': 'outsider'})
        request.user = self.users['normal']
        view = GlobalUserSearchSelect2View()
        view.request = request
        view.term = 'outsider'

        self.assertTrue(view.get_queryset().exists())

    def test_ranking_redirect_rejects_user_outside_class(self):
        request = self.factory.get('/users/find', {'handle': 'outsider'})
        request.user = self.users['normal']

        with self.assertRaises(Http404):
            user_ranking_redirect(request)

    def test_global_ranking_redirect_accepts_user_outside_class(self):
        request = self.factory.get('/users/global/find', {'handle': 'outsider'})
        request.user = self.users['normal']

        response = global_user_ranking_redirect(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/users/global/', response.url)
