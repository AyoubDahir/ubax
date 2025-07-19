from odoo import models, fields


class BOMType(models.Model):
    _name = "idil.bom.type"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "BOM Types"

    name = fields.Char(string="BOM Type", required=True, tracking=True)
    description = fields.Text(string="Description", tracking=True)
