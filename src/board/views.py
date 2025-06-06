import os
import sys
import time
import logging
import tempfile
import subprocess

from django import shortcuts
from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponse
from django.db import transaction
from django.urls import reverse, reverse_lazy
from django.utils.safestring import mark_safe
from django.views import View
from django.views.generic.edit import FormView, FormMixin
from django.views.generic import DetailView
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from django_otp import devices_for_user

from accounts.models import Account
from accounts.users import list_all_users_by_date

from base.mixins import StaffRequiredMixin

from billing import forms as billing_forms, payments
from billing import orders

from board import forms as board_forms
from board.models import CSVFileSync

from epp import rpc_error

from zen import zmaster, zdomains


logger = logging.getLogger(__name__)


class BalanceAdjustmentView(StaffRequiredMixin, FormView, FormMixin):
    template_name = 'board/balance_adjustment.html'
    form_class = board_forms.BalanceAdjustmentForm
    success_url = reverse_lazy('balance_adjustment')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        admin_payments = payments.list_all_payments_of_specific_method(method='pay_by_admin')
        context['payments'] = admin_payments.order_by('-finished_at')
        return context

    @transaction.atomic
    def form_valid(self, form):
        email = form.cleaned_data.get('email')
        amount = form.cleaned_data.get('amount')
        payment_reason = form.cleaned_data.get('reason')
        account = Account.objects.filter(email=email).first()
        if not account:
            messages.warning(self.request, 'This user does not exist.')
            return super().form_valid(form)
        payment = payments.start_payment(
            owner=account,
            amount=amount,
            payment_method='pay_by_admin',
        )
        payments.finish_payment(
            transaction_id=payment.transaction_id,
            status='processed',
            notes=f'{payment_reason} (by {self.request.user.email})',
        )
        messages.success(self.request, f'You successfully added {amount} USD to the balance of {email}')
        return super().form_valid(form)


class TwoFactorResetView(StaffRequiredMixin, FormView, FormMixin):
    template_name = 'board/two_factor_reset.html'
    form_class = board_forms.TwoFactorResetForm
    success_url = reverse_lazy('two_factor_reset')

    @transaction.atomic
    def form_valid(self, form):
        email = form.cleaned_data.get('email')
        account = Account.objects.filter(email=email).first()
        if not account:
            messages.warning(self.request, 'This user does not exist.')
            return super().form_valid(form)
        for device in devices_for_user(account):
            device.delete()
        messages.success(self.request, f'You successfully reset 2FA for account {email}')
        return super().form_valid(form)


class FinancialReportView(StaffRequiredMixin, FormView):
    template_name = 'board/financial_report.html'
    form_class = billing_forms.FilterOrdersByDateForm
    success_url = reverse_lazy('financial_report')

    def form_valid(self, form):
        year = form.cleaned_data.get('year')
        month = form.cleaned_data.get('month')
        if year or (year and month):
            orders_for_specific_time = orders.list_all_processed_orders_by_date(
                year=form.data.get('year'),
                month=form.data.get('month')
            )
            order_items = []
            total_payment = 0
            for order in orders_for_specific_time:
                for order_item in order.items.all():
                    order_items.append(order_item)
                    total_payment += order_item.price
            return self.render_to_response(
                self.get_context_data(
                    form=form,
                    object_list=order_items,
                    total_payment_by_users=total_payment,
                    total_registered_users=len(list_all_users_by_date(year, month))
                )
            )
        return super().form_valid(form)


class NotExistingDomainSyncView(StaffRequiredMixin, FormView):
    template_name = 'board/not_existing_domain_sync.html'
    form_class = board_forms.DomainSyncForm
    success_url = reverse_lazy('not_existing_domain_sync')

    def form_valid(self, form):
        domain_name = form.cleaned_data.get('domain_name', '').strip().lower()
        zmaster.domain_synchronize_from_backend(
            domain_name=domain_name,
            refresh_contacts=True,
            rewrite_contacts=False,
            change_owner_allowed=True,
            create_new_owner_allowed=True,
            soft_delete=True,
            raise_errors=True,
            log_events=True,
            log_transitions=True,
        )
        domain_in_db = zdomains.domain_find(domain_name=domain_name)
        if domain_in_db:
            logger.info(f'domain {domain_name} is successfully synchronized')
            messages.success(self.request, f'Domain {domain_name} is successfully synchronized')
        else:
            messages.warning(self.request, f'Something went wrong during synchronization of {domain_name}')
        return super().form_valid(form)


class CSVFileSyncRecordView(StaffRequiredMixin, DetailView):
    template_name = 'board/csv_file_sync_record.html'

    def get_object(self, queryset=None):
        return shortcuts.get_object_or_404(CSVFileSync, pk=self.kwargs.get('record_id'))


class CSVFileSyncView(StaffRequiredMixin, FormView):
    template_name = 'board/csv_file_sync.html'
    form_class = board_forms.CSVFileSyncForm
    success_url = reverse_lazy('csv_file_sync')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['csv_file_sync_records'] = CSVFileSync.executions.all().order_by('-pk')
        return context

    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        files = request.FILES.getlist('csv_file')

        if not form.is_valid():
            return self.form_invalid(form)

        if CSVFileSync.executions.filter(status='started').exists():
            messages.warning(self.request, 'Another background process is currently running, please wait before starting a new one.')
            return self.form_invalid(form)

        started_records = []

        for f in files:
            csv_sync_record = CSVFileSync.executions.create(
                input_filename='',
                dry_run=bool(form.data.get('dry_run', False)),
            )

            fout, csv_file_path = tempfile.mkstemp(
                suffix='.csv',
                prefix='domains-%d-' % csv_sync_record.id,
                dir=settings.ZENAIDA_CSV_FILES_SYNC_FOLDER_PATH,
            )
            csv_sync_record.input_filename = csv_file_path
            csv_sync_record.save()

            logger.info('reading {}\n'.format(csv_file_path))

            while True:
                chunk = f.file.read(100000)
                if not chunk:
                    break
                os.write(fout, chunk)
            os.close(fout)

            logger.info('file uploaded, new DB record created: %r', csv_sync_record)

            subprocess.Popen(
                '{} {} csv_import --record_id={} {}'.format(
                    os.path.join(os.path.dirname(sys.executable), 'python'),
                    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'manage.py')),
                    csv_sync_record.id,
                    '--dry_run' if form.data.get('dry_run', False) else '',
                ),
                close_fds=True,
                shell=True,
            )

            started_records.append(csv_sync_record)

        messages.success(self.request, f'New background process started: {started_records}')
        return self.form_valid(form)


class SendingSingleEmailView(StaffRequiredMixin, FormView, FormMixin):
    template_name = 'board/sending_single_email.html'
    form_class = board_forms.SendingSingleEmailForm
    success_url = reverse_lazy('sending_single_email')

    def form_valid(self, form):
        receiver = form.cleaned_data.get('receiver')
        subject = form.cleaned_data.get('subject')
        body = form.cleaned_data.get('body')
        context = {
            'subject': subject,
            'body': body,
        }
        html_content = render_to_string('email/single_email.html', context=context, request=None)
        text_content = strip_tags(html_content)
        msg = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            to=[receiver, ],
        )
        msg.attach_alternative(html_content, 'text/html')
        try:
            msg.send()
            messages.success(self.request, f'Email is sent to {receiver}')
        except Exception as e:
            logging.exception('Failed to send email')
            messages.error(self.request, f'Failed to send email: {e}')
        return super().form_valid(form)


class AuthCodesDownloadView(StaffRequiredMixin, View):

    def dispatch(self, request, *args, **kwargs):
        file_name = '%s.txt' % kwargs.get('file_id')
        file_path = f'/tmp/{file_name}'
        if not os.path.isfile(file_path):
            messages.warning(request, "Invalid request, file not exist")
            return shortcuts.redirect('index')
        response = HttpResponse(open(file_path, 'rt').read(), content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename={file_name}'
        try:
            os.remove(file_path)
        except Exception as e:
            logger.exception(f'was not able to delete file {file_path}: {e}')
        return response


class BulkTransferResultDownloadView(StaffRequiredMixin, View):

    def dispatch(self, request, *args, **kwargs):
        file_name = '%s.txt' % kwargs.get('file_id')
        file_path = f'/tmp/{file_name}'
        if not os.path.isfile(file_path):
            messages.warning(request, "Invalid request, file not exist")
            return shortcuts.redirect('index')
        response = HttpResponse(open(file_path, 'rt').read(), content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename={file_name}'
        try:
            os.remove(file_path)
        except Exception as e:
            logger.exception(f'was not able to delete file {file_path}: {e}')
        return response


class BulkTransferView(StaffRequiredMixin, FormView, FormMixin):
    template_name = 'board/bulk_transfer.html'
    form_class = board_forms.BulkTransferForm
    success_url = reverse_lazy('bulk_transfer')

    def form_valid(self, form):
        new_owner_email = form.cleaned_data.get('new_owner')
        body = form.cleaned_data.get('body')
        new_owner = Account.objects.filter(email=new_owner_email).first()
        report = []
        processed_count = 0
        if not new_owner:
            messages.warning(self.request, 'This user does not exist.')
            return super().form_valid(form)
        try:
            for line in body.split('\n'):
                if line.count(','):
                    domain_name, auth_code = line.strip().split(',')
                elif line.strip().count(';'):
                    domain_name, auth_code = line.strip().split(';')
                elif line.strip().count('|'):
                    domain_name, auth_code = line.strip().split('|')
                elif line.strip().count(' '):
                    domain_name, auth_code = line.strip().split(' ')
                else:
                    report.append(('', 'invlid input line', ))
                    continue
                domain_obj = zdomains.domain_find(domain_name=domain_name)
                if not domain_obj:
                    report.append((domain_name, 'domain does not exist', ))
                    continue
                if domain_obj.owner == new_owner:
                    report.append((domain_name, 'domain is already owned by %r' % new_owner, ))
                    continue
                outputs = zmaster.domain_read_info(
                    domain=domain_name,
                    auth_info=auth_code,
                    return_outputs=True,
                )
                if not outputs:
                    report.append((domain_name, 'domain name is not registered or transfer is not possible at the moment', ))
                    continue
                if isinstance(outputs[-1], rpc_error.EPPAuthorizationError):
                    if outputs[-1].message.lower().count('incorrect authcode provided'):
                        report.append((domain_name, 'incorrect authorization code provided', ))
                    else:
                        report.append((domain_name, 'you are not authorized to transfer this domain', ))
                    continue
                if isinstance(outputs[-1], rpc_error.EPPObjectNotExist):
                    report.append((domain_name, 'domain name is not registered', ))
                    continue
                if isinstance(outputs[-1], rpc_error.EPPError):
                    report.append((domain_name, 'domain transfer failed due to unexpected error, please try again later', ))
                    continue
                if not outputs[-1].get(domain_name):
                    report.append((domain_name, 'domain name is not registered', ))
                    continue
                if len(outputs) < 2:
                    report.append((domain_name, 'domain name transfer is not possible at the moment, please try again later', ))
                    continue
                info = outputs[-2]
                current_registrar = info['epp']['response']['resData']['infData']['clID']
                if current_registrar.lower() == settings.ZENAIDA_REGISTRAR_ID.lower():
                    internal = True
                current_statuses = info['epp']['response']['resData']['infData']['status']
                current_statuses = [current_statuses, ] if not isinstance(current_statuses, list) else current_statuses
                current_statuses = [s['@s'] for s in current_statuses]
                pw = info['epp']['response']['resData']['infData']['authInfo']['pw']
                if pw != 'Authinfo Correct' and pw != auth_code:
                    report.append((domain_name, 'given transfer code is not correct', ))
                    continue
                if 'clientTransferProhibited' in current_statuses or 'serverTransferProhibited' in current_statuses:
                    report.append((domain_name, 'transfer failed because domain was locked or auth code was wrong', ))
                    continue
                if len(orders.find_pending_domain_transfer_order_items(domain_name)):
                    report.append((domain_name, 'domain transfer is already in progress', ))
                    continue
                if current_registrar.lower() in [settings.ZENAIDA_AUCTION_REGISTRAR_ID.lower(), settings.ZENAIDA_REGISTRAR_ID.lower()]:
                    price = 0.0
                else:
                    price = settings.ZENAIDA_DOMAIN_PRICE
                if price > new_owner.balance:
                    report.append((domain_name, 'account %r does not have enough funds to complete domain transfer', ))
                    continue
                transfer_order = orders.order_single_item(
                    owner=new_owner,
                    item_type='domain_transfer',
                    item_price=price,
                    item_name=domain_name,
                    item_details={
                        'transfer_code': auth_code,
                        'rewrite_contacts': True,
                        'internal': internal,
                    },
                )
                new_status = orders.execute_order(transfer_order)
                report.append((domain_name, 'created and executed %r, order status is %r' % (transfer_order, new_status, ), ))
                if new_status == 'processed':
                    processed_count += 1
            txt = ''
            counter = 0
            for item in report:
                txt += ('[%s] %s' % (item[0], item[1], )).strip() + '\n'
                counter += 1
            file_name = f"bulk_transfer_{counter}_domains_{time.strftime('%Y%m%d%H%M%S', time.localtime())}.txt"
            open(f'/tmp/{file_name}', 'wt').write(txt)
            messages.success(self.request, mark_safe('Bulk transfer completed, %d orders were processed. Download full report via <a href="%s">this link</a>' % (
                processed_count, reverse("bulk_transfer_result_download", args=[file_name.replace('.txt', ''), ]),
            )))
        except Exception as e:
            logging.exception('bulk transfer failed due to %r' % e)
            messages.error(self.request, f'Bulk transfer failed due to {e}')
        return super().form_valid(form)
