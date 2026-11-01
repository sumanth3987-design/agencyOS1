from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Project, Payment, ActivityLog

wire_bp = Blueprint('wire_bp', __name__, url_prefix='/payment/wire')


@wire_bp.route('/select/<int:project_id>', methods=['POST'])
@login_required
def select_wire_transfer(project_id):
    """
    Allows a client or admin to select Wire Transfer as the payment method
    for a project in a payable state.
    """
    # 1. Authorization guard (matching Phase 1 strict rules)
    if current_user.role not in ('customer', 'client', 'admin'):
        return jsonify({"error": "Unauthorized access."}), 403

    project = Project.query.get_or_404(project_id)

    # 2. Payable state enforcement
    if project.status not in ("Payment Pending", "Pending Assignment"):
        return jsonify({"error": "Project is not in a payable state."}), 400

    # 3. Check if an active/paid payment already exists
    existing_paid = Payment.query.filter_by(project_id=project.id, status='Paid').first()
    if existing_paid:
        return jsonify({"error": "A paid payment already exists for this project."}), 400

    # 4. Create or update payment record for Wire tracking
    try:
        payment = Payment.query.filter_by(project_id=project.id, gateway_type='wire').first()
        if not payment:
            payment = Payment(
                project_id=project.id,
                customer_id=project.customer_id,
                gateway_type='wire',
                status='awaiting_wire',
                currency=project.currency,
                amount=project.amount
            )
            db.session.add(payment)
        else:
            payment.status = 'awaiting_wire'

        project.status = "Awaiting Wire Transfer"
        db.session.commit()

        # Return bank instructions to client frontend
        wire_instructions = {
            "bank_name": current_app.config.get("WIRE_BANK_NAME"),
            "account_name": current_app.config.get("WIRE_ACCOUNT_NAME"),
            "account_number": current_app.config.get("WIRE_ACCOUNT_NUMBER"),
            "swift_bic": current_app.config.get("WIRE_SWIFT_BIC"),
            "iban": current_app.config.get("WIRE_IBAN"),
            "reference_code": f"AGENCYOS-PRJ-{project.id}"
        }

        return jsonify({
            "message": "Wire transfer selected successfully. Please transfer funds using the instructions provided.",
            "instructions": wire_instructions
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Wire selection error for project {project_id}: {str(e)}")
        return jsonify({"error": "Failed to initialize wire transfer processing."}), 500


@wire_bp.route('/admin/reconcile/<int:project_id>', methods=['POST'])
@login_required
def admin_reconcile_wire(project_id):
    """
    Admin-only endpoint to verify incoming wire funds and mark payment as Paid.
    """
    if current_user.role != 'admin':
        return jsonify({"error": "Admin privileges required."}), 403

    project = Project.query.get_or_404(project_id)
    payment = Payment.query.filter_by(project_id=project.id, gateway_type='wire').first()

    if not payment or payment.status != 'awaiting_wire':
        return jsonify({"error": "No pending wire transfer found for this project."}), 400

    try:
        # State-safe transition: awaiting_wire -> Paid
        payment.status = 'Paid'
        project.status = 'Paid'

        # Log the admin reconciliation action
        ActivityLog.log_event(
            project_id=project.id,
            action="WIRE_PAYMENT_RECONCILED",
            description=f"Wire transfer for project {project.id} reconciled and marked as Paid.",
            user_id=current_user.id,
            category="success"
        )

        db.session.commit()

        return jsonify({
            "message": f"Wire transfer for project {project_id} successfully reconciled and marked as Paid."
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Wire reconciliation error for project {project_id}: {str(e)}")
        return jsonify({"error": "Failed to reconcile wire transfer."}), 500