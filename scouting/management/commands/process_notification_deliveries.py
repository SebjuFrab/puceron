import time

from django.core.management.base import BaseCommand

from scouting.notification_service import process_pending_email_deliveries


class Command(BaseCommand):
    help = 'Traite la file des notifications email PUCERON.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true', help='Reste actif et surveille la file.')
        parser.add_argument('--sleep', type=float, default=5.0, help='Pause en secondes lorsque la file est vide.')
        parser.add_argument('--batch-size', type=int, default=50, help='Nombre maximal de livraisons par cycle.')

    def handle(self, *args, **options):
        watch = options['watch']
        sleep_seconds = max(options['sleep'], 0.5)
        batch_size = max(options['batch_size'], 1)

        while True:
            processed = process_pending_email_deliveries(limit=batch_size)
            if processed:
                self.stdout.write(self.style.SUCCESS(f'{processed} notification(s) traitée(s).'))
            if not watch:
                return
            if not processed:
                time.sleep(sleep_seconds)
