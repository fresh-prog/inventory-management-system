import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFound
from ..jsonenc import J, enc
from ..models import ProductionRun, Recipe, RecipeLine, RecipeVersion
from ..schemas.inputs import RecipeDraftIn, RecipeIn, RecipeVersionIn
from ..security import Principal, require
from ..services import recipes as svc
from .serializers import version_out

router = APIRouter(prefix="/recipes", tags=["recipes"])


def _get(db, recipe_id) -> Recipe:
    r = db.get(Recipe, recipe_id)
    if not r:
        raise NotFound("Recipe not found")
    return r


def _detail(db, p, r: Recipe) -> dict:
    db.refresh(r)
    current = next((v for v in r.versions if v.status == "published"), None)
    draft = next((v for v in r.versions if v.status == "draft"), None)
    return {"id": r.id, "code": r.code, "name": r.name, "category": r.category,
            "published_version": version_out(current) if current else None,
            "draft_version": version_out(draft) if draft else None,
            "versions": [{"version_no": v.version_no, "status": v.status, "created_at": v.created_at,
                          "published_at": v.published_at, "change_note": v.change_note} for v in r.versions],
            "cost": svc.compute_cost(db, p.branch, current) if current else None,
            "draft_cost": svc.compute_cost(db, p.branch, draft) if draft else None}


@router.get("")
def list_recipes(p: Principal = Depends(require("recipe.read")), db: Session = Depends(get_db)):
    """Costs come from each published version's latest cost snapshot, which is re-computed on every
    ingredient price/conversion change and on publish — so it IS the current cost, without
    re-costing hundreds of recipes on every request."""
    from sqlalchemy import func
    from ..services.reports import recipe_food_costs
    costs = {r["recipe_id"]: r for r in recipe_food_costs(db, p.branch.id)}
    versions = db.execute(select(RecipeVersion.recipe_id, RecipeVersion.version_no, RecipeVersion.status,
                                 RecipeVersion.portions_per_batch)).all()
    pub = {v.recipe_id: v for v in versions if v.status == "published"}
    drafts = {v.recipe_id for v in versions if v.status == "draft"}
    out = []
    for r in db.execute(select(Recipe.id, Recipe.code, Recipe.name, Recipe.category).order_by(Recipe.name)):
        v, c = pub.get(r.id), costs.get(r.id)
        out.append({"id": r.id, "code": r.code, "name": r.name, "category": r.category,
                    "published_version_no": v.version_no if v else None, "has_draft": r.id in drafts,
                    "portions_per_batch": v.portions_per_batch if v else None,
                    "cost_per_portion": c["cost_per_portion"] if c else None,
                    "selling_price": c["selling_price"] if c else None,
                    "food_cost_rate": c["food_cost_rate"] if c else None,
                    "target_food_cost_rate": c["target_food_cost_rate"] if c else None,
                    "recommended_price": c["recommended_price"] if c else None,
                    "exceeds_target": c["exceeds_target"] if c else False, "cost_basis": "current"})
    return J(out)


@router.post("/cost-preview")
def cost_preview(body: RecipeVersionIn, p: Principal = Depends(require("recipe.read")),
                 db: Session = Depends(get_db)):
    """Cost an unsaved recipe (live editor). Nothing is written."""
    data = body.model_dump()
    data["target_food_cost_rate"] = data.get("target_food_cost_rate") or p.branch.default_target_food_cost_rate
    v = RecipeVersion(version_no=0, status="draft")
    for k in ("portions_per_batch", "cooking_shrinkage_rate", "target_food_cost_rate", "selling_price"):
        setattr(v, k, data[k])
    from ..services.ingredients import get_ingredient
    v.lines = [RecipeLine(ingredient_id=l["ingredient_id"], ingredient=get_ingredient(db, l["ingredient_id"]),
                          quantity=l["quantity"], unit=l["unit"], basis=l["basis"],
                          prep_loss_rate=l["prep_loss_rate"]) for l in data["lines"]]
    result = svc.compute_cost(db, p.branch, v)
    db.rollback()
    return J(result)


@router.post("", status_code=201)
def create(body: RecipeIn, p: Principal = Depends(require("recipe.write")), db: Session = Depends(get_db)):
    r = svc.create_recipe(db, p, body.model_dump())
    db.commit()
    return J(_detail(db, p, r), 201)


@router.get("/{recipe_id}")
def get(recipe_id: uuid.UUID, p: Principal = Depends(require("recipe.read")), db: Session = Depends(get_db)):
    return J(_detail(db, p, _get(db, recipe_id)))


@router.put("/{recipe_id}/draft")
def save_draft(recipe_id: uuid.UUID, body: RecipeDraftIn, p: Principal = Depends(require("recipe.write")),
               db: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    data.setdefault("lines", body.model_dump()["lines"])
    if data.get("target_food_cost_rate") is None:
        data.pop("target_food_cost_rate", None)
    svc.save_draft(db, p, _get(db, recipe_id), data)
    db.commit()
    return J(_detail(db, p, _get(db, recipe_id)))


@router.post("/{recipe_id}/versions/{version_no}/publish")
def publish(recipe_id: uuid.UUID, version_no: int, p: Principal = Depends(require("recipe.publish")),
            db: Session = Depends(get_db)):
    svc.publish(db, p, _get(db, recipe_id), version_no)
    db.commit()
    return J(_detail(db, p, _get(db, recipe_id)))


@router.get("/{recipe_id}/versions/{version_no}")
def get_version(recipe_id: uuid.UUID, version_no: int, p: Principal = Depends(require("recipe.read")),
                db: Session = Depends(get_db)):
    v = svc.get_version(db, recipe_id, version_no)
    return J({**version_out(v), "cost": svc.compute_cost(db, p.branch, v)})


@router.get("/{recipe_id}/cost-history")
def cost_history(recipe_id: uuid.UUID, p: Principal = Depends(require("recipe.read")),
                 db: Session = Depends(get_db)):
    rows = svc.snapshots(db, p.branch.id, recipe_id)
    out = []
    for i, (s, vno) in enumerate(rows):
        prev = rows[i + 1][0] if i + 1 < len(rows) else None
        out.append({"id": s.id, "version_no": vno, "computed_at": s.computed_at, "trigger": s.trigger,
                    "trigger_ref": s.trigger_ref, "batch_cost": s.batch_cost,
                    "cost_per_portion": s.cost_per_portion, "recommended_price": s.recommended_price,
                    "food_cost_rate": s.food_cost_rate, "exceeds_target": s.exceeds_target,
                    "change_per_portion": (s.cost_per_portion - prev.cost_per_portion) if prev else None,
                    "cost_basis": "current", "detail": s.detail})
    return J(out)


@router.get("/{recipe_id}/production-runs")
def production_runs(recipe_id: uuid.UUID, p: Principal = Depends(require("recipe.read")),
                    db: Session = Depends(get_db)):
    runs = db.scalars(select(ProductionRun).join(RecipeVersion)
                      .where(RecipeVersion.recipe_id == recipe_id, ProductionRun.branch_id == p.branch.id)
                      .order_by(ProductionRun.produced_at.desc()).limit(50)).all()
    return J([{"id": r.id, "version_no": r.recipe_version.version_no, "batches": r.batches,
                 "expected_portions": r.expected_portions, "actual_portions": r.actual_portions,
                 "total_cost": r.total_cost, "cost_per_portion": r.cost_per_portion, "cost_basis": "fifo",
                 "produced_at": r.produced_at} for r in runs])
