from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.safestring import mark_safe
from nested_admin import NestedModelAdmin  # @UnresolvedImport

from billing import orders
from billing.models.payment import Payment
from billing.models.order import Order
from billing.models.order_item import OrderItem
from billing.pay_btcpay.models import BTCPayInvoice


class PaymentAdmin(NestedModelAdmin):
    list_display = ('transaction_id', 'amount', 'account', 'method', 'started_at', 'finished_at', 'status', 'notes', )
    search_fields = ('owner__email', 'transaction_id', )
    list_filter = ('status', 'method', )

    def account(self, payment_instance):
        return mark_safe('<a href="{}?q={}">{}</a>'.format(
            reverse("admin:accounts_account_changelist"), payment_instance.owner.email, payment_instance.owner.email))


class BTCPayInvoiceAdmin(NestedModelAdmin):
    list_display = ('invoice_id', 'transaction_id', 'amount', 'started_at', 'finished_at', 'status', )
    list_filter = ('status', )
    search_fields = ('invoice_id', 'transaction_id', )


class OrderAdmin(NestedModelAdmin):
    list_display = ('description', 'order_items', 'total_price', 'account', 'started_at', 'finished_at', 'retries' , 'status', )
    search_fields = ('owner__email', 'description', )
    list_filter = ('status', 'retries', )
    actions = ('order_retry', )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(_all_items_count=Count("items", distinct=True))
        return queryset

    def order_retry(self, request, queryset):
        results = []
        for order_object in queryset:
            results.append('{}: {}'.format(order_object, orders.execute_order(order_object)))
        self.message_user(request, ', '.join(results))
    order_retry.short_description = "Re-try selected orders"

    def order_items(self, order_instance):
        return mark_safe('<a href="{}?q={}">[ {} items ]</a>'.format(
            reverse("admin:billing_orderitem_changelist"), order_instance.id, order_instance._all_items_count))

    def account(self, order_instance):
        return mark_safe('<a href="{}?q={}">{}</a>'.format(
            reverse("admin:accounts_account_changelist"), order_instance.owner.email, order_instance.owner.email))


class OrderItemAdmin(NestedModelAdmin):
    list_display = ('order', 'description', 'name', 'type', 'price', 'status', )
    list_filter = ('status', 'type', )
    search_fields = ('name', 'order__owner__email', 'order__id')

    def description(self, order_item_instance):
        return mark_safe('<a href="{}?q={}">{}</a>'.format(
            reverse("admin:billing_orderitem_changelist"), order_item_instance.order.id, order_item_instance.order.description))


admin.site.register(Payment, PaymentAdmin)
admin.site.register(BTCPayInvoice, BTCPayInvoiceAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(OrderItem, OrderItemAdmin)
