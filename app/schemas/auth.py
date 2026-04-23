from pydantic import BaseModel, EmailStr


class SetupStatusResponse(BaseModel):
    needs_setup: bool


class SetupRequest(BaseModel):
    email: EmailStr
    password: str
    password_confirm: str


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class MeResponse(BaseModel):
    id: str | None
    email: str | None
    is_active: bool
    is_guest: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    password_confirm: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class MessageResponse(BaseModel):
    message: str


class GuestAccessStatusResponse(BaseModel):
    enabled: bool


class GuestAccessResponse(BaseModel):
    enabled: bool
    url: str
