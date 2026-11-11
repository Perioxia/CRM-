from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Numeric, Enum, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.core.enums import QuoteStatus


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    quote_number = Column(String(50), nullable=False)
    title = Column(String(255), nullable=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    status = Column(
        Enum(QuoteStatus, values_callable=lambda x: [e.value for e in x], native_enum=False),
        default=QuoteStatus.DRAFT,
    )
    subtotal = Column(Numeric(12, 2), default=0)
    tax = Column(Numeric(12, 2), default=0)
    cgst = Column(Numeric(12, 2), default=0)
    sgst = Column(Numeric(12, 2), default=0)
    igst = Column(Numeric(12, 2), default=0)
    seller_gstin = Column(String(15), nullable=True)
    buyer_gstin = Column(String(15), nullable=True)
    place_of_supply = Column(String(2), nullable=True)
    tax_mode = Column(String(10), nullable=True)
    total = Column(Numeric(12, 2), default=0)
    notes = Column(Text, nullable=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    sales_order_id = Column(Integer, nullable=True, index=True)
    approval_status = Column(String(20), nullable=True, index=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    share_token_hash = Column(String(64), nullable=True, unique=True, index=True)
    share_created_at = Column(DateTime, nullable=True)

    plan_id = Column(Integer, ForeignKey("quote_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    plan_name = Column(String(200), nullable=True)
    project = Column(String(255), nullable=True)
    website = Column(String(500), nullable=True)
    validity_days = Column(Integer, nullable=True)
    currency = Column(String(10), nullable=True, default="INR")
    billing_interval = Column(String(20), nullable=True)
    executive_summary = Column(Text, nullable=True)
    scope_text = Column(Text, nullable=True)
    fee_inclusive = Column(Integer, nullable=True, default=1)

    items = relationship("QuoteItem", back_populates="quote", cascade="all, delete-orphan")


class QuoteItem(Base):
    __tablename__ = "quote_items"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    hsn = Column(String(20), nullable=True)
    tax_rate = Column(Numeric(5, 2), nullable=True)
    tax = Column(Numeric(12, 2), default=0)

    quote = relationship("Quote", back_populates="items")
