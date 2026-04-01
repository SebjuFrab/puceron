# Generated manually to migrate information pages into Wagtail CMS.

from django.db import migrations
from django.utils.html import escape


DEFAULT_PAGE_TITLES = {
    'protocol': 'Protocole',
    'techniques': 'Techniques de lutte',
    'auxiliaries': 'Auxiliaires',
}


def _plain_text_to_richtext(value):
    text = (value or '').strip()
    if not text:
        return ''
    if '<' in text and '>' in text:
        return text
    paragraphs = [segment.strip() for segment in text.replace('\r\n', '\n').split('\n\n') if segment.strip()]
    if not paragraphs:
        return ''
    return ''.join(f'<p>{escape(paragraph).replace("\n", "<br>")}</p>' for paragraph in paragraphs)


def migrate_info_pages_to_wagtail(apps, schema_editor):
    from wagtail.documents.models import Document
    from wagtail.models import Page, Site
    from scouting.models import InfoContentPage, InfoContentPageResource, InfoIndexPage, InfoPage

    site = Site.objects.order_by('id').first()
    if site:
        root_page = Page.objects.get(pk=site.root_page_id).specific
    else:
        root_page = Page.get_first_root_node()

    index_page = InfoIndexPage.objects.first()
    if index_page is None:
        index_page = InfoIndexPage(
            title='Informations',
            slug='infos',
            intro='<p>Pages d&apos;information sur le protocole, les auxiliaires et les techniques de lutte.</p>',
        )
        root_page.add_child(instance=index_page)
        index_page.save_revision().publish()

    legacy_pages = {page.page_key: page for page in InfoPage.objects.prefetch_related('resources').all()}
    ordered_keys = ['protocol', 'techniques', 'auxiliaries']

    for page_key in ordered_keys:
        legacy_page = legacy_pages.get(page_key)
        content_page = InfoContentPage.objects.filter(page_key=page_key).first()
        if content_page is None:
            content_page = InfoContentPage(
                title=legacy_page.title if legacy_page else DEFAULT_PAGE_TITLES[page_key],
                slug=page_key,
                page_key=page_key,
                intro=_plain_text_to_richtext(legacy_page.intro if legacy_page else 'Contenu a completer dans le CMS.'),
                body=_plain_text_to_richtext(legacy_page.content if legacy_page else ''),
            )
            index_page.add_child(instance=content_page)
            if legacy_page is None or legacy_page.is_published:
                content_page.save_revision().publish()
            else:
                content_page.save_revision()

        if not legacy_page:
            continue

        for position, legacy_resource in enumerate(legacy_page.resources.all(), start=1):
            if InfoContentPageResource.objects.filter(
                page=content_page,
                title=legacy_resource.title,
                external_url=legacy_resource.external_url,
            ).exists():
                continue

            document = None
            if legacy_resource.file:
                document = Document.objects.create(
                    title=legacy_resource.title or legacy_resource.file.name.rsplit('/', 1)[-1],
                    file=legacy_resource.file.name,
                )

            InfoContentPageResource.objects.create(
                page=content_page,
                sort_order=position,
                title=legacy_resource.title,
                description=legacy_resource.description,
                document=document,
                external_url=legacy_resource.external_url,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('scouting', '0013_infocontentpage_infoindexpage_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_info_pages_to_wagtail, migrations.RunPython.noop),
    ]
