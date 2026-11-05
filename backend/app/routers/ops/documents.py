import os
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.ops.document import Document
from app.services.sales.lead_access import ensure_can_work_lead
from app.utils.dependencies import get_current_user, apply_company_scope, ensure_company_access, get_active_team_id
from app.utils.audit import log_activity
from pydantic import BaseModel
from datetime import datetime, timezone

router = APIRouter(tags=["documents"])

UPLOAD_DIR = "uploads/documents"
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".txt",
    ".csv",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}

# Ensure upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)


class DocumentResponse(BaseModel):
    id: int
    filename: str
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    uploaded_by_id: Optional[int] = None
    uploaded_by_name: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True


@router.post("/upload", response_model=DocumentResponse)
def upload_document(
    file: UploadFile = File(...),
    lead_id: Optional[int] = Form(None),
    client_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: int | None = Depends(get_active_team_id),
):
    """Upload a document and attach it to a Lead or Client."""
    if not lead_id and not client_id:
        raise HTTPException(status_code=400, detail="Must provide either lead_id or client_id")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    # Verify access to the parent entity
    entity = None
    entity_name = ""
    entity_type = ""
    if lead_id:
        entity = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == lead_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="Lead not found or access denied")
        
        ensure_can_work_lead(entity, current_user, active_team_id, "You cannot upload documents to this lead")
            
        entity_name = entity.name
        entity_type = "lead"
    elif client_id:
        entity = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == client_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="Client not found or access denied")
            
        # Scoping: Sales must own the client
        if current_user.role == "sales" and entity.assigned_to_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only upload documents to your own clients")
        # Scoping: Manager must manage the client's team
        if current_user.role == "manager" and (active_team_id is None or entity.team_id != active_team_id):
            raise HTTPException(status_code=403, detail="You can only upload documents to your team's clients")
            
        entity_name = entity.name
        entity_type = "client"

    # Validate file type and size before writing to disk.
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed extensions: {allowed}")

    file_bytes = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if len(file_bytes) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB.")

    from app.services.billing.limits import assert_can_upload
    assert_can_upload(db, current_user.company_id, len(file_bytes))

    # Save physical file
    stored_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, stored_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(file_bytes)

    # Save to DB
    new_doc = Document(
        filename=file.filename,
        stored_filename=stored_filename,
        file_path=file_path,
        lead_id=lead_id,
        client_id=client_id,
        company_id=current_user.company_id,
        uploaded_by_id=current_user.id,
        file_size=len(file_bytes),
    )
    
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)

    log_activity(db, user=current_user, action='uploaded_document', entity_type=entity_type, 
                 entity_id=getattr(entity, 'id', 0), entity_name=entity_name, after=file.filename)
    db.commit()

    return {
        "id": new_doc.id,
        "filename": new_doc.filename,
        "lead_id": new_doc.lead_id,
        "client_id": new_doc.client_id,
        "uploaded_by_id": new_doc.uploaded_by_id,
        "uploaded_by_name": current_user.full_name,
        "created_at": new_doc.created_at.isoformat() if new_doc.created_at else datetime.now(timezone.utc).isoformat()
    }


@router.get("/download/{document_id}")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: int | None = Depends(get_active_team_id),
):
    """Download a file."""
    doc = apply_company_scope(db.query(Document), Document, current_user).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Scoping checks on parent entity
    if doc.lead_id:
        parent = db.query(Lead).filter(Lead.id == doc.lead_id).first()
        if parent:
            ensure_can_work_lead(parent, current_user, active_team_id, "Access denied to this document")
    elif doc.client_id:
        parent = db.query(Client).filter(Client.id == doc.client_id).first()
        if parent:
            if current_user.role == "sales" and parent.assigned_to_id != current_user.id:
                raise HTTPException(status_code=403, detail="Access denied to this document")
            if current_user.role == "manager" and (active_team_id is None or parent.team_id != active_team_id):
                raise HTTPException(status_code=403, detail="Access denied to another team's document")

    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File physical path not found")

    return FileResponse(doc.file_path, filename=doc.filename)


@router.get("/{entity_type}/{entity_id}", response_model=List[DocumentResponse])
def get_documents(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: int | None = Depends(get_active_team_id),
):
    """Get all documents for a Lead or Client."""
    if entity_type not in ["lead", "client"]:
        raise HTTPException(status_code=400, detail="Invalid entity type. Use 'lead' or 'client'")

    query = apply_company_scope(db.query(Document), Document, current_user)

    if entity_type == "lead":
        # First check lead access
        lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == entity_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        ensure_can_work_lead(lead, current_user, active_team_id, "Access denied to these documents")

        query = query.filter(Document.lead_id == entity_id)
    else:
        # Check client access
        client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == entity_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Scoping checks
        if current_user.role == "sales" and client.assigned_to_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied to these documents")
        if current_user.role == "manager" and (active_team_id is None or client.team_id != active_team_id):
            raise HTTPException(status_code=403, detail="Access denied to another team's documents")

        query = query.filter(Document.client_id == entity_id)

    docs = query.order_by(Document.created_at.desc()).all()

    result = []
    for doc in docs:
        result.append({
            "id": doc.id,
            "filename": doc.filename,
            "lead_id": doc.lead_id,
            "client_id": doc.client_id,
            "uploaded_by_id": doc.uploaded_by_id,
            "uploaded_by_name": doc.uploaded_by.full_name if doc.uploaded_by else "Unknown",
            "created_at": doc.created_at.isoformat() if doc.created_at else ""
        })
    return result


@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: int | None = Depends(get_active_team_id),
):
    """Delete a document."""
    doc = apply_company_scope(db.query(Document), Document, current_user).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if current_user.role == "purchase" and doc.lead_id:
        raise HTTPException(status_code=403, detail="You cannot delete this document")
    # Sales can only delete their own uploads.
    if current_user.role == "sales" and doc.uploaded_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own documents")
    # Managers are team-scoped for delete actions.
    if current_user.role == "manager":
        if active_team_id is None:
            raise HTTPException(status_code=403, detail="Active team required")
        if doc.lead_id:
            lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == doc.lead_id).first()
            if not lead or lead.team_id != active_team_id:
                raise HTTPException(status_code=403, detail="You can only delete documents from your team's leads")
        elif doc.client_id:
            client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == doc.client_id).first()
            if not client or client.team_id != active_team_id:
                raise HTTPException(status_code=403, detail="You can only delete documents from your team's clients")

    # Remove file from disk
    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    entity_id = doc.lead_id or doc.client_id
    entity_type = "lead" if doc.lead_id else "client"
    
    db.delete(doc)
    
    log_activity(db, user=current_user, action='deleted_document', entity_type=entity_type, 
                 entity_id=entity_id, entity_name=str(entity_id), before=doc.filename)
    db.commit()

    return {"message": "Document deleted successfully"}
