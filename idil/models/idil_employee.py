from datetime import date

from odoo import models, fields, api


class IdilEmployee(models.Model):
    _name = "idil.employee"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Employee"
    _order = "name"
    _order = "id desc"

    name = fields.Char(required=True, tracking=True)
    staff_id = fields.Char(string="Employee Id", tracking=True)

    company_id = fields.Many2one("res.company", required=True, tracking=True)
    department_id = fields.Many2one("idil.employee_department", tracking=True)
    position_id = fields.Many2one("idil.employee_position", tracking=True)

    private_phone = fields.Char(string="Private Phone", tracking=True)
    private_email = fields.Char(string="Private Email", tracking=True)
    gender = fields.Selection(
        [("male", "Male"), ("female", "Female"), ("other", "Other")],
        string="Gender",
        tracking=True,
    )
    marital = fields.Selection(
        [
            ("single", "Single"),
            ("married", "Married"),
            ("cohabitant", "Legal Cohabitant"),
            ("widower", "Widower"),
            ("divorced", "Divorced"),
        ],
        string="Marital Status",
        tracking=True,
    )
    employee_type = fields.Selection(
        [
            ("employee", "Employee"),
            ("student", "Student"),
            ("trainee", "Trainee"),
            ("contractor", "Contractor"),
            ("freelance", "Freelancer"),
        ],
        string="Employee Type",
        tracking=True,
    )
    pin = fields.Char(
        string="PIN",
        copy=False,
        help="PIN used to Check In/Out in the Kiosk Mode of the Attendance application "
        "(if enabled in Configuration) and to change the cashier in the Point of Sale application.",
        tracking=True,
    )
    image_1920 = fields.Image(
        string="Image", max_width=1920, max_height=1920, tracking=True
    )

    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        tracking=True,
    )

    account_id = fields.Many2one(
        "idil.chart.account",
        string="Commission Account",
        domain="[('account_type', 'like', 'commission'), ('code', 'like', '2%'), "
        "('currency_id', '=', currency_id)]",
        tracking=True,
    )

    account_receivable_id = fields.Many2one(
        "idil.chart.account",
        string="Sales Receivable Account",
        domain="[('account_type', 'like', 'receivable'), ('code', 'like', '1%'), "
        "('currency_id', '=', currency_id)]",
        help="Select the receivable account for transactions.",
        required=True,
    )

    commission = fields.Float(string="Commission Percentage", tracking=True)

    # Salary and bonus information
    salary = fields.Monetary(
        string="Basic Salary", currency_field="currency_id", tracking=True
    )
    bonus = fields.Monetary(string="Bonus", currency_field="currency_id", tracking=True)
    total_compensation = fields.Monetary(
        string="Total Compensation",
        compute="_compute_total_compensation",
        currency_field="currency_id",
        store=True,
        tracking=True,
    )
    # Contract details
    contract_start_date = fields.Date(string="Contract Start Date", tracking=True)
    contract_end_date = fields.Date(string="Contract End Date", tracking=True)
    contract_type = fields.Selection(
        [
            ("permanent", "Permanent"),
            ("temporary", "Temporary"),
            ("internship", "Internship"),
            ("freelance", "Freelance"),
        ],
        string="Contract Type",
        tracking=True,
    )

    # Leaves and attendance
    leave_balance = fields.Float(string="Leave Balance", defualt=100.0, tracking=True)
    maker_checker = fields.Boolean(
        string="Maker & Checker", default=False, tracking=True
    )
    salary_history_ids = fields.One2many(
        "idil.employee.salary", "employee_id", string="Salary History", tracking=True
    )

    advance_history_ids = fields.One2many(
        "idil.employee.salary.advance",
        "employee_id",
        string="Advance History",
        tracking=True,
    )
    # Status field
    status = fields.Selection(
        [
            ("active", "Active"),
            ("inactive", "Inactive"),
        ],
        string="Status",
        compute="_compute_status",
        store=True,
        tracking=True,
    )
    user_id = fields.Many2one(
        "res.users", string="User", help="Link to the related Odoo user", tracking=True
    )
    customer_id = fields.Many2one("idil.customer.registration", string="Customer")

    @api.depends("contract_start_date", "contract_end_date")
    def _compute_status(self):
        today = date.today()
        for record in self:
            if record.contract_end_date and record.contract_end_date < today:
                record.status = "inactive"
            elif record.contract_start_date and (
                not record.contract_end_date or record.contract_end_date >= today
            ):
                record.status = "active"
            else:
                record.status = "inactive"

    @api.depends("salary", "bonus")
    def _compute_total_compensation(self):
        for record in self:
            record.total_compensation = (record.salary or 0.0) + (record.bonus or 0.0)

    @api.onchange("currency_id")
    def _onchange_currency_id(self):
        """Updates the domain for account_id based on the selected currency."""
        for employee in self:
            if employee.currency_id:
                return {
                    "domain": {
                        "account_id": [
                            ("account_type", "like", "commission"),
                            ("code", "like", "2%"),
                            ("currency_id", "=", employee.currency_id.id),
                        ]
                    }
                }
            else:
                return {
                    "domain": {
                        "account_id": [
                            ("account_type", "like", "commission"),
                            ("code", "like", "2%"),
                        ]
                    }
                }

    @api.model
    def create(self, vals):
        # Create the record in idil.employee
        record = super(IdilEmployee, self).create(vals)
        # Create the same record in hr.employee
        self.env["hr.employee"].create(
            {
                "idil_staff_id": record.id,
                "name": record.name,
                "company_id": record.company_id.id,
                "private_phone": record.private_phone,
                "work_phone": record.private_phone,
                "mobile_phone": record.private_phone,
                "private_email": record.private_email,
                "work_email": record.private_email,
                "gender": record.gender,
                "marital": record.marital,
                "employee_type": record.employee_type,
                "pin": record.pin,
                "image_1920": record.image_1920,
            }
        )
        return record

    def write(self, vals):
        # Update the record in idil.employee
        res = super(IdilEmployee, self).write(vals)
        # Update the same record in hr.employee
        for record in self:
            hr_employee = self.env["hr.employee"].search(
                [("idil_staff_id", "=", record.id)]
            )
            if hr_employee:
                hr_employee.write(
                    {
                        "name": vals.get("name", record.name),
                        "company_id": vals.get("company_id", record.company_id.id),
                        "private_phone": vals.get(
                            "private_phone", record.private_phone
                        ),
                        "work_phone": vals.get("private_phone", record.private_phone),
                        "mobile_phone": vals.get("private_phone", record.private_phone),
                        "private_email": vals.get(
                            "private_email", record.private_email
                        ),
                        "work_email": vals.get("private_email", record.private_email),
                        "gender": vals.get("gender", record.gender),
                        "marital": vals.get("marital", record.marital),
                        "employee_type": vals.get(
                            "employee_type", record.employee_type
                        ),
                        "pin": vals.get("pin", record.pin),
                        "image_1920": record.image_1920,
                    }
                )
        return res


class IdilEmployeeDepartment(models.Model):
    _name = "idil.employee_department"
    _description = "Employee Department"
    _order = "name"
    _order = "id desc"

    name = fields.Char(required=True)


class IdilEmployeePosition(models.Model):
    _name = "idil.employee_position"
    _description = "Employee Position"
    _order = "id desc"

    name = fields.Char(required=True)


class HrEmployeeInherit(models.Model):
    _inherit = "hr.employee"

    idil_staff_id = fields.Integer(string="IDIL Staff ID")


class HREmployee(models.Model):
    _inherit = "hr.employee"

    merchant = fields.Char(string="Merchant")
