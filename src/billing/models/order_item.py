from django.db import models

from back.domains import validate

from billing.models.order import Order

class OrderItem(models.Model):
    
    order_items = models.Manager()

    class Meta:
        app_label = 'billing'
        base_manager_name = 'order_items'
        default_manager_name = 'order_items'


    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')

    price = models.FloatField(null=False, blank=False)

    type = models.CharField(
        choices=(
            ('domain_register', 'Domain Register', ),
            ('domain_renew', 'Domain Renew', ),
            ('domain_restore', 'Domain Restore', )
        ),
        max_length=32,
        null=False,
        blank=False,
    )

    domain_name = models.CharField(max_length=255, unique=True, validators=[validate, ])