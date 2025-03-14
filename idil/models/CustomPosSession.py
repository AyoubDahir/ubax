from odoo import models, api


class CustomPosSession(models.Model):
    _inherit = 'pos.session'

    def _load_model(self, model_name):
        if model_name == 'res.partner':
            # Using a direct string reference to avoid KeyError
            model_obj = self.env['idil.customer.registration']
            customers = model_obj.search([])  # Assuming you might want all active customers or add your domain
            return [
                {
                    'id': customer.id,
                    'name': customer.name,
                    # Add other necessary fields
                    'phone': customer.phone,
                    'email': customer.email,
                    'gender': customer.gender,
                    'status': customer.status,
                    'image': customer.image
                }
                for customer in customers
            ]
        else:
            return super(CustomPosSession, self)._load_model(model_name)
