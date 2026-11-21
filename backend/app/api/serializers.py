from decimal import Decimal

from ..models import Ingredient, PurchaseInvoice, RecipeVersion, Supplier, SupplierPayment
from ..services.ingredients import base_unit


def ingredient_out(ing: Ingredient, *, setting=None, cost=None, suppliers=None, on_hand=None) -> dict:
    return {
        "id": ing.id, "code": ing.code, "name": ing.name,
        "category": ing.category.name if ing.category else None, "description": ing.description,
        "base_dimension": ing.base_dimension, "base_unit": base_unit(ing),
        "purchase_unit": ing.purchase_unit, "consumption_unit": ing.consumption_unit,
        "is_active": ing.is_active, "version": ing.version, "updated_at": ing.updated_at,
        "conversions": [{"from_unit": c.from_unit, "from_qty": c.from_qty, "to_unit": c.to_unit,
                         "to_qty": c.to_qty, "note": c.note} for c in ing.conversions],
        "min_stock_base": setting.min_stock_base if setting else Decimal(0),
        "current_cost": None if cost is None else {
            "cost_per_base_unit": cost.cost_per_base_unit, "currency": cost.currency,
            "effective_date": cost.effective_date, "cost_basis": "current"},
        "suppliers": suppliers or [],
        "on_hand_base": on_hand,
    }


def supplier_out(s: Supplier, balance=None) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "tax_id": s.tax_id,
            "contact_person": s.contact_person, "phone": s.phone, "email": s.email,
            "address": s.address, "payment_terms_days": s.payment_terms_days, "notes": s.notes,
            "is_active": s.is_active, "version": s.version, "balance": balance}


def purchase_out(inv: PurchaseInvoice, balance: dict, extra: dict | None = None) -> dict:
    return {
        "id": inv.id, "supplier_id": inv.supplier_id, "supplier_name": inv.supplier.name,
        "invoice_ref": inv.invoice_ref, "supplier_tax_ref": inv.supplier_tax_ref,
        "invoice_date": inv.invoice_date, "received_at": inv.received_at, "due_date": inv.due_date,
        "currency": inv.currency, "subtotal": inv.subtotal, "discount_total": inv.discount_total,
        "tax_total": inv.tax_total, "total": inv.total, "status": inv.status, "notes": inv.notes,
        "created_at": inv.created_at, "void_reason": inv.void_reason, **balance,
        "lines": [{"id": l.id, "line_no": l.line_no, "ingredient_id": l.ingredient_id,
                   "ingredient_name": l.ingredient.name, "description": l.description,
                   "quantity": l.quantity, "unit": l.unit, "unit_price": l.unit_price,
                   "line_discount": l.line_discount, "line_total": l.line_total,
                   "base_quantity": l.base_quantity, "base_unit": base_unit(l.ingredient),
                   "stock_cost": l.stock_cost, "cost_per_base_unit": l.cost_per_base_unit}
                  for l in inv.lines],
        **(extra or {}),
    }


def payment_out(pay: SupplierPayment) -> dict:
    return {"id": pay.id, "supplier_id": pay.supplier_id, "paid_on": pay.paid_on, "amount": pay.amount,
            "currency": pay.currency, "method": pay.method, "reference": pay.reference,
            "notes": pay.notes, "status": pay.status, "created_at": pay.created_at,
            "void_reason": pay.void_reason,
            "allocations": [{"invoice_id": a.invoice_id, "amount": a.amount} for a in pay.allocations]}


def version_out(v: RecipeVersion) -> dict:
    return {
        "id": v.id, "version_no": v.version_no, "status": v.status,
        "portions_per_batch": v.portions_per_batch, "portion_size_qty": v.portion_size_qty,
        "portion_size_unit": v.portion_size_unit, "cooking_shrinkage_rate": v.cooking_shrinkage_rate,
        "target_food_cost_rate": v.target_food_cost_rate, "selling_price": v.selling_price,
        "prep_instructions": v.prep_instructions, "cooking_instructions": v.cooking_instructions,
        "change_note": v.change_note, "created_at": v.created_at, "published_at": v.published_at,
        "lines": [{"ingredient_id": l.ingredient_id, "ingredient_name": l.ingredient.name,
                   "quantity": l.quantity, "unit": l.unit, "basis": l.basis,
                   "prep_loss_rate": l.prep_loss_rate} for l in v.lines],
    }
