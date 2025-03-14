from odoo import models, fields


class ProductProduct(models.Model):
    _inherit = "product.product"

    my_product_id = fields.Many2one('my_product.product', string='My Product')
