from datetime import date, datetime
from decimal import Decimal
import StringIO

from django.core.exceptions import ValidationError

from pdfdocument.document import PDFDocument

import plata
from plata.discount.models import Discount
from options_product.models import Product, \
    ProductVariation, Category, ProductPrice
from plata.product.stock.models import Period, StockTransaction
import plata.reporting.order
from plata.shop.models import Order

from options.tests.base import PlataTest


class ModelTest(PlataTest):
    def test_00_test(self):
        """Test assertRaisesWithCode works as expected"""
        def raise_validationerror():
            raise ValidationError('test', code='test')

        def raise_notimplementederror():
            raise NotImplementedError

        self.assertRaisesWithCode(ValidationError, raise_validationerror, code='test')

        self.assertRaises(ValidationError,
            lambda: self.assertRaisesWithCode(ValidationError,
                raise_validationerror, code='something'))

        self.assertRaises(NotImplementedError,
            lambda: self.assertRaisesWithCode(ValidationError,
                raise_notimplementederror, code='something'))

        self.assertRaises(Exception,
            lambda: self.assertRaisesWithCode(ValidationError,
                lambda: None, code='something'))

    def test_01_basic_order(self):
        """Test basic order and product properties"""
        product = self.create_product()
        order = self.create_order()

        item_price = Decimal('79.90')
        line_item_price = item_price * 2
        order_total = Decimal('159.80')
        tax_factor = Decimal('1.076')

        price = product.get_price(currency=order.currency)
        self.assertEqual(price.currency, order.currency)
        self.assertAlmostEqual(price.unit_price, item_price)
        self.assertAlmostEqual(price.unit_price_incl_tax, price.unit_price)
        self.assertAlmostEqual(price.unit_price_excl_tax, item_price / tax_factor)
        self.assertAlmostEqual(price.unit_tax, price.unit_price_excl_tax * Decimal('0.076'))

        prices = dict(product.get_prices())
        self.assertAlmostEqual(prices['CHF']['normal'].unit_price, Decimal('99.90'))
        self.assertAlmostEqual(prices['CHF']['sale'].unit_price, Decimal('79.90'))
        self.assertAlmostEqual(prices['EUR']['normal'].unit_price, Decimal('49.90'))
        self.assertEqual(prices['EUR']['sale'], None)
        self.assertTrue(product.in_sale('CHF'))
        self.assertFalse(product.in_sale('EUR'))

        order.modify_item(product.variations.get(), 5)
        order.modify_item(product.variations.get(), -4)
        item = order.modify_item(product.variations.get(), 1)

        self.assertEqual(order.items.count(), 1)

        self.assertEqual(item.quantity, 2)

        self.assertAlmostEqual(order.items_subtotal, order_total / tax_factor)
        self.assertAlmostEqual(order.items_subtotal + order.items_tax, order_total)
        self.assertAlmostEqual(order.total, order_total)

        self.assertAlmostEqual(item._unit_price, item_price / tax_factor)
        self.assertAlmostEqual(item.discounted_subtotal_incl_tax, line_item_price)
        self.assertAlmostEqual(item.discounted_subtotal_excl_tax, line_item_price / tax_factor)
        self.assertAlmostEqual(item.discounted_subtotal_incl_tax, line_item_price)

        self.assertAlmostEqual(item.unit_price, item_price)
        self.assertAlmostEqual(item.line_item_discount, 0)
        self.assertAlmostEqual(item.discounted_subtotal, item.discounted_subtotal_incl_tax)

        self.assertAlmostEqual(order.shipping, Decimal('0.00'))

        # Switch around tax handling and re-test
        plata.settings.PLATA_PRICE_INCLUDES_TAX = False

        self.assertAlmostEqual(item.unit_price, item_price / tax_factor)
        self.assertAlmostEqual(item.line_item_discount, 0 / tax_factor)
        self.assertAlmostEqual(item.discounted_subtotal, item.discounted_subtotal_excl_tax)

        self.assertRaises(NotImplementedError, lambda: order.shipping)

        # Switch tax handling back
        plata.settings.PLATA_PRICE_INCLUDES_TAX = True

        product.prices.filter(currency='CHF', is_sale=False).delete()
        product.flush_price_cache()
        prices = dict(product.get_prices())
        self.assertEqual(prices['CHF']['normal'], None)
        self.assertAlmostEqual(prices['CHF']['sale'].unit_price, Decimal('79.90'))

        product.prices.all().delete()
        self.assertRaises(ProductPrice.DoesNotExist, order.modify_item,
            product, absolute=1)

    def test_02_eur_order(self):
        """Test basic order in EUR works as expected"""
        product = self.create_product()
        order = self.create_order()

        order.currency = 'EUR'
        order.save()

        item = order.modify_item(product.variations.get(), 2)

        self.assertEqual(item.unit_price, Decimal('49.90'))
        self.assertEqual(item.currency, order.currency)

    def test_03_mixed_currencies(self):
        """Test orders with mixed currencies are rejected during validation"""

        p1 = self.create_product()
        p2 = self.create_product()
        order = self.create_order()

        order.currency = 'CHF'
        i1 = order.modify_item(p1.variations.get(), 3)

        order.currency = 'EUR'
        self.assertRaisesWithCode(ValidationError, lambda: order.modify_item(p2.variations.get(), 2),
            code='multiple_currency')

        # Validation should still fail
        self.assertRaisesWithCode(ValidationError, lambda: order.validate(order.VALIDATE_BASE),
            code='multiple_currency')

        order.currency = 'CHF'
        # Order should validate now
        order.validate(order.VALIDATE_BASE)

    def test_04_order_modify_item(self):
        """Test Order.modify_item method is well behaving"""
        p1 = self.create_product()
        p2 = self.create_product()
        order = self.create_order()

        order.modify_item(p1.variations.get(), 42)
        order.modify_item(p2.variations.get(), 42)
        self.assertEqual(order.items.count(), 2)

        order.modify_item(p1.variations.get(), -42)
        self.assertEqual(order.items.count(), 1)

        item = order.modify_item(p1.variations.get(), relative=3)
        self.assertEqual(item.quantity, 3)
        item = order.modify_item(p1.variations.get(), relative=2)
        self.assertEqual(item.quantity, 5)
        item = order.modify_item(p1.variations.get(), absolute=33)
        self.assertEqual(item.quantity, 33)

    def test_05_order_status(self):
        """Test order status modification"""
        order = self.create_order()

        self.assertRaisesWithCode(ValidationError, lambda: order.update_status(
            Order.CHECKOUT,
            'Checkout process has started',
            ), code='order_empty')

        product = self.create_product()
        order.modify_item(product.variations.get(), 1)

        # Should be possible to update order status now
        order.update_status(
            Order.CONFIRMED,
            'Order has been confirmed',
            )

        # Should not be possible to modify order once checkout process has started
        self.assertRaisesWithCode(ValidationError, lambda: order.modify_item(product.variations.get(), 2),
            code='order_sealed')

        self.assertEqual(order.status, Order.CONFIRMED)
        self.assertEqual(Order.objects.get().status, Order.CONFIRMED)

    def test_06_order_percentage_discount(self):
        """Test a simple percentage discount"""
        order = self.create_order()
        p1 = self.create_product()
        p2 = self.create_product()

        order.modify_item(p1.variations.get(), 3)
        order.modify_item(p2.variations.get(), 5)

        discount = Discount.objects.create(
            is_active=False,
            type=Discount.PERCENTAGE,
            code='asdf',
            name='Percentage discount',
            value=30)

        self.assertRaises(ValidationError, lambda: discount.add_to(order))
        discount.is_active = True
        discount.save()

        discount.add_to(order)
        order.recalculate_total()

        tax_factor = Decimal('1.076')
        item_price_incl_tax = Decimal('79.90')
        item_price_excl_tax = item_price_incl_tax / tax_factor

        order.recalculate_total()
        item = order.modify_item(p1.variations.get(), relative=0)
        item2 = order.modify_item(p2.variations.get(), relative=0)

        self.assertAlmostEqual(item.unit_price, item_price_incl_tax)
        self.assertAlmostEqual(item.line_item_discount, item_price_incl_tax * 3 * Decimal('0.30'))
        self.assertAlmostEqual(order.total,
            item.discounted_subtotal + item2.discounted_subtotal)

        plata.settings.PLATA_PRICE_INCLUDES_TAX = False
        order.recalculate_total()
        item = order.modify_item(p1.variations.get(), 0)
        item2 = order.modify_item(p2.variations.get(), 0)

        self.assertAlmostEqual(item.unit_price, item_price_excl_tax)
        self.assertAlmostEqual(item.line_item_discount, item_price_excl_tax * 3 * Decimal('0.30'))
        self.assertAlmostEqual(order.total,
            item.discounted_subtotal + item2.discounted_subtotal + order.items_tax)

        plata.settings.PLATA_PRICE_INCLUDES_TAX = True

    def test_07_order_amount_discount(self):
        """Test a simple amount discount"""
        order = self.create_order()
        p1 = self.create_product()
        p2 = self.create_product()

        normal1 = order.modify_item(p1.variations.get(), 3)
        normal2 = order.modify_item(p2.variations.get(), 5)

        order.recalculate_total()
        self.assertAlmostEqual(order.total, Decimal('639.20'))

        discount = Discount.objects.create(
            type=Discount.AMOUNT_INCL_TAX,
            code='asdf',
            name='Amount discount',
            value=Decimal('50.00'),
            is_active=True,
            tax_class=self.tax_class,
            currency='CHF')
        discount.add_to(order)
        order.recalculate_total()

        discounted1 = order.modify_item(p1.variations.get(), 0)
        discounted2 = order.modify_item(p2.variations.get(), 0)

        tax_factor = Decimal('1.076')
        item_price_incl_tax = Decimal('79.90')
        item_price_excl_tax = item_price_incl_tax / tax_factor

        self.assertAlmostEqual(order.total, Decimal('639.20') - Decimal('50.00'))

        self.assertAlmostEqual(normal1.unit_price, discounted1.unit_price)
        self.assertAlmostEqual(normal2.unit_price, discounted2.unit_price)
        self.assertAlmostEqual(normal1.unit_price, item_price_incl_tax)

        self.assertEqual(normal1.line_item_discount, 0)
        self.assertEqual(normal2.line_item_discount, 0)

        self.assertAlmostEqual(discounted1.line_item_discount, Decimal('50.00') / 8 * 3)
        self.assertAlmostEqual(discounted2.line_item_discount, Decimal('50.00') / 8 * 5)

        self.assertAlmostEqual(discounted1.discounted_subtotal, order.total / 8 * 3)
        self.assertAlmostEqual(discounted2.discounted_subtotal, order.total / 8 * 5)

        plata.settings.PLATA_PRICE_INCLUDES_TAX = False
        order.recalculate_total()
        discounted1 = order.modify_item(p1.variations.get(), 0)
        discounted2 = order.modify_item(p2.variations.get(), 0)

        self.assertAlmostEqual(order.total, Decimal('639.20') - Decimal('50.00'))

        self.assertAlmostEqual(discounted1.unit_price, item_price_excl_tax)
        self.assertAlmostEqual(discounted1.line_item_discount, discount.value / tax_factor / 8 * 3)
        self.assertAlmostEqual(order.total,
            discounted1.discounted_subtotal + discounted2.discounted_subtotal + order.items_tax)

        plata.settings.PLATA_PRICE_INCLUDES_TAX = True

    def test_08_order_payment(self):
        """Test basic order payment model behavior"""
        order = self.create_order()
        product = self.create_product()

        order.modify_item(product.variations.get(), 10)
        order.recalculate_total()

        payment = order.payments.model(
            order=order,
            currency='CHF',
            amount=Decimal('49.90'),
            payment_method='Mafia style',
            )

        # The descriptor cannot be used through create(), therefore
        # we need this stupid little dance
        payment.data = {'anything': 42}
        payment.save()

        order = Order.objects.get(pk=order.pk)
        self.assertAlmostEqual(order.paid, 0)

        payment.authorized = datetime.now()
        payment.save()

        order = Order.objects.get(pk=order.pk)
        self.assertAlmostEqual(order.balance_remaining, order.total - payment.amount)

        self.assertEqual(order.payments.all()[0].data['anything'], 42)

        payment2 = order.payments.model(
            order=order.reload(),
            currency='EUR', # mismatch!
            amount=Decimal('100'),
            payment_method='Whatever',
            )
        payment2.data = {}
        payment2.save()

        order2 = order.reload()

        # Shouldn't have changed
        self.assertAlmostEqual(order2.balance_remaining, order.balance_remaining)
        self.assertNotEqual(order2.notes, order.notes)

    def test_09_selective_discount(self):
        """Test applying discounts with product restriction"""
        p1 = self.create_product()
        p2 = self.create_product()
        p2.name = 'Discountable'
        p2.save()

        c = Category.objects.create(
            name='category',
            slug='category',
            is_active=True,
            is_internal=True,
            )
        p2.categories.add(c)

        d = Discount(
            type=Discount.PERCENTAGE,
            name='Some discount',
            code='asdf',
            value=Decimal('30'),
            is_active=True,
            )

        d.config = {'only_categories': {'categories': [c.pk]}}
        d.save()

        order = self.create_order()
        order.modify_item(p1.variations.get(), 3)
        order.modify_item(p2.variations.get(), 2)
        d.add_to(order)
        order.recalculate_total()

        # Test that only one order item has its discount applied
        self.assertEqual(Product.objects.all().count(), 2)
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(1,
            len([item for item in order.items.all() if item._line_item_discount]))

        self.assertEqual(Category.objects.active().count(), 1)
        self.assertEqual(Category.objects.public().count(), 0)
        self.assertEqual(unicode(Category.objects.create(
            name='blaa', slug='blaa', parent=c)), 'category - blaa')

    def test_10_discount_validation(self):
        """Test discount validity periods"""
        order = self.create_order()
        d = Discount(
            is_active=False,
            valid_from=date(2100, 1, 1), # far future date
            valid_until=None,
            )

        try:
            d.validate(order)
        except ValidationError, e:
            self.assertEqual(len(e.messages), 2)

        d.is_active = True
        d.valid_until = date(2000, 1, 1)

        try:
            d.validate(order)
        except ValidationError, e:
            self.assertEqual(len(e.messages), 2)

    def test_11_multiple_discounts(self):
        """Test behavior of orders with more than one discount"""
        order = self.create_order()
        product = self.create_product()
        order.modify_item(product.variations.get(), 3)
        order.recalculate_total()

        self.assertAlmostEqual(order.total, Decimal('239.70'))

        Discount.objects.create(
            type=Discount.PERCENTAGE,
            name='Percentage',
            code='perc20',
            value=Decimal('20.00'),
            is_active=True,
            ).add_to(order)
        order.recalculate_total()

        self.assertAlmostEqual(order.total, Decimal('239.70') / 5 * 4)

        # Add unsaved discount
        Discount(
            type=Discount.AMOUNT_INCL_TAX,
            name='Amount incl. tax',
            code='amount_incl_20',
            value=Decimal('20.00'),
            is_active=True,
            tax_class=self.tax_class,
            currency='CHF',
            ).add_to(order)
        order.recalculate_total()

        self.assertAlmostEqual(order.total, (Decimal('239.70') - 20) / 5 * 4)

    def test_12_order4567_test(self):
        """Reproduce order ID 4567 of a deployed satchmo shop installation"""
        order = self.create_order()

        p1 = self.create_product()
        p1.name = 'Kleid'
        p1.save()
        p1.prices.all().delete()
        p1.prices.create(
            _unit_price=160,
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class,
            )

        p2 = self.create_product()
        p2.prices.all().delete()
        p2.prices.create(
            _unit_price=280,
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class,
            )

        c = Category.objects.create(
            name='category',
            slug='category',
            )
        p1.categories.add(c)

        order.modify_item(p1.variations.get(), 1)
        order.modify_item(p2.variations.get(), 1)

        self.assertAlmostEqual(order.total, Decimal('440.00'))

        discount = Discount(
            type=Discount.PERCENTAGE,
            name='Sonderrabatt Kleid',
            value=Decimal('20.00'),
            code='1234code',
            )
        discount.config = {'only_categories': {'categories': [c.pk]}}
        discount.save()

        discount.add_to(order)
        order.recalculate_total()

        self.assertAlmostEqual(order.total, 408)
        self.assertAlmostEqual(order.subtotal, 440)
        self.assertAlmostEqual(order.discount, 32)

    def test_13_order4206_test(self):
        """Reproduce order ID 4206 of a deployed satchmo shop installation"""
        order = self.create_order()

        p1 = self.create_product()
        p1.name = 'Venice'
        p1.save()
        p1.prices.all().delete()
        p1.prices.create(
            _unit_price=170,
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class,
            )

        p2 = self.create_product()
        p2.prices.all().delete()
        p2.prices.create(
            _unit_price=Decimal('40.80'),
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class,
            )

        order.modify_item(p1.variations.get(), 1)
        order.modify_item(p2.variations.get(), 1)

        c = Category.objects.create(
            name='category',
            slug='category',
            )
        p1.categories.add(c)

        discount = Discount(
            type=Discount.AMOUNT_INCL_TAX,
            name='Sonderrabatt Venice',
            value=Decimal('20.00'),
            code='1234code',
            tax_class=self.tax_class,
            currency='CHF',
            )
        discount.config = {'only_categories': {'categories': [c.pk]}}
        discount.save()

        discount.add_to(order)
        order.recalculate_total()

        self.assertAlmostEqual(order.total, Decimal('190.80'))
        self.assertAlmostEqual(order.subtotal, Decimal('210.80'))
        self.assertAlmostEqual(order.discount, 20)

    def test_14_invoice2009_0170_0002_test(self):
        """Reproduce invoide of a deployed metronom installation"""
        order = self.create_order()

        p = self.create_product()
        p.prices.all().delete()
        p.prices.create(
            _unit_price=1,
            tax_included=False,
            currency=order.currency,
            tax_class=self.tax_class,
            )

        order.modify_item(p.variations.get(), 952)
        order.modify_item(p.variations.get(), 120)

        discount = Discount.objects.create(
            type=Discount.AMOUNT_EXCL_TAX,
            name='Discount',
            value=532,
            code='1234code',
            currency='CHF',
            )
        discount.add_to(order)

        order.recalculate_total()

        plata.settings.PLATA_PRICE_INCLUDES_TAX = False
        self.assertAlmostEqual(order.subtotal, Decimal('1072.00'))
        self.assertAlmostEqual(order.discount, Decimal('532.00'))
        self.assertAlmostEqual(order.items_tax, Decimal('41.04'))
        self.assertAlmostEqual(order.total, Decimal('581.04'))
        plata.settings.PLATA_PRICE_INCLUDES_TAX = True

    def test_15_remaining_discount(self):
        """Test determination of remaining discount amount"""
        order = self.create_order()
        product = self.create_product()

        order.modify_item(product.variations.get(), 1)
        self.assertAlmostEqual(order.total, Decimal('79.90'))

        Discount.objects.create(
            type=Discount.AMOUNT_INCL_TAX,
            name='Discount',
            value='100',
            code='1234code',
            tax_class=self.tax_class,
            currency='CHF',
            ).add_to(order)

        self.assertAlmostEqual(order.subtotal, Decimal('79.90'))
        self.assertAlmostEqual(order.discount, Decimal('79.90'))
        self.assertAlmostEqual(order.total, 0)
        self.assertAlmostEqual(order.discount_remaining, Decimal('20.10') / Decimal('1.076'))

    def test_16_payment(self):
        """Test order with payments and discounts"""
        order = self.create_order()
        product = self.create_product()

        order.modify_item(product.variations.get(), 3)
        self.assertAlmostEqual(order.balance_remaining, Decimal('79.90') * 3)

        payment = order.payments.create(
            currency=order.currency,
            amount=100,
            )

        self.assertAlmostEqual(order.balance_remaining, Decimal('79.90') * 3)

        payment.transaction_id = '1234' # Not strictly required
        payment.authorized = datetime.now()
        payment.save()

        order = Order.objects.get(pk=order.pk)
        self.assertAlmostEqual(order.balance_remaining, Decimal('139.70'))

        order.payments.create(
            currency=order.currency,
            amount=Decimal('139.70'),
            authorized=datetime.now(),
            )

        order = Order.objects.get(pk=order.pk)
        self.assertAlmostEqual(order.balance_remaining, Decimal('0.00'))
        self.assertTrue(order.is_paid())

        payment.delete()
        order = Order.objects.get(pk=order.pk)
        self.assertAlmostEqual(order.balance_remaining, Decimal('100.00'))

    def test_17_stocktransactions(self):
        """Simple stock transaction test"""
        order = self.create_order()
        product = self.create_product()
        variation = product.variations.get()

        period = Period.objects.create(
            name='Period 1',
            start=datetime.now(),
            )
        # Create a period which has been superceeded by Period 1
        Period.objects.create(
            name='Period 0',
            start=datetime(2000, 1, 1, 0, 0),
            )

        # Create a period in the far future
        Period.objects.create(
            name='Period 2',
            start=datetime(2100, 1, 1, 0, 0),
            )

        s = StockTransaction.objects.create(
            product=variation,
            type=StockTransaction.INITIAL,
            change=10,
            )

        self.assertEqual(s.period, period)
        self.assertEqual(ProductVariation.objects.get(pk=variation.id).items_in_stock, 10)

        StockTransaction.objects.create(
            product=variation,
            type=StockTransaction.CORRECTION,
            change=-3,
            )

        self.assertEqual(StockTransaction.objects.items_in_stock(variation), 7)

        StockTransaction.objects.create(
            product=variation,
            type=StockTransaction.SALE,
            change=-2,
            )

        StockTransaction.objects.create(
            product=variation,
            type=StockTransaction.PURCHASE,
            change=4,
            )

        StockTransaction.objects.open_new_period(name='Something')

        transaction = StockTransaction.objects.filter(product=variation)[0]

        self.assertEqual(transaction.type, StockTransaction.INITIAL)
        self.assertEqual(transaction.change, 9)
        self.assertEqual(transaction.period.name, 'Something')

    def test_18_amount_discount_incl_tax(self):
        """Test discount amounts specified with tax included"""
        p1 = self.create_product()
        p2 = self.create_product()

        price = p1.get_price(currency='CAD')
        price.tax_class = self.tax_class_germany
        price.save()

        order = self.create_order()
        order.currency = 'CAD'
        order.save()

        normal1 = order.modify_item(p1.variations.get(), 3)
        normal2 = order.modify_item(p2.variations.get(), 5)

        order.recalculate_total()
        #self.assertAlmostEqual(order.total, Decimal('598.84'))
        # We use ROUND_HALF_UP now
        self.assertAlmostEqual(order.total, Decimal('598.85'))

        discount = Discount.objects.create(
            type=Discount.AMOUNT_INCL_TAX,
            code='asdf',
            name='Amount discount',
            value=Decimal('50.00'),
            is_active=True,
            tax_class=self.tax_class_germany,
            currency='CAD',
            config_json='{"products": {"products": [%d]}}' % p1.id,
            )
        discount.add_to(order)
        order.recalculate_total()

        # Exact values after usage of different tax rates in same order
        #self.assertAlmostEqual(order.total, Decimal('548.84'))
        # We use ROUND_HALF_UP now
        self.assertAlmostEqual(order.total, Decimal('548.85'))
        self.assertAlmostEqual(order.discount, Decimal('50.00'))

    def test_19_product_methods(self):
        """Test product helper methods"""
        product = self.create_product()
        self.assertEqual(product.main_image, None)

    def test_20_shipping_discount(self):
        """Test applying discounts to shipping too"""
        order_processors = plata.settings.PLATA_ORDER_PROCESSORS[:]
        plata.settings.PLATA_ORDER_PROCESSORS[-2] = 'plata.shop.processors.FixedAmountShippingProcessor'

        p1 = self.create_product()
        p2 = self.create_product()
        order = self.create_order()
        normal1 = order.modify_item(p1.variations.get(), 3, recalculate=False)
        normal2 = order.modify_item(p2.variations.get(), 5)

        self.assertAlmostEqual(order.total, Decimal('639.20') + 8)

        discount = Discount.objects.create(
            type=Discount.AMOUNT_INCL_TAX,
            code='d2',
            name='d2',
            value=Decimal('640.00'),
            is_active=True,
            tax_class=self.tax_class,
            currency='CHF',
            )

        discount.add_to(order)
        self.assertAlmostEqual(order.total, Decimal('7.20'))

        discount.value = Decimal('650.00')
        discount.add_to(order)
        self.assertAlmostEqual(order.total, Decimal('0.00'))

        plata.settings.PLATA_ORDER_PROCESSORS = order_processors[:]

    def test_21_product_template_tags(self):
        """Test the product template tag library"""

        from options_product.templatetags import plata_product_tags
        product = self.create_product()
        product.categories.create(
            name='Category',
            slug='category',
            ordering=5,
            )

        Category.objects.create(name='Category 2', slug='category-2')

        c = Category.objects.all()
        plata_product_tags.featured_products_for_categories(c)

        self.assertEqual(Category.objects.count(), 2)
        self.assertEqual(product.categories.count(), 1)
        self.assertEqual(c[1].featured_product, product)
        self.assertFalse(hasattr(c[0], 'featured_product'))

    def test_22_tax_rounding(self):
        """Test tax rounding behavior"""

        p1 = self.create_product(stock=10)
        order = self.create_order()

        p1.prices.all().delete()
        p1.prices.create(
            _unit_price=Decimal('84.005'),
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class_germany,
            )

        order.modify_item(p1.variations.get(), absolute=1)

        self.assertEqual(order.total, Decimal('84.01'))


        p1.prices.all().delete()
        p1.prices.create(
            _unit_price=Decimal('84.0049999999'),
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class_germany,
            )

        order.modify_item(p1.variations.get(), absolute=1)

        self.assertEqual(order.total, Decimal('84.00'))


        p1.prices.all().delete()
        p1.prices.create(
            _unit_price=Decimal('84.0050000001'),
            tax_included=True,
            currency=order.currency,
            tax_class=self.tax_class_germany,
            )

        order.modify_item(p1.variations.get(), absolute=1)

        self.assertEqual(order.total, Decimal('84.01'))

    def test_23_mixed_tax(self):
        order_processors = plata.settings.PLATA_ORDER_PROCESSORS[:]
        plata.settings.PLATA_ORDER_PROCESSORS[-2] = 'plata.shop.processors.FixedAmountShippingProcessor'

        p1 = self.create_product(stock=10)
        p2 = self.create_product(stock=10)

        p2.name = 'Test 2'
        p2.save()

        p2.prices.all().delete()
        p2.prices.create(
            currency='CHF',
            tax_class=self.tax_class_something,
            _unit_price=Decimal('59.90'),
            tax_included=True,
            )

        order = self.create_order()

        order.modify_item(p1.variations.get(), 5)
        order.modify_item(p2.variations.get(), 5)

        self.assertEqual(order.items.count(), 2)
        self.assertAlmostEqual(order.total, Decimal('707.00'))

        tax_details = dict(order.data['tax_details'])

        # Two tax rates
        self.assertEqual(len(tax_details), 2)

        self.assertAlmostEqual(tax_details[Decimal('12.5')]['tax_amount'], Decimal('33.28'), 2)
        self.assertAlmostEqual(tax_details[Decimal('7.6')]['tax_amount'], Decimal('28.78'), 2)

        # Shipping has to be added here too; otherwise it should be 399.50
        self.assertAlmostEqual(tax_details[Decimal('7.6')]['total'], Decimal('407.50'))

        plata.settings.PLATA_ORDER_PROCESSORS = order_processors[:]

    def test_24_uninitialized_order(self):
        # This should not crash; generating a PDF exercises the methods
        # and properties of the order
        plata.reporting.order.invoice_pdf(PDFDocument(StringIO.StringIO()),
            Order.objects.create())

    def test_25_discount_validation(self):
        p1 = self.create_product(stock=10)

        discount = Discount.objects.create(
            is_active=True,
            type=Discount.PERCENTAGE,
            code='asdf',
            name='Percentage discount',
            value=30)

        discount.save() # should not raise

        discount.type = Discount.AMOUNT_EXCL_TAX
        self.assertRaises(ValidationError, lambda: discount.save())

        discount.currency = 'CHF'
        discount.save() # should not raise

        discount.tax_class = self.tax_class
        self.assertRaises(ValidationError, lambda: discount.save())

        discount.type = Discount.AMOUNT_INCL_TAX
        discount.save() # should not raise

        discount.currency = None
        self.assertRaises(ValidationError, lambda: discount.save())

        discount.type = 42
        self.assertRaises(ValidationError, lambda: discount.save())

        discount.type = Discount.AMOUNT_INCL_TAX
        discount.currency = 'EUR'
        discount.save()

        order = self.create_order()
        order.modify_item(p1.variations.get(), 3)

        self.assertRaises(ValidationError, lambda: discount.add_to(order))

        discount.currency = order.currency
        discount.save()

        discount.add_to(order) # should not raise

    def test_26_amount_coupon_incl_tax(self):
        """Test coupons"""
        tax_class, tax_class_germany, tax_class_something = self.create_tax_classes()

        product = Product.objects.create(
            name='Ein Paar Hosen',
            slug='prodeinpaarhosen1',
            )

        product.create_variations()
        product.prices.create(
            currency='CHF',
            tax_class=tax_class,
            _unit_price=Decimal('100.00'),
            tax_included=True,
            )

        price = product.get_price(currency='CHF')
        price.tax_class = tax_class
        price.save()

        order = self.create_order()
        order.save()

        normal1 = order.modify_item(product.variations.get(), 1)

        order.recalculate_total()
        # We use ROUND_HALF_UP now
        self.assertAlmostEqual(order.total, Decimal('100'))

        discount = Discount.objects.create(
            type=Discount.PREPAID,
            code='asdf',
            name='Amount discount',
            value=Decimal('20.00'),
            is_active=True,
            tax_class=tax_class,
            currency='CHF',
            )
        discount.add_to(order)
        order.recalculate_total()

        # We use ROUND_HALF_UP now
        self.assertAlmostEqual(order.total, Decimal('87.6'))

    def test_27_discounts(self):
        """Discount testing reloaded"""
        tax_class, tax_class_germany, tax_class_something = self.create_tax_classes()

        product = Product.objects.create(
            name='Ein Paar Hosen',
            slug='prodeinpaarhosen1',
            )

        product.create_variations()
        product.prices.create(
            currency='CHF',
            tax_class=tax_class,
            _unit_price=Decimal('100.00'),
            tax_included=True,
            )

        price = product.get_price(currency='CHF')
        price.tax_class = tax_class
        price.save()

        order = self.create_order()
        order.save()

        normal1 = order.modify_item(product.variations.get(), 1)

        order.recalculate_total()
        self.assertAlmostEqual(order.total, Decimal('100'))

        discount = Discount.objects.create(
            type=Discount.PREPAID,
            code='asdf',
            name='Amount discount',
            value=Decimal('20.00'),
            is_active=True,
            tax_class=tax_class,
            currency='CHF',
            )
        discount.add_to(order)
        order.recalculate_total()

        # Pre-paid discount -- tax still applies to undiscounted value
        self.assertAlmostEqual(order.total, Decimal('80.00'))
        self.assertAlmostEqual(order.subtotal, Decimal('100.00') / Decimal('1.076') - Decimal('20.00'))

        # Change something on the discount
        discount.before_tax = True # TODO implement this
        discount.add_to(order)
        order.recalculate_total()

        # Voucher from a magazine or something -- tax only applies to
        # discounted value
        self.assertAlmostEqual(order.total, Decimal('80.00'))
        self.assertAlmostEqual(order.subtotal, Decimal('80.00') / Decimal('1.076'))
