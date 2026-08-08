"""Request schema validation via pydantic v2."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError

SERVICES: tuple[str, ...] = (
    "gp-practice",
    "iv-therapy",
    "ozone-therapy",
    "red-light-therapy",
    "weight-loss",
    "yoga-breathwork",
    "gut-biome-test",
    "functional-blood-test",
)

SERVICE_TITLES: dict[str, str] = {
    "gp-practice": "GP Practice",
    "iv-therapy": "IV Therapy",
    "ozone-therapy": "Ozone Therapy",
    "red-light-therapy": "Red-Light Therapy",
    "weight-loss": "Weight Loss",
    "yoga-breathwork": "Yoga & Breathwork",
    "gut-biome-test": "Gut Biome Test",
    "functional-blood-test": "Functional Blood Tests",
}

_PHONE_RE = re.compile(r"^[+\d][\d\s\-()]{6,}$")
_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EmergencyContact(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)


class MedicalAid(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    provider: Optional[str] = None
    memberNumber: Optional[str] = None
    mainMember: Optional[str] = None
    dependentCode: Optional[str] = None


class Personal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    dob: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    idOrPassport: Optional[str] = None
    phone: str
    email: EmailStr
    emergencyContact: Optional[EmergencyContact] = None
    medicalAid: Optional[MedicalAid] = None


class Medical(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    existingConditions: Optional[str] = ""
    allergies: Optional[str] = ""
    currentMeds: Optional[str] = ""
    reasonForVisit: Optional[str] = ""
    notes: Optional[str] = ""
    marketingOptIn: bool = False


class BookingPricing(BaseModel):
    """Client selection only — prices are resolved server-side from the catalog."""
    model_config = ConfigDict(str_strip_whitespace=True)
    itemId: str = Field(min_length=1)
    extras: list[str] = Field(default_factory=list)


class BookingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    service: str
    requestedSlot: str
    personal: Personal
    medical: Optional[Medical] = Field(default_factory=Medical)
    pricing: Optional[BookingPricing] = None
    consent: bool
    submittedAt: Optional[str] = None


class ContactRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1)
    email: EmailStr
    phone: Optional[str] = ""
    topic: Optional[str] = ""
    message: str = Field(min_length=1)
    ts: Optional[str] = None


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: EmailStr
    password: str = Field(min_length=1)


class PatientRegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    dob: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    phone: str = Field(min_length=7)
    email: EmailStr
    password: str = Field(min_length=8)
    medicalAid: Optional[MedicalAid] = None


class PatientLoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: EmailStr
    password: str = Field(min_length=1)


class PatientProfileUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    firstName: Optional[str] = Field(default=None, min_length=1)
    lastName: Optional[str] = Field(default=None, min_length=1)
    dob: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    phone: Optional[str] = Field(default=None, min_length=7)
    medicalAid: Optional[MedicalAid] = None


class PricingExtra(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: int = Field(ge=0)


class PricingItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: Optional[int] = Field(default=None, ge=0)
    priceNote: Optional[str] = None
    description: Optional[str] = None
    extras: list[PricingExtra] = Field(default_factory=list)


class PricingScheduleEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    day: str = Field(min_length=1)
    time: str = Field(min_length=1)


class PricingCategory(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    schedule: list[PricingScheduleEntry] = Field(default_factory=list)
    items: list[PricingItem] = Field(default_factory=list)


class PricingCatalogPut(BaseModel):
    categories: list[PricingCategory] = Field(min_length=1)


class AdminBookingPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    status: Optional[Literal["pending", "confirmed", "completed", "noshow", "cancelled"]] = None
    newSlot: Optional[str] = None
    notifyPatient: Optional[bool] = True
    cancelReason: Optional[str] = None


def collect_error_fields(err: ValidationError) -> list[str]:
    """Pydantic error tree → dotted-field paths, e.g. ['personal.email']."""
    out: list[str] = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e.get("loc", ()))
        if loc:
            out.append(loc)
    return out


def is_valid_phone(value: str) -> bool:
    return bool(_PHONE_RE.match(value or ""))
