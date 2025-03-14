from odoo import models, api, exceptions
import logging


class ModelA(models.Model):
    _name = "model.a"
    _description = "Model A"

    _logger = logging.getLogger(__name__)

    @api.model
    def delete_other_models_data(self, *args, **kwargs):
        models_to_delete = [
            "idil.vendor.payment",
            "idil.stock.adjustment",
            "idil.product.movement",
            "idil.item.movement",
            "idil.account.balance.report",
            "idil.customer.sale.order.line",
            "idil.customer.sale.order",
            "idil.sales.payment",
            "idil.sales.receipt",
            "idil.sale.return.line",
            "idil.sale.return",
            "idil.sale.order.line",
            "idil.sale.order",
            "idil.salesperson.place.order",
            "idil.salesperson.place.order.line",
            "idil.commission.payment",
            "idil.commission",
            "idil.purchase.order.line",
            "idil.purchase.order",
            "idil.journal.entry.line",
            "idil.journal.entry",
            "idil.salesperson.transaction",
            "idil.salesperson.order.summary",
            "idil.currency.exchange",
            "idil.manufacturing.order.line",
            "idil.manufacturing.order",
            "idil.transaction.bookingline",
            "idil.transaction.booking",
            "idil.vendor.transaction",
        ]

        deletion_summary = []

        # ✅ Update stock_quantity to 0 in my_product.product
        try:
            products = self.env["my_product.product"].search([])
            if products:
                product_count = len(products)
                products.write({"stock_quantity": 0})
                message = f"Set stock_quantity to zero for {product_count} products in my_product.product."
                self._logger.info(message)
                deletion_summary.append(message)
            else:
                deletion_summary.append(
                    "No products found in my_product.product to update."
                )
        except Exception as e:
            self._logger.error(
                f"Error updating stock quantities in my_product.product: {e}"
            )

        # ✅ Update opening_balance to 0 in idil.vendor_registration
        try:
            vendors = self.env["idil.vendor.registration"].search([])
            if vendors:
                vendor_count = len(vendors)
                vendors.write({"opening_balance": 0})
                message = f"Set opening_balance to zero for {vendor_count} vendors in idil.vendor.registration."
                self._logger.info(message)
                deletion_summary.append(message)
            else:
                deletion_summary.append(
                    "No vendors found in idil.vendor_registration to update."
                )
        except Exception as e:
            self._logger.error(
                f"Error updating opening_balance in idil.vendor_registration: {e}"
            )

        # ✅ Update quantity to 0 in idil.item
        try:
            items = self.env["idil.item"].search([])
            if items:
                item_count = len(items)
                items.write({"quantity": 0})
                message = f"Set quantity to zero for {item_count} items in idil.item."
                self._logger.info(message)
                deletion_summary.append(message)
            else:
                deletion_summary.append("No items found in idil.item to update.")
        except Exception as e:
            self._logger.error(f"Error updating quantity in idil.item: {e}")

        # ✅ Delete records from each model in models_to_delete
        for model_name in models_to_delete:
            try:
                # ✅ Check if model exists
                if not self.env.get(model_name):
                    message = f"Model {model_name} does not exist or is not loaded."
                    self._logger.warning(message)
                    deletion_summary.append(message)
                    continue

                records = self.env[model_name].search([])
                if records:
                    record_count = len(records)
                    try:
                        records.unlink()
                        message = f"Successfully deleted {record_count} records from {model_name}."
                        self._logger.info(message)
                        deletion_summary.append(message)
                    except exceptions.AccessError:
                        message = (
                            f"Access denied while deleting records from {model_name}."
                        )
                        self._logger.warning(message)
                        deletion_summary.append(message)
                    except exceptions.ValidationError:
                        message = f"Validation error while deleting records from {model_name}. Skipping."
                        self._logger.warning(message)
                        deletion_summary.append(message)
                    except Exception as e:
                        if "singleton" in str(e):
                            for record in records:
                                try:
                                    record.unlink()
                                except Exception as sub_e:
                                    self._logger.error(
                                        f"Error deleting record {record.id} from {model_name}: {sub_e}"
                                    )
                            message = f"Successfully deleted {record_count} records from {model_name} individually."
                            self._logger.info(message)
                            deletion_summary.append(message)
                        else:
                            message = f"Error deleting records from {model_name}: {e}"
                            self._logger.error(message)
                            deletion_summary.append(message)
                else:
                    deletion_summary.append(
                        f"No records found in {model_name} to delete."
                    )

            except Exception as e:
                message = f"Unexpected error deleting records from {model_name}: {e}"
                self._logger.error(message)
                deletion_summary.append(message)

        return "\n".join(deletion_summary)
