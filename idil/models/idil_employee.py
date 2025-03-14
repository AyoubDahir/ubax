from odoo import models, fields, api


class IdilEmployee(models.Model):
    _name = 'idil.employee'
    _description = 'Employee'
    _order = 'name'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True)
    private_phone = fields.Char(string='Private Phone')
    private_email = fields.Char(string='Private Email')
    gender = fields.Selection([
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other')
    ], string='Gender')
    marital = fields.Selection([
        ('single', 'Single'),
        ('married', 'Married'),
        ('cohabitant', 'Legal Cohabitant'),
        ('widower', 'Widower'),
        ('divorced', 'Divorced')
    ], string='Marital Status')
    employee_type = fields.Selection([
        ('employee', 'Employee'),
        ('student', 'Student'),
        ('trainee', 'Trainee'),
        ('contractor', 'Contractor'),
        ('freelance', 'Freelancer')
    ], string='Employee Type')
    pin = fields.Char(string='PIN', copy=False,
                      help='PIN used to Check In/Out in the Kiosk Mode of the Attendance application '
                           '(if enabled in Configuration) and to change the cashier in the Point of Sale application.')
    image_1920 = fields.Image(string="Image", max_width=1920, max_height=1920)

    currency_id = fields.Many2one('res.currency', string='Currency', required=True,
                                  default=lambda self: self.env.company.currency_id)

    account_id = fields.Many2one('idil.chart.account', string='Commission Account',
                                 domain="[('account_type', 'like', 'commission'), ('code', 'like', '2%'), "
                                        "('currency_id', '=', currency_id)]"
                                 )

    commission = fields.Float(string='Commission Percentage')

    @api.onchange('currency_id')
    def _onchange_currency_id(self):
        """Updates the domain for account_id based on the selected currency."""
        for employee in self:
            if employee.currency_id:
                return {
                    'domain': {
                        'account_id': [
                            ('account_type', 'like', 'commission'),
                            ('code', 'like', '2%'),
                            ('currency_id', '=', employee.currency_id.id)
                        ]
                    }
                }
            else:
                return {
                    'domain': {
                        'account_id': [
                            ('account_type', 'like', 'commission'),
                            ('code', 'like', '2%')
                        ]
                    }
                }

    @api.model
    def create(self, vals):
        # Create the record in idil.employee
        record = super(IdilEmployee, self).create(vals)
        # Create the same record in hr.employee
        self.env['hr.employee'].create({
            'name': record.name,
            'company_id': record.company_id.id,
            'private_phone': record.private_phone,
            'private_email': record.private_email,
            'gender': record.gender,
            'marital': record.marital,
            'employee_type': record.employee_type,
            'pin': record.pin,
            'image_1920': record.image_1920,

        })
        return record

    def write(self, vals):
        # Update the record in idil.employee
        res = super(IdilEmployee, self).write(vals)
        # Update the same record in hr.employee
        for record in self:
            hr_employee = self.env['hr.employee'].search([('name', '=', record.name)])
            if hr_employee:
                hr_employee.write({
                    'name': vals.get('name', record.name),
                    'company_id': vals.get('company_id', record.company_id.id),
                    'private_phone': vals.get('private_phone', record.private_phone),
                    'private_email': vals.get('private_email', record.private_email),
                    'gender': vals.get('gender', record.gender),
                    'marital': vals.get('marital', record.marital),
                    'employee_type': vals.get('employee_type', record.employee_type),
                    'pin': vals.get('pin', record.pin),
                    'image_1920': record.image_1920,

                })
        return res
