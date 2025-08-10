from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class SystemClearingWizard(models.TransientModel):
    _name = "system.clearing.wizard"
    _description = "System Clearing Wizard"

    confirm = fields.Boolean(
        string="Confirm",
        help="Check this box to confirm system clearing",
        default=False,
    )

    def action_clear_system_data(self):
        if not self.confirm:
            raise Warning("Please confirm before running system clearing!")

        query = """
        update my_product_product set stock_quantity =0;
        update my_product_product set actual_cost =0;
        update idil_item set quantity =0;
        Update public.idil_vendor_registration set opening_balance =0;
        Delete from idil_transaction_bookingline;
        Delete from idil_transaction_booking;
        Delete from idil_purchase_return;
        Delete FROM public.idil_purchase_order_line;
        Delete FROM public.idil_purchase_order;
        Delete from idil_product_purchase_return;
        Delete from idil_product_purchase_order;
        Delete from idil_product_purchase_return_line;
        Delete FROM public.idil_vendor_payment;
        Delete from idil_vendor_transaction;
        Delete from idil_vendor_opening_balance_line;
        Delete from idil_vendor_opening_balance;
        Delete FROM public.idil_vendor_bulk_payment;
        Delete FROM public.idil_vendor_bulk_payment_line;
        Delete FROM public.idil_commission_payment;
        Delete FROM public.idil_commission_bulk_payment_line;
        Delete FROM public.idil_commission_bulk_payment;
        Delete FROM public.idil_commission;
        Delete from idil_manufacturing_order;
        Delete from idil_manufacturing_order_line;
        Delete from idil_receipt_bulk_payment_line;
        Delete from idil_receipt_bulk_payment_method;
        Delete from idil_receipt_bulk_payment;
        Delete FROM public.idil_sales_receipt;
        Delete FROM public.idil_sale_return_line;
        Delete FROM public.idil_sale_return;
        Delete FROM public.idil_sale_order;
        Delete from idil_customer_sale_return_line;
        Delete from idil_customer_sale_return;
        Delete FROM public.idil_sales_payment;
        Delete FROM public.idil_customer_sale_payment;
        Delete FROM public.idil_customer_sale_order_line;
        Delete FROM public.idil_customer_sale_order;
        Delete FROM public.idil_journal_entry;
        Delete FROM public.idil_salesperson_transaction;
        Delete FROM public.idil_salesperson_place_order;
        Delete FROM public.idil_salesperson_place_order_line;
        Delete FROM public.idil_salesperson_order_summary;
        Delete FROM public.idil_employee_salary_advance;
        Delete FROM public.idil_employee_salary;
        Delete FROM public.idil_currency_exchange;
        Delete FROM public.idil_item_opening_balance;
        Delete FROM public.idil_item_opening_balance_line;
        Delete from idil_sales_opening_balance_line;
        Delete from idil_sales_opening_balance;
        Delete from idil_customer_opening_balance;
        Delete from idil_customer_opening_balance_line;
        Delete FROM public.idil_product_adjustment;
        Delete FROM public.idil_stock_adjustment;
        Delete FROM public.idil_product_movement;
        Delete FROM public.idil_item_movement;
        Delete from my_product_opening_balance;
        Delete from my_product_opening_balance_line;     
        Delete from idil_staff_sales;
        Delete from idil_staff_sales_line;
        Delete from idil_customer_place_order_line;
        Delete from idil_customer_place_order;
 

        """

        self.env.cr.execute(query)
        self.env.cr.commit()
        _logger.info("✅ System clearing completed successfully.")
