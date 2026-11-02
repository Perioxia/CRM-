from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

from app.database import get_db
from app.utils.dependencies import get_current_user, apply_company_scope, ensure_company_access, get_active_team_id
from app.models.core.user import User
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.sales.client import Client
from app.models.sales.account import Account
from app.routers.sales.accounts import get_account_or_404
from app.models.finance.invoice import Invoice
from app.models.sales.note import Note
from app.models.sales.task import Task
from app.services.sales.custom_fields import get_values_map, set_values
from app.services.sales.lead_access import ensure_can_work_lead
from app.models.sales.audit import AuditLog
from app.models.sales.lead import Lead
from app.schemas.sales import ClientCreate, ClientUpdate, ClientListResponse
from app.services.predictions.churn_service import churn_for_client
from app.schemas.admin import MessageResponse
from app.utils.helpers import normalize_email, normalize_phone
from app.services.finance.gst import normalize_gstin
from sqlalchemy import func as sa_func

router = APIRouter()


def _parse_gstin(value):
    try:
        return normalize_gstin(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _resolve_account_id(db, current_user, account_id):
    if account_id is None:
        return None
    return get_account_or_404(db, current_user, account_id).id


def _account_names(db, current_user, account_ids):
    ids = [i for i in set(account_ids) if i]
    if not ids:
        return {}
    rows = apply_company_scope(db.query(Account), Account, current_user).filter(Account.id.in_(ids)).all()
    return {row.id: row.name for row in rows}


# ===============================
# Clients Endpoints
# ===============================

@router.get("", response_model=ClientListResponse)
def list_clients(
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """List clients (paginated)."""
    query = apply_company_scope(db.query(Client), Client, current_user)
    
    # Sales: all clients they own (company-scoped). Do not filter by Client.team_id — converted
    # clients keep the lead's team_id, which can differ from X-Team-Id when switching teams or
    # with multi-team membership, which previously hid newly converted clients from this list.
    if getattr(current_user, 'role', '') == 'sales':
        query = query.filter(Client.assigned_to_id == current_user.id)
    elif getattr(current_user, 'role', '') == 'manager':
        # Managers are strictly scoped to their active team.
        if active_team_id is None:
            query = query.filter(False)
        else:
            query = query.filter(Client.team_id == active_team_id)
    
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (Client.name.ilike(search_pattern)) |
            (Client.email.ilike(search_pattern)) |
            (Client.company.ilike(search_pattern))
        )
    total = query.count()
    clients = query.order_by(Client.created_at.desc()).offset(skip).limit(limit).all()

    assignee_ids = list({c.assigned_to_id for c in clients if c.assigned_to_id})
    assignee_map = {}
    if assignee_ids:
        assignees = db.query(User).filter(User.id.in_(assignee_ids)).all()
        assignee_map = {u.id: u.full_name for u in assignees}

    account_map = _account_names(db, current_user, [c.account_id for c in clients])

    return {
        "items": [
            {
                "id": client.id,
                "name": client.name,
                "email": client.email,
                "phone": client.phone,
                "company": client.company,
                "address": client.address,
                "gstin": client.gstin,
                "account_id": client.account_id,
                "account_name": account_map.get(client.account_id),
                "assigned_to_id": client.assigned_to_id,
                "assigned_to_name": assignee_map.get(client.assigned_to_id, "Unassigned"),
                "created_at": client.created_at.strftime("%Y-%m-%d") if client.created_at else None
            }
            for client in clients
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{client_id}")
def get_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get client details by ID"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    ensure_company_access(client, current_user)
    
    # Role-based scoping
    if current_user.role == "sales" and client.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this client")
    if current_user.role == "manager":
        if active_team_id is None or client.team_id != active_team_id:
            raise HTTPException(status_code=403, detail="You do not have access to this team's client")
    
    # Get client invoices (company-scoped)
    inv_query = apply_company_scope(db.query(Invoice), Invoice, current_user)
    invoices = inv_query.filter(Invoice.client_id == client_id).all()
    
    # Get client notes (company-scoped)
    notes = apply_company_scope(db.query(Note), Note, current_user).filter(Note.client_id == client_id).order_by(Note.created_at.desc()).all()
    
    # Get client tasks (company-scoped)
    tasks = apply_company_scope(db.query(Task), Task, current_user).filter(Task.client_id == client_id).order_by(Task.due_date.asc()).all()
    
    # Look up assignee name
    assignee_name = "Unassigned"
    if client.assigned_to_id:
        assignee = db.query(User).filter(User.id == client.assigned_to_id).first()
        if assignee:
            assignee_name = assignee.full_name

    account_name = None
    if client.account_id:
        acc = apply_company_scope(db.query(Account), Account, current_user).filter(Account.id == client.account_id).first()
        if acc:
            account_name = acc.name

    return {
        "id": client.id,
        "name": client.name,
        "email": client.email,
        "phone": client.phone,
        "company": client.company,
        "address": client.address,
        "gstin": client.gstin,
        "account_id": client.account_id,
        "account_name": account_name,
        "assigned_to_id": client.assigned_to_id,
        "assigned_to_name": assignee_name,
        "created_at": client.created_at.strftime("%Y-%m-%d") if client.created_at else None,
        "custom_fields": get_values_map(db, current_user.company_id, "client", client.id),
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "total": inv.total,
                "status": inv.status,
                "issued_date": inv.issued_date.strftime("%Y-%m-%d") if inv.issued_date else None
            }
            for inv in invoices
        ],
        "notes": [
            {
                "id": note.id,
                "content": note.content,
                "author": note.author_name or "System",
                "date": note.created_at.strftime("%b %d, %Y") if note.created_at else None
            }
            for note in notes
        ],
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "due": task.due_date.strftime("%b %d") if task.due_date else "No date",
                "priority": task.priority.capitalize(),
                "status": task.status,
                "assigned_to": task.assigned_to_id
            }
            for task in tasks
        ]
    }


@router.get("/{client_id}/churn")
def get_client_churn(client_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == client_id).first()
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    ensure_company_access(client, current_user)
    return churn_for_client(db, current_user.company_id, client)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Create a new client"""
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must be assigned to a company")
    
    # Prevent duplicate conversion from same lead
    if body.converted_from_lead_id:
        source_lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(
            Lead.id == body.converted_from_lead_id
        ).first()
        if source_lead is None:
            raise HTTPException(status_code=400, detail="converted_from_lead_id not found in your company")
        ensure_can_work_lead(source_lead, current_user, active_team_id)
        existing = apply_company_scope(
            db.query(Client), Client, current_user
        ).filter(
            Client.converted_from_lead_id == body.converted_from_lead_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="This lead has already been converted to a client.")

    normalized_email = normalize_email(body.email)
    normalized_phone = normalize_phone(body.phone)

    # Prevent duplicate clients by email/phone. Use .query(Model.id) to avoid Enum deserialization crashes.
    if normalized_email:
        existing_by_email = apply_company_scope(
            db.query(Client.id), Client, current_user
        ).filter(sa_func.lower(Client.email) == normalized_email).first()
        if existing_by_email:
            raise HTTPException(
                status_code=400,
                detail=f"A client with email '{body.email}' already exists."
            )
        existing_lead_by_email = apply_company_scope(
            db.query(Lead.id), Lead, current_user
        ).filter(sa_func.lower(Lead.email) == normalized_email).first()
        if existing_lead_by_email:
            raise HTTPException(
                status_code=400,
                detail=f"A lead with email '{body.email}' already exists. Convert the lead instead of creating a new client."
            )

    if normalized_phone:
        existing_by_phone = apply_company_scope(
            db.query(Client.id), Client, current_user
        ).filter(Client.phone == normalized_phone).first()
        if existing_by_phone:
            raise HTTPException(
                status_code=400,
                detail="A client with this phone number already exists."
            )
        existing_lead_by_phone = apply_company_scope(
            db.query(Lead.id), Lead, current_user
        ).filter(Lead.phone == normalized_phone).first()
        if existing_lead_by_phone:
            raise HTTPException(
                status_code=400,
                detail="A lead with this phone number already exists. Convert the lead instead of creating a new client."
            )
            
    # Validate assigned_to_id
    final_assigned_to = current_user.id
    if body.assigned_to_id:
        assignee = apply_company_scope(
            db.query(User), User, current_user
        ).filter(User.id == body.assigned_to_id).first()
        if not assignee:
            raise HTTPException(status_code=400, detail="Assigned user not found in your company")
        if current_user.role == "manager":
            if active_team_id is None:
                raise HTTPException(status_code=400, detail="Active team required for manager actions")
            in_team = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id == active_team_id,
                TeamMembership.user_id == assignee.id,
            ).first()
            if not in_team:
                raise HTTPException(status_code=403, detail="Cannot assign client outside your team")
        final_assigned_to = body.assigned_to_id

    # Team selection rules (same as leads)
    requested_team_id = body.team_id
    final_team_id: Optional[int]

    if requested_team_id is not None:
        team = apply_company_scope(db.query(Team), Team, current_user).filter(Team.id == requested_team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")

        if current_user.role in ("sales", "manager"):
            member = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id == requested_team_id,
                TeamMembership.user_id == current_user.id,
            ).first()
            if not member:
                raise HTTPException(status_code=403, detail="You are not a member of the selected team")

        final_team_id = requested_team_id
    else:
        if current_user.role in ("sales", "manager"):
            if active_team_id is None:
                raise HTTPException(status_code=400, detail="Active team required. Select a team or provide team_id.")
            final_team_id = active_team_id
        else:
            final_team_id = active_team_id
    
    new_client = Client(
        company_id=current_user.company_id,
        name=body.name,
        email=normalized_email,
        phone=normalized_phone,
        company=body.company,
        address=body.address,
        gstin=_parse_gstin(body.gstin),
        assigned_to_id=final_assigned_to,
        team_id=final_team_id,
        converted_from_lead_id=body.converted_from_lead_id,
        account_id=_resolve_account_id(db, current_user, body.account_id),
    )
    
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    
    return {
        "id": new_client.id,
        "name": new_client.name,
        "message": "Client created successfully"
    }


@router.put("/{client_id}")
def update_client(
    client_id: int,
    body: ClientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Update client details"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    ensure_company_access(client, current_user)
    
    # Role-based scoping
    if current_user.role == "sales" and client.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You cannot edit a client you do not own")
    if current_user.role == "manager":
        if active_team_id is None or client.team_id != active_team_id:
            raise HTTPException(status_code=403, detail="You cannot edit a client outside your team")
    
    if body.name is not None:
        client.name = body.name
    if body.email is not None and body.email != client.email:
        normalized_email = normalize_email(body.email)
        if normalized_email:
            existing_by_email = apply_company_scope(
                db.query(Client.id), Client, current_user
            ).filter(
                sa_func.lower(Client.email) == normalized_email,
                Client.id != client.id,
            ).first()
            if existing_by_email:
                raise HTTPException(
                    status_code=400,
                    detail=f"A client with email '{body.email}' already exists."
                )
            existing_lead_by_email = apply_company_scope(
                db.query(Lead.id), Lead, current_user
            ).filter(sa_func.lower(Lead.email) == normalized_email).first()
            if existing_lead_by_email:
                raise HTTPException(
                    status_code=400,
                    detail=f"A lead with email '{body.email}' already exists. Leads and clients cannot share the same email."
                )
        client.email = normalized_email

    if body.phone is not None and body.phone != client.phone:
        normalized_phone = normalize_phone(body.phone)
        if normalized_phone:
            existing_by_phone = apply_company_scope(
                db.query(Client.id), Client, current_user
            ).filter(
                Client.phone == normalized_phone,
                Client.id != client.id,
            ).first()
            if existing_by_phone:
                raise HTTPException(
                    status_code=400,
                    detail="A client with this phone number already exists."
                )
            existing_lead_by_phone = apply_company_scope(
                db.query(Lead.id), Lead, current_user
            ).filter(Lead.phone == normalized_phone).first()
            if existing_lead_by_phone:
                raise HTTPException(
                    status_code=400,
                    detail="A lead with this phone number already exists. Leads and clients cannot share the same phone number."
                )
        client.phone = normalized_phone
    if body.company is not None:
        client.company = body.company
    if body.address is not None:
        client.address = body.address
    if body.gstin is not None:
        client.gstin = _parse_gstin(body.gstin)
    if "account_id" in body.model_fields_set:
        client.account_id = _resolve_account_id(db, current_user, body.account_id)
    if body.custom_fields is not None:
        set_values(db, current_user.company_id, "client", client.id, body.custom_fields)

    db.commit()
    db.refresh(client)
    
    return {"message": "Client updated successfully", "id": client.id}


@router.delete("/{client_id}", response_model=MessageResponse)
def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Delete a client"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    ensure_company_access(client, current_user)
    
    # Role-based delete permission
    if current_user.role == "sales" and client.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own clients")
    elif current_user.role == "manager":
        if active_team_id is None or client.team_id != active_team_id:
            raise HTTPException(status_code=403, detail="You can only delete clients in your team")
    
    db.delete(client)
    db.commit()
    
    return {"message": f"Client {client_id} deleted successfully"}


# ===============================
# Client Invoices
# ===============================

@router.get("/{client_id}/invoices")
def get_client_invoices(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get all invoices for a client"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    ensure_company_access(client, current_user)
    
    # Role-based scoping
    if current_user.role == "sales" and client.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have access to this client's invoices")
    if current_user.role == "manager":
        if active_team_id is None or client.team_id != active_team_id:
            raise HTTPException(status_code=403, detail="You do not have access to this team's client invoices")
    
    inv_query = apply_company_scope(db.query(Invoice), Invoice, current_user)
    invoices = inv_query.filter(Invoice.client_id == client_id).all()
    
    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "total": inv.total,
            "status": inv.status,
            "issued_date": inv.issued_date.strftime("%Y-%m-%d") if inv.issued_date else None,
            "due_date": inv.due_date.strftime("%Y-%m-%d") if inv.due_date else None,
            "paid_date": inv.paid_date.strftime("%Y-%m-%d") if inv.paid_date else None
        }
        for inv in invoices
    ]


@router.post("/{client_id}/notes", status_code=status.HTTP_201_CREATED)
def add_client_note(
    client_id: int,
    content: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Add a note to a client"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    ensure_company_access(client, current_user)
    
    # Role-based scoping
    if current_user.role == "sales" and client.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You cannot add notes to a client you do not own")
    if current_user.role == "manager":
        if active_team_id is None or client.team_id != active_team_id:
            raise HTTPException(status_code=403, detail="You cannot add notes to a client outside your team")
    
    new_note = Note(
        company_id=client.company_id,
        client_id=client_id,
        content=content,
        created_by_id=current_user.id,
    )
    db.add(new_note)
    
    # Audit log
    log = AuditLog(
        company_id=client.company_id,
        entity_type="client",
        entity_id=str(client.id),
        entity_name=client.name,
        action="note_added",
        admin_id=current_user.id,
        admin_name=current_user.full_name,
        after_value=content
    )
    db.add(log)
    
    db.commit()
    db.refresh(new_note)
    
    return {
        "id": new_note.id,
        "content": new_note.content,
        "author": current_user.full_name,
        "date": new_note.created_at.strftime("%b %d, %Y") if new_note.created_at else None
    }

