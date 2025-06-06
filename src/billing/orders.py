import logging
import os
import calendar
from datetime import timedelta

import pdfkit  # @UnresolvedImport

from django import shortcuts
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import SuspiciousOperation
from django.template.loader import get_template

from billing import exceptions
from billing.models.order import Order
from billing.models.order_item import OrderItem

from zen import zdomains, zcontacts
from zen import zmaster

#------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

#------------------------------------------------------------------------------


def by_id(order_id):
    """
    Return Order object by ID
    """
    return Order.orders.filter(id=order_id).first()


def get_all_orders_by_status_and_older_than_days(status, older_than_days=1):
    """
    Return all orders in database for given status and older than given days.
    """
    return Order.orders.filter(status=status, started_at__lte=timezone.now()-timedelta(days=older_than_days))


def get_order_by_id_and_owner(order_id, owner, log_action=None):
    """
    Safe method to find order for given user.
    """
    order = by_id(order_id)
    if not order:
        logger.critical(f'user {owner} tried to {log_action} non-existing order')
        raise SuspiciousOperation()
    if order.owner != owner:
        logger.critical(f'user {owner} tried to {log_action} an order for another user')
        raise SuspiciousOperation()
    return order


def list_orders(owner, exclude_cancelled=False, include_statuses=[]):
    """
    List orders for given user.
    """
    qs = Order.orders.filter(owner=owner)
    if exclude_cancelled:
        qs = qs.exclude(status='cancelled')
    if include_statuses:
        qs = qs.filter(status__in=include_statuses)
    qs = qs.order_by('-finished_at')
    return list(qs.all())


def list_order_items(owner, order_item_type=None, order_statuses=[]):
    """
    List existing order items for given user.
    """
    qs = OrderItem.order_items.filter(order__owner=owner)
    if order_statuses:
        qs = qs.filter(order__status__in=order_statuses)
    if order_item_type is not None:
        qs = qs.filter(type=order_item_type)
    return list(qs.all())


def list_processed_orders(owner, order_id_list):
    """
    List only processed orders for given user.
    """
    return shortcuts.get_list_or_404(Order.orders.filter(owner=owner, id__in=order_id_list, status='processed'))


def list_orders_by_date(owner, year, month=None, exclude_cancelled=False):
    """
    List orders for given user by date.
    """
    if year and month:
        orders = Order.orders.filter(owner=owner, started_at__year=year, started_at__month=month)
    elif year:
        orders = Order.orders.filter(owner=owner, started_at__year=year)
    else:
        orders = Order.orders.filter(owner=owner)
    if exclude_cancelled:
        orders = orders.exclude(status='cancelled')
    orders = orders.order_by('-finished_at')
    return list(orders.all())


def list_processed_orders_by_date_for_specific_user(owner, year, month=None):
    """
    List only processed orders by date for given user.
    """
    if year and month:
        orders = Order.orders.filter(
            owner=owner,
            finished_at__year=year,
            finished_at__month=month,
            status='processed',
        ).order_by('-finished_at')
    elif year:
        orders = Order.orders.filter(
            owner=owner,
            finished_at__year=year,
            status='processed',
        ).order_by('-finished_at')
    else:
        orders = Order.orders.filter(
            owner=owner,
            status='processed',
        ).order_by('-finished_at')
    return list(orders.all())


def list_all_processed_orders_by_date(year, month=None):
    if year and month:
        return Order.orders.filter(
            finished_at__year=year,
            finished_at__month=month,
            status='processed',
        ).order_by('-finished_at')
    else:
        return Order.orders.filter(
            finished_at__year=year,
            status='processed',
        ).order_by('-finished_at')


def list_orders_with_failed_items(order_item_statuses=['executing', 'failed', ], order_statuses=['failed', 'incomplete', ]):
    return Order.orders.filter(
        Q(items__status__in=order_item_statuses) &
        Q(status__in=order_statuses) &
        Q(started_at__lte=timezone.now() - timedelta(minutes=5))
    ).order_by('-finished_at').distinct()


def find_pending_domain_renew_order_items(domain_name):
    """
    Find OrderItem objects for domain transfer order for given domain.
    """
    return list(OrderItem.order_items.filter(
        type='domain_renew',
        name=domain_name,
    ).exclude(
        status__in=['processed', ],
    ).all())


def find_latest_processed_domain_restore_order(domain_name):
    """
    Find most recent Order object for given domain name with completed domain_restore OrderItems.
    """
    return Order.orders.filter(
        Q(items__status__in=['processed', ], items__type='domain_restore', items__name=domain_name) &
        Q(status__in=['processed', 'incomplete', ])
    ).order_by('-finished_at').first()

def find_pending_domain_transfer_order_items(domain_name):
    """
    Find OrderItem objects for domain transfer order for given domain.
    """
    return list(OrderItem.order_items.filter(
        type='domain_transfer',
        name=domain_name,
        status='pending',
    ).all())


def prepare_register_renew_restore_item(domain_object):
    """
    Prepare required info to be able to construct OrderItem object from given Domain object.
    """
    if domain_object.is_blocked:
        raise exceptions.DomainBlockedError()
    item_type = 'domain_register'
    item_price = settings.ZENAIDA_DOMAIN_PRICE
    item_name = domain_object.name
    if domain_object.can_be_restored:
        item_type = 'domain_restore'
        item_price = settings.ZENAIDA_DOMAIN_RESTORE_PRICE
    elif domain_object.is_registered:
        item_type = 'domain_renew'
    return item_type, item_price, item_name


def order_single_item(owner, item_type, item_price, item_name, item_details=None):
    """
    Create an Order with one item with given attributes.
    """
    if item_details and item_details.get('created_automatically'):
        description = '{} {} (automatically)'.format(item_name, item_type.replace('_', ' ').split(' ')[1])
    else:
        description = '{} {}'.format(item_name, item_type.replace('_', ' ').split(' ')[1])
    new_order = Order.orders.create(
        owner=owner,
        status='started',
        started_at=timezone.now(),
        description=description,
    )
    new_order_item = OrderItem.order_items.create(
        order=new_order,
        type=item_type,
        price=item_price,
        name=item_name,
        details=item_details,
    )
    logger.info('created new %r with single %r', new_order, new_order_item)
    return new_order


def order_multiple_items(owner, order_items):
    """
    Create and Order with multiple items.
    """
    items_by_type = {}
    if len(order_items) == 1:
        description = '{}'.format(order_items[0]['item_type'].replace('_', ' '))
    else:
        description = []
        for order_item in order_items:
            if order_item['item_type'] not in items_by_type:
                items_by_type[order_item['item_type']] = []
            items_by_type[order_item['item_type']].append(order_item)
        for item_type, items_of_that_type in items_by_type.items():
            item_label, _, order_type = item_type.partition('_')
            if len(items_of_that_type) > 1:
                item_label = item_label.replace('domain', 'domains')
            description.append('{} {} {}'.format(order_type, len(items_of_that_type), item_label, ))
        description = ', '.join(description)
    new_order = Order.orders.create(
        owner=owner,
        status='started',
        started_at=timezone.now(),
        description=description,
    )
    for order_item in order_items:
        OrderItem.order_items.create(
            order=new_order,
            type=order_item['item_type'],
            price=order_item['item_price'],
            name=order_item['item_name'],
        )
    logger.info('created new %r with %d items', new_order, len(order_items))
    return new_order


def update_order_item(order_item, new_status=None, charge_user=False, save=True, details=None, outputs=None):
    """
    Update given OrderItem object with new info.
    Optionally can charge user balance if order fulfillment was successful.
    We must use that and only that method to confirm/reject orders.
    """
    if charge_user:
        if order_item.order.owner.balance < order_item.price:
            logger.critical('not enough account balance to execute order item for %r', order_item.order.owner)
            return False
        order_item.order.owner.balance -= order_item.price
        if save:
            order_item.order.owner.save()
        order_item.order.finished_at = timezone.now()
        if save:
            order_item.order.save() 
        logger.info('charged user %s for "%s"' % (order_item.order.owner, order_item.price))
    if new_status:
        old_status = order_item.status
        order_item.status = new_status
        if save:
            order_item.save()
        logger.info('updated status of %r from "%s" to "%s"' % (order_item, old_status, new_status))
    if (details is not None or outputs is not None) and save:
        d = order_item.details or {}
        if details:
            d.update(details)
        if outputs:
            d['outputs'] = [(str(o) if isinstance(o, Exception) else o) for o in outputs]
        order_item.details = d
        order_item.save()
        logger.info('updated details of %r : %r' % (order_item, d, ))
    return True


def update_order_with_order_item(owner, order, order_item, item_type, item_price, item_name, item_details=None):
    order.owner = owner
    order.status = 'started'
    order.started_at = timezone.now()
    order.description = '{} {}'.format(item_name, item_type.replace('_', ' ').split(' ')[1])
    order.save()
    order_item.order = order
    order_item.type = item_type
    order_item.price = item_price
    order_item.name = item_name
    order_item.details = item_details
    order_item.save()
    logger.info('updated order item %s in %s order' % (order_item, order, ))
    return order


def execute_domain_register(order_item, target_domain):
    """
    Execute domain register order fulfillment and update order item.
    """
    outputs = zmaster.domain_check_create_update_renew(
        domain_object=target_domain,
        sync_contacts=False,
        sync_nameservers=True,
        renew_years=settings.ZENAIDA_DOMAIN_RENEW_YEARS,
        log_events=True,
        log_transitions=True,
        raise_errors=False,
        return_outputs=True,
    )
    if not outputs or not outputs[-1] or isinstance(outputs[-1], Exception):
        update_order_item(order_item, new_status='failed', charge_user=False, save=True, outputs=outputs)
        return False
    return update_order_item(order_item, new_status='processed', charge_user=True, save=True, outputs=outputs)


def execute_domain_renew(order_item, target_domain):
    """
    Execute domain renew order fulfillment and update order item.
    """
    outputs = zmaster.domain_check_create_update_renew(
        domain_object=target_domain,
        sync_contacts=False,
        sync_nameservers=True,
        renew_years=settings.ZENAIDA_DOMAIN_RENEW_YEARS,
        log_events=True,
        log_transitions=True,
        raise_errors=False,
        return_outputs=True,
    )
    if not outputs or not outputs[-1] or isinstance(outputs[-1], Exception):
        update_order_item(order_item, new_status='failed', charge_user=False, save=True, outputs=outputs)
        return False
    ret = update_order_item(order_item, new_status='processed', charge_user=True, save=True, outputs=outputs)
    zmaster.domain_synchronize_from_backend(
        domain_name=order_item.name,
        refresh_contacts=False,
        rewrite_contacts=False,
        change_owner_allowed=False,
        create_new_owner_allowed=False,
        soft_delete=False,
        domain_transferred_away=False,
        raise_errors=False,
        log_events=True,
        log_transitions=True,
    )
    return ret


def execute_domain_restore(order_item, target_domain):
    """
    Execute domain restore order fulfillment and update order item.
    """
    outputs = zmaster.domain_restore(
        domain_object=target_domain,
        res_reason='Customer %s requested to restore %s domain' % (order_item.order.owner.email, target_domain.name, ),
        log_events=True,
        log_transitions=True,
        raise_errors=False,
        return_outputs=True,
    )
    if not outputs or not outputs[-1] or isinstance(outputs[-1], Exception):
        update_order_item(order_item, new_status='failed', charge_user=False, save=True, outputs=outputs)
        return False
    return update_order_item(order_item, new_status='processed', charge_user=True, save=True, outputs=outputs)


def execute_domain_transfer(order_item):
    """
    Execute domain transfer take-over order fulfillment and update order item, status will be "pending".
    """
    outputs = []
    if hasattr(order_item, 'details') and order_item.details.get('internal'):
        domain = zdomains.domain_find(order_item.name)
        if not domain:
            logger.info('domain name %r was not found in local DB, sync from back-end', order_item.name)
            outputs.extend(zmaster.domain_synchronize_from_backend(
                domain_name=order_item.name,
                refresh_contacts=True,
                rewrite_contacts=False,
                change_owner_allowed=True,
                create_new_owner_allowed=True,
                soft_delete=True,
                domain_transferred_away=False,
                raise_errors=True,
                log_events=True,
                log_transitions=True,
            ))

        domain = zdomains.domain_find(order_item.name)
        if not domain:
            logger.critical('failed to synchronize domain %r, not possible to finish internal transfer', order_item.name)
            return False

        if domain.auth_key and domain.auth_key != order_item.details.get('transfer_code'):
            logger.critical('failed to finish internal domain transfer of %r because of invalid transfer code', domain)
            return False

        first_contact = order_item.order.owner.contacts.first()
        if not first_contact:
            # must create one contact if not exist yet
            first_contact = zcontacts.contact_create_from_profile(order_item.order.owner, order_item.order.owner.profile)

        # Change the owner of the domain with removing his/her contact and updating the new contact
        oldest_registrant = zcontacts.get_oldest_registrant(order_item.order.owner)
        zdomains.domain_change_registrant(domain, oldest_registrant, True)
        zdomains.domain_detach_contact(domain, 'admin')
        zdomains.domain_detach_contact(domain, 'billing')
        zdomains.domain_detach_contact(domain, 'tech')
        zdomains.domain_join_contact(domain, 'admin', first_contact)
        zdomains.domain_join_contact(domain, 'tech', first_contact)
        domain.refresh_from_db()

        # Override info on back-end
        outputs.extend(zmaster.domain_synchronize_from_backend(
            domain_name=domain.name,
            refresh_contacts=False,
            rewrite_contacts=True,
            change_owner_allowed=False,
            expected_owner=order_item.order.owner,
            soft_delete=True,
            raise_errors=True,
            log_events=True,
            log_transitions=True,
        ))
        domain.refresh_from_db()

        # Override registrant on back-end
        outputs.extend(zmaster.domain_synchronize_contacts(
            domain,
            merge_duplicated_contacts=True,
            rewrite_registrant=True,
            new_registrant=None,
            raise_errors=True,
            log_events=True,
            log_transitions=True,
        ))
        domain.refresh_from_db()

        try:
            outputs.extend(zmaster.domain_set_auth_info(domain, return_outputs=True))
        except Exception as e:
            logger.exception('failed changing auth code for %r after internal transfer' % domain)
            update_order_item(order_item, new_status='failed', charge_user=False, save=True, details={'error': str(e), }, outputs=outputs)
            return False

        domain.refresh_from_db()

        # Auth key shouldn't be used anymore as transfer is done.
        domain.auth_key = ''
        domain.save()

        # In the end, order is done so, update order item as processed.
        update_order_item(order_item, new_status='processed', charge_user=True, save=True, outputs=outputs)

        return True

    outputs.extend(zmaster.domain_transfer_request(
        domain=order_item.name,
        auth_info=order_item.details.get('transfer_code'),
        return_outputs=True,
    ))
    if not outputs or not outputs[-1] or isinstance(outputs[-1], Exception):
        update_order_item(order_item, new_status='failed', charge_user=False, save=True, outputs=outputs)
        return False

    return update_order_item(order_item, new_status='pending', charge_user=False, save=True, outputs=outputs)


def execute_one_item(order_item):
    """
    Based on type of OrderItem executes corresponding fulfillment procedure.
    """
    logger.info('executing %r with %r', order_item, order_item.details)

    update_order_item(order_item, new_status='executing', charge_user=False, save=True)

    try:

        if order_item.type == 'domain_transfer':
            return execute_domain_transfer(order_item)

        target_domain = zdomains.domain_find(order_item.name)
        if not target_domain:
            logger.critical('domain %r not exists in local db', order_item.name)
            update_order_item(order_item, new_status='blocked', charge_user=False, save=True, details={'error': 'domain was not prepared or not exist', })
            return False

        if target_domain.owner != order_item.order.owner:
            logger.critical('user %r tried to execute an order item %r with domain from another owner' % order_item.order.owner, order_item, target_domain.owner)
            update_order_item(order_item, new_status='blocked', charge_user=False, save=True, details={'error': 'domain was owned by another user', })
            return False

        if order_item.order.owner.balance < order_item.price:
            logger.critical('not enough account balance to execute order item %r for %r', order_item, order_item.order.owner)
            update_order_item(order_item, new_status='failed', charge_user=False, save=True, details={'error': 'not enough account balance', })
            return False

        if order_item.type == 'domain_register':
            return execute_domain_register(order_item, target_domain)

        if order_item.type == 'domain_renew':
            return execute_domain_renew(order_item, target_domain)

        if order_item.type == 'domain_restore':
            return execute_domain_restore(order_item, target_domain)

    except Exception as e:
        logger.critical('order item %r execution has failed: %r' % (order_item, e, ))
        update_order_item(order_item, new_status='failed', charge_user=False, save=True, details={'error': str(e), })
        return False

    logger.critical('order item %r has a wrong type' % order_item)
    return False


def execute_order(order_object, already_processed=False):
    """
    High-level method to execute fulfillment from given Order object.
    Checks every OrderItem in that Order and tries to execute it if possible.
    Counts "processed", "pending" and "failed" order items and populate final Order status in the end.
    Returns new Order object status: "processed", "incomplete", "processing", "failed".
    """
    current_status = order_object.status
    new_status = order_object.status
    total_processed = 0
    total_executed = 0
    total_in_progress = 0
    total_failed = 0
    # TODO: check/verify every item against Back-end before start processing
    for order_item in order_object.items.all():
        if order_item.status in ['processed', 'pending', 'blocked', ]:
            # only take actions with items that are not yet finished or in pending state
            continue
        total_executed += 1
        if already_processed:
            update_order_item(order_item, new_status='processed', charge_user=True, save=True, details={'reason': 'already processed'})
            total_processed += 1
        else:
            if execute_one_item(order_item):
                if order_item.status == 'processed':
                    total_processed += 1
                elif order_item.status == 'pending':
                    total_in_progress += 1
                elif order_item.status == 'failed':
                    total_failed += 1
                else:
                    logger.critical('order item %s execution finished with unexpected status: %s', order_item, order_item.status)
    if total_processed == total_executed:
        if total_executed > 0:
            new_status = 'processed'
        else:
            new_status = 'incomplete'
            logger.critical('nothing was executed during order processing: %r', order_object)
    else:
        if total_failed > 0:
            new_status = 'failed'
        elif total_in_progress > 0:
            new_status = 'processing'
        else:
            new_status = 'incomplete'
    order_object.status = new_status
    order_object.save()
    logger.info('updated status for %r : "%s" -> "%s"' % (order_object, current_status, new_status))
    return new_status


def refresh_order(order_object):
    """
    Tries to finish Order object fulfillment if it was started but not finished completely.
    Normally should be always safe to execute for any order - it just checks and counts statuses of all items. 
    Returns new Order object status: "processed", "incomplete", "processing", "failed".
    """
    current_status = order_object.status
    new_status = order_object.status
    total_in_progress = 0
    total_failed = 0
    total_processed = 0
    order_items = list(order_object.items.all())
    for order_item in order_items:
        if order_item.status in ['processed', ]:
            # skip changes for already finished items
            total_processed += 1
            continue
        if order_item.status == 'pending':
            total_in_progress += 1
        elif order_item.status == 'failed':
            total_failed += 1
    if total_failed > 0:
        new_status = 'failed'
    elif total_in_progress > 0:
        new_status = 'processing'
    else:
        if total_processed == len(order_items):
            new_status = 'processed'
        else:
            new_status = 'incomplete'
    order_object.status = new_status
    order_object.save()
    logger.debug('refreshed status for %r from "%s" to "%s"' % (order_object, current_status, new_status))
    return new_status


def cancel_and_remove_order(order_object):
    """
    Cancel order and remove it from database.
    """
    new_status = 'cancelled'
    old_status = order_object.status
    order_object.delete()
    logger.debug('updated status for %r from "%s" to "%s" and removed it' % (order_object, old_status, new_status))
    return new_status


def build_receipt(owner, year=None, month=None, order_id=None):
    """
    Creates detailed report in PDF format about all orders for given user.
    Optionally selects orders for given period.
    """
    order_objects = []
    if order_id:
        order_object = by_id(order_id)
        if not order_object:
            return None
        if not order_object.finished_at:
            return None
        order_objects.append(order_object)
        receipt_period = order_object.finished_at.strftime('%B %Y')
    else:
        order_objects = list_processed_orders_by_date_for_specific_user(owner=owner, year=year, month=month)
        if not order_objects:
            return None
        if year and month:
            month_label = calendar.month_name[int(month)]
            receipt_period = f'{year} {month_label}'
        elif year:
            receipt_period = f'{year}'
        else:
            receipt_period = order_objects[-1].finished_at.strftime('%B %Y')

    domain_orders = []
    total_price = 0
    for order in order_objects:
        for order_item in order.items.all():
            domain_orders.append({
                'domain_name': order_item.name,
                'transaction_date': order.finished_at.strftime('%d %B %Y'),
                'transaction_type': order_item.get_type_display().replace('Domain ', ''),
                'price': int(order_item.price)
            })
            total_price += int(order_item.price)

    # Fill html template with the domain orders and user profile info
    html_template = get_template('billing/billing_receipt.html')
    rendered_html = html_template.render({
        'domain_orders': domain_orders,
        'user_profile': owner.profile,
        'total_price': total_price,
        'receipt_period': receipt_period
    })

    # Create pdf file from a html file
    pdfkit.from_string(rendered_html, '/tmp/out.pdf')
    with open("/tmp/out.pdf", "rb") as pdf_file:
        pdf_raw = pdf_file.read()
    os.remove("/tmp/out.pdf")
    return {
        'body': pdf_raw,
        'filename': '{}_receipt.pdf'.format(receipt_period.replace(' ', '_')),
    }
