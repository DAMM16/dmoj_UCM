import errno
import os

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.cache.utils import make_template_fragment_key
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q
from django.utils.translation import gettext as _

from .caching import finished_submission
from .models import BlogPost, Comment, Contest, ContestSubmission, EFFECTIVE_MATH_ENGINES, Judge, Language, License, \
    MiscConfig, Organization, Problem, Profile, Submission, WebAuthnCredential

# --- NUEVO: crear Profile al crear User ---
from django.contrib.auth import get_user_model  # NUEVO
# ya tienes: from .models import ... Profile, ...

User = get_user_model()

@receiver(post_save, sender=User)
def create_profile_for_new_user(sender, instance, created, **kwargs):
    if created:
        # Crea el Profile solo si no existe (idempotente)
        Profile.objects.get_or_create(user=instance)
# --- FIN NUEVO ---

def get_pdf_path(basename):
    return os.path.join(settings.DMOJ_PDF_PROBLEM_CACHE, basename)


def unlink_if_exists(file):
    try:
        os.unlink(file)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


@receiver(post_save, sender=Problem)
def problem_update(sender, instance, **kwargs):
    if kwargs.get('created'):
        send_new_problem_email(instance.pk)

    if hasattr(instance, '_updating_stats_only'):
        return

    cache.delete_many([
        make_template_fragment_key('submission_problem', (instance.id,)),
        make_template_fragment_key('problem_feed', (instance.id,)),
        'problem_tls:%s' % instance.id, 'problem_mls:%s' % instance.id,
    ])
    cache.delete_many([make_template_fragment_key('problem_html', (instance.id, engine, lang))
                       for lang, _ in settings.LANGUAGES for engine in EFFECTIVE_MATH_ENGINES])
    cache.delete_many([make_template_fragment_key('problem_authors', (instance.id, lang))
                       for lang, _ in settings.LANGUAGES])
    cache.delete_many(['generated-meta-problem:%s:%d' % (lang, instance.id) for lang, _ in settings.LANGUAGES])

    for lang, _ in settings.LANGUAGES:
        unlink_if_exists(get_pdf_path('%s.%s.pdf' % (instance.code, lang)))

def build_problem_url(problem):
    base = getattr(settings, "PUBLIC_SITE_URL", None)
    if base:
        return f"{base}{problem.get_absolute_url()}"
    scheme = "https" if settings.DMOJ_SSL else "http"
    domain = Site.objects.get_current().domain
    return f"{scheme}://{domain}{problem.get_absolute_url()}"


def send_new_problem_email(problem_id, *, only_to=None, dry_run=False):
    """
    only_to: lista de correos para pruebas (evita enviar a todos).
    dry_run: si True, NO envía, solo imprime subject/message/recipients.
    """
    try:
        problem = Problem.objects.prefetch_related('organizations').get(pk=problem_id)
    except Problem.DoesNotExist:
        print("Problem no existe:", problem_id)
        return
    if not problem.is_public:
        print("Problem no es público; no se envía.")
        return
    User = get_user_model()
    recipients_qs = User.objects.filter(is_active=True).exclude(email='')
    organizations = problem.organizations.all()
    org_names = list(organizations.values_list('name', flat=True))
    if organizations.exists():
        org_filter = Q(profile__organizations__in=organizations)
        # perm_filter = (
        #     Q(is_superuser=True)
        #     | Q(user_permissions__codename='see_organization_problem')
        #     | Q(groups__permissions__codename='see_organization_problem')
        # )
        no_staff_filter = Q(is_staff=False)
        no_superuser_filter = Q(is_superuser=False)
        recipients_qs = recipients_qs.filter(org_filter & no_staff_filter & no_superuser_filter)
    else:
        return
    recipients = list(recipients_qs.values_list('email', flat=True).distinct())
    # Forzar destinatarios de prueba si se indicó
    if only_to is not None:
        recipients = list(only_to)
    if not recipients:
        print("Sin destinatarios.")
        return
    org_line = ("Organización(es): " + ", ".join(org_names)) if org_names else "Organización(es): (Público general)"
    subject = _('[SIPU] Nuevo ejercicio disponible: %(problem_name)s') % {'problem_name': problem.name}
    problem_url = build_problem_url(problem)
    message = _(
        'Se ha añadido un nuevo ejercicio en la plataforma: %(problem_name)s (%(problem_code)s).\n'
        '\n'
        'Puedes consultarlo aquí: %(problem_url)s'
    ) % {
        'problem_name': problem.name,
        'problem_code': problem.code,
        'problem_url': problem_url,
    }
    if dry_run:
        print("SUBJECT:", subject)
        print("RECIPIENTS:", recipients)
        print("MESSAGE:\n", message)
        return
    return send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)





@receiver(post_save, sender=Profile)
def profile_update(sender, instance, **kwargs):
    if hasattr(instance, '_updating_stats_only'):
        return

    cache.delete_many([make_template_fragment_key('user_about', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES] +
                      [make_template_fragment_key('org_member_count', (org_id,))
                       for org_id in instance.organizations.values_list('id', flat=True)])


@receiver(post_delete, sender=WebAuthnCredential)
def webauthn_delete(sender, instance, **kwargs):
    profile = instance.user
    if profile.webauthn_credentials.count() == 0:
        profile.is_webauthn_enabled = False
        profile.save(update_fields=['is_webauthn_enabled'])


@receiver(post_save, sender=Contest)
def contest_update(sender, instance, **kwargs):
    if hasattr(instance, '_updating_stats_only'):
        return

    cache.delete_many(['generated-meta-contest:%d' % instance.id] +
                      [make_template_fragment_key('contest_html', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


@receiver(post_save, sender=License)
def license_update(sender, instance, **kwargs):
    cache.delete(make_template_fragment_key('license_html', (instance.id,)))


@receiver(post_save, sender=Language)
def language_update(sender, instance, **kwargs):
    cache.delete_many([make_template_fragment_key('language_html', (instance.id,)),
                       'lang:cn_map'])


@receiver(post_save, sender=Judge)
def judge_update(sender, instance, **kwargs):
    cache.delete(make_template_fragment_key('judge_html', (instance.id,)))


@receiver(post_save, sender=Comment)
def comment_update(sender, instance, **kwargs):
    cache.delete('comment_feed:%d' % instance.id)


@receiver(post_save, sender=BlogPost)
def post_update(sender, instance, **kwargs):
    cache.delete_many([
        make_template_fragment_key('post_summary', (instance.id,)),
        'blog_slug:%d' % instance.id,
        'blog_feed:%d' % instance.id,
    ])
    cache.delete_many([make_template_fragment_key('post_content', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


@receiver(post_delete, sender=Submission)
def submission_delete(sender, instance, **kwargs):
    finished_submission(instance)
    instance.user._updating_stats_only = True
    instance.user.calculate_points()
    instance.problem._updating_stats_only = True
    instance.problem.update_stats()


@receiver(post_delete, sender=ContestSubmission)
def contest_submission_delete(sender, instance, **kwargs):
    participation = instance.participation
    participation.recompute_results()
    Submission.objects.filter(id=instance.submission_id).update(contest_object=None)


@receiver(post_save, sender=Organization)
def organization_update(sender, instance, **kwargs):
    cache.delete_many([make_template_fragment_key('organization_html', (instance.id, engine))
                       for engine in EFFECTIVE_MATH_ENGINES])


_misc_config_i18n = [code for code, _ in settings.LANGUAGES]
_misc_config_i18n.append('')


def misc_config_cache_delete(key):
    cache.delete_many(['misc_config:%s:%s:%s' % (domain, lang, key.split('.')[0])
                       for lang in _misc_config_i18n
                       for domain in Site.objects.values_list('domain', flat=True)])


@receiver(pre_save, sender=MiscConfig)
def misc_config_pre_save(sender, instance, **kwargs):
    try:
        old_key = MiscConfig.objects.filter(id=instance.id).values_list('key').get()[0]
    except MiscConfig.DoesNotExist:
        old_key = None
    instance._old_key = old_key


@receiver(post_save, sender=MiscConfig)
def misc_config_update(sender, instance, **kwargs):
    misc_config_cache_delete(instance.key)
    if instance._old_key is not None and instance._old_key != instance.key:
        misc_config_cache_delete(instance._old_key)


@receiver(post_delete, sender=MiscConfig)
def misc_config_delete(sender, instance, **kwargs):
    misc_config_cache_delete(instance.key)


@receiver(post_save, sender=ContestSubmission)
def contest_submission_update(sender, instance, **kwargs):
    Submission.objects.filter(id=instance.submission_id).update(contest_object_id=instance.participation.contest_id)
