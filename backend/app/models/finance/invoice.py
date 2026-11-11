from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Numeric, Date, UniqueConstraint, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.core.enums import InvoiceStatus


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("company_id", "invoice_number", name="uq_invoices_company_invoice_number"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=False)
    
    # Client
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    
    # Amounts (Numeric to avoid float rounding errors)
    subtotal = Column(Numeric(12, 2), default=0)
    tax = Column(Numeric(12, 2), default=0)
    discount = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    cgst = Column(Numeric(12, 2), default=0)
    sgst = Column(Numeric(12, 2), default=0)
    igst = Column(Numeric(12, 2), default=0)
    seller_gstin = Column(String(15), nullable=True)
    buyer_gstin = Column(String(15), nullable=True)
    place_of_supply = Column(String(2), nullable=True)
    tax_mode = Column(String(10), nullable=True)
    irn = Column(String(64), nullable=True)
    ack_no = Column(String(32), nullable=True)
    ack_date = Column(DateTime, nullable=True)
    signed_qr = Column(Text, nullable=True)

    status = Column(Enum(InvoiceStatus, values_callable=lambda x: [e.value for e in x], native_enum=False, length=50), default=InvoiceStatus.DRAFT)
    
    # Dates
    issued_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)
    paid_date = Column(Date, nullable=True)
    
    # Payment info
    payment_method = Column(String(50), nullable=True)
    payment_reference = Column(String(100), nullable=True)
    payment_url = Column(String(500), nullable=True)
    
    notes = Column(Text, nullable=True)
    
    # Created by
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    share_token_hash = Column(String(64), nullable=True, unique=True, index=True)
    share_created_at = Column(DateTime, nullable=True)
    
    # Relationships
    company = relationship("Company", backref="invoices")
    client = relationship("Client", back_populates="invoices")
    created_by = relationship("User", foreign_keys=[created_by_id], back_populates="invoices_created")
    approved_by = relationship("User", foreign_keys=[approved_by_id], back_populates="invoices_approved")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    hsn = Column(String(20), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    tax_rate = Column(Numeric(5, 2), nullable=True)
    tax = Column(Numeric(12, 2), default=0)
    
    company = relationship("Company")
    invoice = relationship("Invoice", back_populates="items")
