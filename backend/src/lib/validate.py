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
)

SERVICE_TITLES: dict[str, str] = {
    "gp-practice": "GP Practice",
    "iv-therapy": "IV Therapy",
    "ozone-therapy": "Ozone Therapy",
    "red-light-therapy": "Red-Light Therapy",
    "weight-loss": "Weight Loss",
    "yoga-breathwork": "Yoga & Breathwork",
}

_PHONE_RE = re.compile(r"^[+\d][\d\s\-()]{6,}$")


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
    idOrPassport: str = Field(min_length=4)
    phone: str
    email: EmailStr
    emergencyContact: EmergencyContact
    medicalAid: Optional[MedicalAid] = None


class Medical(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    existingConditions: Optional[str] = ""
    allergies: Optional[str] = ""
    currentMeds: Optional[str] = ""
    reasonForVisit: Optional[str] = ""
    notes: Optional[str] = ""
    marketingOptIn: bool = False


class BookingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    service: str
    requestedSlot: str
    personal: Personal
    medical: Medical
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
