from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Fee, Student
from app.schemas import FeeCreate, FeeResponse, FeeUpdateStatus
from app.security.identity_records import current_student
from app.security.tenant_context import TenantContext, require_roles

router = APIRouter(prefix="/fees", tags=["fees"])


@router.get("", response_model=list[FeeResponse])
def list_fees(
    status_filter: str | None = Query(default=None, alias="status"),
    student_id: str | None = Query(default=None),
    ctx: TenantContext = Depends(require_roles("college_admin", "student")),
    db: Session = Depends(get_db),
) -> list[Fee]:
    """List fee invoices for students in the active tenant."""
    query = select(Fee).where(Fee.tenant_id == ctx.tenant.id)
    if ctx.role == "student":
        own_student = current_student(ctx, db)
        if student_id and student_id != own_student.id:
            raise HTTPException(status_code=404, detail="Fee records not found")
        query = query.where(Fee.student_id == own_student.id)
    if status_filter:
        query = query.where(Fee.status == status_filter)
    if student_id:
        query = query.where(Fee.student_id == student_id)
    query = query.order_by(Fee.due_date)
    return list(db.scalars(query).all())


@router.post("", response_model=FeeResponse, status_code=status.HTTP_201_CREATED)
def issue_fee_invoice(
    payload: FeeCreate,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> Fee:
    """Issue a fee invoice for a student in this tenant."""
    student = db.scalar(
        select(Student).where(Student.id == payload.student_id, Student.tenant_id == ctx.tenant.id)
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this tenant")

    fee = Fee(
        tenant_id=ctx.tenant.id,
        student_id=payload.student_id,
        description=payload.description,
        amount=payload.amount,
        due_date=payload.due_date,
        status="pending",
    )
    db.add(fee)
    db.commit()
    db.refresh(fee)
    return fee


@router.patch("/{fee_id}/status", response_model=FeeResponse)
def update_fee_status(
    fee_id: str,
    payload: FeeUpdateStatus,
    ctx: TenantContext = Depends(require_roles("college_admin")),
    db: Session = Depends(get_db),
) -> Fee:
    """Update payment status of a fee invoice."""
    fee = db.scalar(
        select(Fee).where(Fee.id == fee_id, Fee.tenant_id == ctx.tenant.id)
    )
    if not fee:
        raise HTTPException(status_code=404, detail="Fee invoice not found in this tenant")

    fee.status = payload.status
    if payload.status == "paid":
        fee.paid_date = payload.paid_date or date.today()
    elif payload.status == "pending":
        fee.paid_date = None

    db.commit()
    db.refresh(fee)
    return fee
