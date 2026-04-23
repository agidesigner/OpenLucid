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
    # Present when guest mode is on AND the raw token is stored (rows
    # from pre-v0.9.9.8 installs have token_hash only — ``url`` is then
    # None and the UI prompts the owner to regenerate to reveal a URL).
    url: str | None = None


class GuestAccessResponse(BaseModel):
    enabled: bool
    url: str
